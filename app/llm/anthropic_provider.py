"""Adapter da Anthropic (Messages API)."""

from collections.abc import Sequence
from typing import Any

import anthropic
import httpx2

from app.llm.base import (
    MAX_OUTPUT_TOKENS,
    MAX_RETRIES,
    REQUEST_TIMEOUT_SECONDS,
    AssistantMessage,
    LLMAuthError,
    LLMBadRequestError,
    LLMProviderError,
    LLMRateLimitError,
    Message,
    ToolCall,
    ToolResultMessage,
)


def _content_block(block: Any) -> dict[str, Any]:
    if block.type == "text":
        return {"type": "text", "text": block.text}
    if block.type == "tool_use":
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
    return block.model_dump(exclude_none=True)  # ex.: thinking, que precisa voltar intacto


def _to_api_messages(messages: Sequence[Message]) -> list[dict[str, Any]]:
    api_messages: list[dict[str, Any]] = []
    for message in messages:
        if isinstance(message, AssistantMessage):
            content = message.provider_state or message.text
            api_messages.append({"role": "assistant", "content": content})
        elif isinstance(message, ToolResultMessage):
            block = {
                "type": "tool_result",
                "tool_use_id": message.call_id,
                "content": message.as_json(),
            }
            # Resultados de chamadas paralelas precisam ir na mesma mensagem.
            previous = api_messages[-1] if api_messages else None
            if previous and previous["role"] == "user" and isinstance(previous["content"], list):
                previous["content"].append(block)
            else:
                api_messages.append({"role": "user", "content": [block]})
        else:
            api_messages.append({"role": "user", "content": message.text})
    return api_messages


class AnthropicProvider:
    name = "anthropic"

    def __init__(
        self,
        api_key: str,
        model: str,
        http_client: httpx2.Client | None = None,
        max_retries: int = MAX_RETRIES,
    ) -> None:
        self.model = model
        self._client = anthropic.Anthropic(
            api_key=api_key,
            timeout=REQUEST_TIMEOUT_SECONDS,
            max_retries=max_retries,
            http_client=http_client,
        )

    def complete(
        self, system: str, messages: Sequence[Message], tools: Sequence[dict[str, Any]]
    ) -> AssistantMessage:
        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=MAX_OUTPUT_TOKENS,
                system=system,
                messages=_to_api_messages(messages),
                tools=[
                    {
                        "name": tool["name"],
                        "description": tool["description"],
                        "input_schema": tool["parameters"],
                    }
                    for tool in tools
                ],
            )
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as error:
            raise LLMAuthError("Chave da Anthropic inválida ou sem permissão") from error
        except anthropic.RateLimitError as error:
            raise LLMRateLimitError("Limite de uso da Anthropic atingido") from error
        except (anthropic.BadRequestError, anthropic.NotFoundError) as error:
            raise LLMBadRequestError(f"Anthropic recusou a requisição: {error.message}") from error
        except anthropic.APIError as error:
            raise LLMProviderError(f"Falha na API da Anthropic: {type(error).__name__}") from error

        return AssistantMessage(
            text="".join(b.text for b in response.content if b.type == "text"),
            tool_calls=tuple(
                ToolCall(id=b.id, name=b.name, arguments=b.input)
                for b in response.content
                if b.type == "tool_use"
            ),
            provider=self.name,
            provider_state=[_content_block(b) for b in response.content],
        )
