"""Interface comum dos provedores de LLM e formato neutro das mensagens."""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

MAX_OUTPUT_TOKENS = 2048
REQUEST_TIMEOUT_SECONDS = 60.0
MAX_RETRIES = 2


class LLMError(Exception):
    """Falha ao chamar o provedor. As mensagens nunca incluem a chave de API."""


class LLMAuthError(LLMError):
    """Chave de API inválida ou sem permissão."""


class LLMRateLimitError(LLMError):
    """Limite de uso ou de cota do provedor atingido."""


class LLMProviderError(LLMError):
    """Erro do provedor ou de rede."""


class LLMBadRequestError(LLMError):
    """Requisição recusada pelo provedor, como um modelo inexistente."""


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] | None  # None: o modelo gerou argumentos que não são JSON válido


@dataclass(frozen=True)
class UserMessage:
    text: str


@dataclass(frozen=True)
class AssistantMessage:
    text: str
    tool_calls: tuple[ToolCall, ...] = ()
    provider: str | None = None
    # Resposta bruta, devolvida sem alterações ao mesmo provedor. Alguns exigem isso para manter
    # o raciocínio entre chamadas de tools (assinaturas do Gemini, itens de reasoning da OpenAI).
    provider_state: Any = None


@dataclass(frozen=True)
class ToolResultMessage:
    call_id: str
    name: str
    result: dict[str, Any]

    def as_json(self) -> str:
        return json.dumps(self.result, ensure_ascii=False)


Message = UserMessage | AssistantMessage | ToolResultMessage


class LLMProvider(Protocol):
    name: str
    model: str

    def complete(
        self, system: str, messages: Sequence[Message], tools: Sequence[dict[str, Any]]
    ) -> AssistantMessage: ...


def parse_tool_arguments(raw: str | None) -> dict[str, Any] | None:
    """Interpreta os argumentos em JSON. Sem argumentos vira {}; JSON inválido vira None."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def prepare_history(messages: Sequence[Message], provider_name: str) -> list[Message]:
    """Reduz ao texto os turnos gerados por outro provedor, pois são incompatíveis entre si.

    O BYOK é por requisição, então o provedor pode mudar no meio de uma conversa. Chamadas de
    tools alheias são descartadas; o agente consulta de novo o que precisar.
    """
    prepared: list[Message] = []
    foreign_calls: set[str] = set()
    for message in messages:
        if isinstance(message, AssistantMessage) and message.provider != provider_name:
            foreign_calls.update(call.id for call in message.tool_calls)
            if message.text:
                prepared.append(AssistantMessage(text=message.text))
        elif isinstance(message, ToolResultMessage) and message.call_id in foreign_calls:
            continue
        else:
            prepared.append(message)
    return prepared
