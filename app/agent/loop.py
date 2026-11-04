"""Loop do agente: chama o modelo e executa as tools até haver uma resposta final."""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.agent.prompts import build_system_prompt
from app.db import repository as repo
from app.llm.base import (
    AssistantMessage,
    LLMProvider,
    Message,
    ToolCall,
    ToolResultMessage,
    UserMessage,
    prepare_history,
)
from app.tools.context import ToolContext
from app.tools.registry import execute_tool, tool_schemas

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 8

FALLBACK_REPLY = (
    "Desculpa, não consegui concluir sua solicitação agora. Você pode falar com a nossa equipe "
    "pelo telefone ou WhatsApp (67) 3341-4444."
)


@dataclass(frozen=True)
class ToolTrace:
    name: str
    arguments: dict[str, Any] | None
    result: dict[str, Any]


@dataclass(frozen=True)
class AgentReply:
    text: str
    # Mensagens novas deste turno, para o chamador anexar ao histórico só se tudo der certo.
    messages: tuple[Message, ...]
    tool_trace: tuple[ToolTrace, ...]


def _run_tool(call: ToolCall, ctx: ToolContext) -> dict[str, Any]:
    if call.arguments is None:
        return {"error": "Os argumentos não são um JSON válido. Tente de novo."}
    return execute_tool(call.name, call.arguments, ctx)


def run_agent(
    provider: LLMProvider, history: Sequence[Message], user_text: str, ctx: ToolContext
) -> AgentReply:
    """Processa uma mensagem do cliente. Erros do provedor (LLMError) sobem para o chamador."""
    categories = [c.name for c in repo.list_categories(ctx.catalog) if c.product_count > 0]
    system = build_system_prompt(ctx.now, categories)
    tools = tool_schemas()
    base = prepare_history(history, provider.name)
    new_messages: list[Message] = [UserMessage(user_text)]
    trace: list[ToolTrace] = []

    for _ in range(MAX_ITERATIONS):
        assistant = provider.complete(system, [*base, *new_messages], tools)
        new_messages.append(assistant)

        if not assistant.tool_calls:
            if assistant.text.strip():
                return AgentReply(assistant.text.strip(), tuple(new_messages), tuple(trace))
            logger.warning("Resposta vazia do provedor %s", provider.name)
            break

        for call in assistant.tool_calls:
            result = _run_tool(call, ctx)
            trace.append(ToolTrace(call.name, call.arguments, result))
            new_messages.append(ToolResultMessage(call.id, call.name, result))
    else:
        logger.warning("Limite de %d iterações atingido", MAX_ITERATIONS)

    # Mantém no histórico só a pergunta e o aviso: um turno incompleto de tools seria inválido.
    fallback = AssistantMessage(text=FALLBACK_REPLY)
    return AgentReply(FALLBACK_REPLY, (UserMessage(user_text), fallback), tuple(trace))
