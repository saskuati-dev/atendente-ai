"""Catálogo de tools expostas ao agente: esquemas para o LLM e execução com validação."""

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.tools import catalog, orders, policies
from app.tools.context import ToolContext

logger = logging.getLogger(__name__)


class RegisterComplaintArgs(BaseModel):
    customer_name: str = Field(min_length=1, description="Nome informado pelo cliente.")
    contact: str = Field(min_length=1, description="Telefone ou e-mail para o retorno.")
    summary: str = Field(min_length=1, description="Resumo objetivo da reclamação.")
    order_id: int | None = Field(default=None, ge=1, description="Número do pedido, se houver.")


def register_complaint(ctx: ToolContext, args: RegisterComplaintArgs) -> dict[str, Any]:
    protocol = ctx.complaints.add(
        args.customer_name, args.contact, args.summary, args.order_id, ctx.now
    )
    return {"registered": True, "protocol": protocol, "response_deadline": "24 horas úteis"}


def _without_titles(schema: Any) -> Any:
    """Remove os "title" gerados pelo Pydantic: repetem os nomes e só gastam tokens."""
    if isinstance(schema, dict):
        return {k: _without_titles(v) for k, v in schema.items() if k != "title"}
    if isinstance(schema, list):
        return [_without_titles(item) for item in schema]
    return schema


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args_model: type[BaseModel]
    handler: Callable[[ToolContext, Any], dict[str, Any]]

    def json_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": _without_titles(self.args_model.model_json_schema()),
        }


TOOLS: dict[str, Tool] = {
    tool.name: tool
    for tool in (
        Tool(
            "search_products",
            "Busca instrumentos no catálogo por palavras-chave, categoria e faixa de preço. Use "
            "para qualquer pergunta sobre o que a loja tem, modelos e sugestões.",
            catalog.SearchProductsArgs,
            catalog.search_products,
        ),
        Tool(
            "get_product",
            "Retorna preço vigente, promoção, disponibilidade, descrição e alternativas de um "
            "produto. Use antes de afirmar preço, estoque ou características.",
            catalog.GetProductArgs,
            catalog.get_product,
        ),
        Tool(
            "list_active_promotions",
            "Lista as promoções vigentes agora. Use em qualquer pergunta sobre promoções, "
            "descontos ou campanhas como Black Friday.",
            catalog.ListActivePromotionsArgs,
            catalog.list_active_promotions,
        ),
        Tool(
            "get_payment_options",
            "Calcula PIX, débito, boleto e parcelas válidas para um produto ou valor. Use "
            "sempre que o cliente perguntar quanto fica ou em quantas vezes pode pagar.",
            catalog.GetPaymentOptionsArgs,
            catalog.get_payment_options,
        ),
        Tool(
            "get_order",
            "Consulta status, itens e rastreamento de pedidos. Exige o número do pedido com "
            "telefone ou e-mail, ou só o telefone para listar os pedidos do cliente. Use apenas "
            "dados informados pelo cliente nesta conversa.",
            orders.GetOrderArgs,
            orders.get_order,
        ),
        Tool(
            "get_policy",
            "Consulta as regras da loja: horário, contatos, formas de pagamento, trocas, "
            "devoluções, frete, garantia e privacidade. Use antes de responder sobre esses temas.",
            policies.GetPolicyArgs,
            policies.get_policy,
        ),
        Tool(
            "register_complaint",
            "Registra uma reclamação e a encaminha ao responsável. Use depois de ouvir o cliente "
            "e obter nome, contato e resumo do problema.",
            RegisterComplaintArgs,
            register_complaint,
        ),
    )
}


def tool_schemas() -> list[dict[str, Any]]:
    return [tool.json_schema() for tool in TOOLS.values()]


def execute_tool(name: str, raw_args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """Executa a tool. Erros voltam como dados, para o agente reagir sem inventar respostas."""
    tool = TOOLS.get(name)
    if tool is None:
        return {"error": f"Tool desconhecida: {name}"}
    try:
        args = tool.args_model.model_validate(raw_args)
    except ValidationError as error:
        details = "; ".join(e["msg"] for e in error.errors())
        return {"error": f"Argumentos inválidos: {details}"}
    try:
        return tool.handler(ctx, args)
    except sqlite3.Error:
        logger.exception("Falha de banco na tool %s", name)
        return {
            "error": "Sistema indisponível. Diga ao cliente que não foi possível consultar agora "
            "e não informe dados de memória."
        }
