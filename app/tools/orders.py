"""Tool de pedidos, com verificação de identidade feita no código, não pelo LLM."""

from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from app.db import repository as repo
from app.db.text import normalize_phone
from app.tools.context import ToolContext
from app.tools.pricing import format_brl

STATUS_LABELS = {
    "pending": "Aguardando confirmação",
    "confirmed": "Confirmado",
    "shipped": "Enviado",
    "delivered": "Entregue",
    "cancelled": "Cancelado",
}

MSG_VERIFICATION_REQUIRED = (
    "Por segurança, peça o número do pedido e o telefone ou e-mail do cadastro antes de "
    "informar dados do pedido."
)
MSG_EMAIL_ONLY = (
    "E-mail sozinho não identifica o cliente. Peça o número do pedido ou o telefone do cadastro."
)
# A mesma mensagem para pedido inexistente e contato errado: não revela quais pedidos existem.
MSG_NOT_VERIFIED = (
    "Não foi possível localizar um pedido com esses dados. Peça para o cliente conferir as "
    "informações. Não dê nenhum detalhe sobre o motivo."
)
MSG_OVERDUE = (
    "A previsão de entrega já passou. Reconheça o atraso, peça desculpas, informe o código de "
    "rastreamento e ofereça registrar o caso com register_complaint. Não prometa nova data."
)


def _payment_label(method: str) -> str:
    if method.startswith("credit_"):
        return f"Cartão de crédito em {method.removeprefix('credit_')}"
    return {"pix": "PIX", "debit": "Cartão de débito", "boleto": "Boleto"}.get(method, method)


def _order_view(ctx: ToolContext, order: repo.Order, today: date) -> dict[str, Any]:
    """Dados do pedido. O total é o registrado, sem recalcular pelo catálogo atual."""
    data: dict[str, Any] = {
        "order_id": order.order_id,
        "placed_on": order.order_date,  # data da compra, não do envio
        "status": STATUS_LABELS[order.status],
        "total": format_brl(order.total_cents),
        "payment": _payment_label(order.payment_method),
        "items": [
            {"name": item.product_name, "quantity": item.quantity}
            for item in repo.get_order_items(ctx.catalog, order.order_id)
        ],
    }
    if order.tracking_code:
        data["tracking_code"] = order.tracking_code
        data["tracking_note"] = (
            "A consulta é feita no site dos Correios ou da Jadlog. O sistema não informa qual "
            "transportadora fez o envio, nem a data de envio."
        )
    if order.estimated_delivery:
        data["estimated_delivery"] = order.estimated_delivery
    if order.notes:
        data["notes"] = order.notes
    if (
        order.status == "shipped"
        and order.estimated_delivery
        and date.fromisoformat(order.estimated_delivery) < today
    ):
        data["delivery_overdue"] = True
        data["guidance"] = MSG_OVERDUE
    return data


def _contact_matches(customer: repo.Customer, phone: str | None, email: str | None) -> bool:
    if phone and normalize_phone(phone) == normalize_phone(customer.phone):
        return True
    return bool(email and email.strip().casefold() == customer.email)


class GetOrderArgs(BaseModel):
    order_id: int | None = Field(default=None, ge=1, description="Número do pedido.")
    phone: str | None = Field(
        default=None, description="Telefone do cadastro, informado pelo cliente."
    )
    email: str | None = Field(
        default=None, description="E-mail do cadastro, informado pelo cliente."
    )


def get_order(ctx: ToolContext, args: GetOrderArgs) -> dict[str, Any]:
    if not args.phone and not args.email:
        return {"verified": False, "message": MSG_VERIFICATION_REQUIRED}

    if args.order_id is None:
        if not args.phone:
            return {"verified": False, "message": MSG_EMAIL_ONLY}
        customer = repo.find_customer_by_phone(ctx.catalog, args.phone)
        if customer is None:
            return {"verified": False, "message": MSG_NOT_VERIFIED}
        orders = repo.list_orders_by_customer(ctx.catalog, customer.customer_id)
    else:
        order = repo.get_order(ctx.catalog, args.order_id)
        customer = repo.get_customer(ctx.catalog, order.customer_id) if order else None
        if customer is None or not _contact_matches(customer, args.phone, args.email):
            return {"verified": False, "message": MSG_NOT_VERIFIED}
        orders = [order]

    return {
        "verified": True,
        "customer_first_name": customer.name.split()[0],
        "orders": [_order_view(ctx, o, ctx.today) for o in orders],
    }
