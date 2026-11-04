"""Adapter da OpenAI (Responses API).

Usa Responses, e não Chat Completions, porque nos modelos GPT-6 o tool calling em Chat Completions
é limitado ou indisponível. Sem armazenamento no servidor: o histórico fica na aplicação, e os itens
de raciocínio voltam criptografados.
"""

from collections.abc import Sequence
from typing import Any

import httpx
import openai

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
    parse_tool_arguments,
)


def _to_api_input(messages: Sequence[Message]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for message in messages:
        if isinstance(message, AssistantMessage):
            if message.provider_state is not None:
                items.extend(message.provider_state)
            else:
                items.append({"role": "assistant", "content": message.text})
        elif isinstance(message, ToolResultMessage):
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": message.call_id,
                    "output": message.as_json(),
                }
            )
        else:
            items.append({"role": "user", "content": message.text})
    return items


class OpenAIProvider:
    name = "openai"

    def __init__(
        self,
        api_key: str,
        model: str,
        http_client: httpx.Client | None = None,
        max_retries: int = MAX_RETRIES,
    ) -> None:
        self.model = model
        self._client = openai.OpenAI(
            api_key=api_key,
            timeout=REQUEST_TIMEOUT_SECONDS,
            max_retries=max_retries,
            http_client=http_client,
        )

    def complete(
        self, system: str, messages: Sequence[Message], tools: Sequence[dict[str, Any]]
    ) -> AssistantMessage:
        try:
            response = self._client.responses.create(
                model=self.model,
                instructions=system,
                input=_to_api_input(messages),
                tools=[
                    {
                        "type": "function",
                        "name": tool["name"],
                        "description": tool["description"],
                        "parameters": tool["parameters"],
                        "strict": False,  # os esquemas têm campos opcionais
                    }
                    for tool in tools
                ],
                max_output_tokens=MAX_OUTPUT_TOKENS,
                store=False,
                include=["reasoning.encrypted_content"],
            )
        except (openai.AuthenticationError, openai.PermissionDeniedError) as error:
            raise LLMAuthError("Chave da OpenAI inválida ou sem permissão") from error
        except openai.RateLimitError as error:
            raise LLMRateLimitError("Limite de uso da OpenAI atingido") from error
        except (openai.BadRequestError, openai.NotFoundError) as error:
            raise LLMBadRequestError(f"OpenAI recusou a requisição: {error.message}") from error
        except openai.APIError as error:
            raise LLMProviderError(f"Falha na API da OpenAI: {type(error).__name__}") from error

        return AssistantMessage(
            text=response.output_text,
            tool_calls=tuple(
                ToolCall(
                    id=item.call_id,
                    name=item.name,
                    arguments=parse_tool_arguments(item.arguments),
                )
                for item in response.output
                if item.type == "function_call"
            ),
            provider=self.name,
            provider_state=[item.model_dump(exclude_none=True) for item in response.output],
        )
