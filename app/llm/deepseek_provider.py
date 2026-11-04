"""Adapter do DeepSeek: Chat Completions com o SDK da OpenAI e outra base_url.

O modo de raciocínio é desligado: é ligado por padrão na API, aumenta custo e latência e não é
necessário para atendimento.
"""

import json
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
    LLMProviderError,
    LLMRateLimitError,
    Message,
    ToolCall,
    ToolResultMessage,
    parse_tool_arguments,
)

DEEPSEEK_BASE_URL = "https://api.deepseek.com"


def _to_api_messages(system: str, messages: Sequence[Message]) -> list[dict[str, Any]]:
    api_messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for message in messages:
        if isinstance(message, AssistantMessage):
            entry: dict[str, Any] = {"role": "assistant", "content": message.text}
            if message.tool_calls:
                entry["tool_calls"] = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(call.arguments or {}, ensure_ascii=False),
                        },
                    }
                    for call in message.tool_calls
                ]
            api_messages.append(entry)
        elif isinstance(message, ToolResultMessage):
            api_messages.append(
                {"role": "tool", "tool_call_id": message.call_id, "content": message.as_json()}
            )
        else:
            api_messages.append({"role": "user", "content": message.text})
    return api_messages


class DeepSeekProvider:
    name = "deepseek"

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
            base_url=DEEPSEEK_BASE_URL,
            timeout=REQUEST_TIMEOUT_SECONDS,
            max_retries=max_retries,
            http_client=http_client,
        )

    def complete(
        self, system: str, messages: Sequence[Message], tools: Sequence[dict[str, Any]]
    ) -> AssistantMessage:
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=_to_api_messages(system, messages),
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": tool["name"],
                            "description": tool["description"],
                            "parameters": tool["parameters"],
                        },
                    }
                    for tool in tools
                ],
                max_tokens=MAX_OUTPUT_TOKENS,
                extra_body={"thinking": {"type": "disabled"}},
            )
        except (openai.AuthenticationError, openai.PermissionDeniedError) as error:
            raise LLMAuthError("Chave do DeepSeek inválida ou sem permissão") from error
        except openai.RateLimitError as error:
            raise LLMRateLimitError("Limite de uso do DeepSeek atingido") from error
        except openai.APIStatusError as error:
            # O DeepSeek usa 402 para saldo insuficiente.
            if error.status_code == 402:
                raise LLMRateLimitError("Saldo insuficiente no DeepSeek") from error
            raise LLMProviderError(f"Falha na API do DeepSeek: {error.status_code}") from error
        except openai.APIError as error:
            raise LLMProviderError(f"Falha na API do DeepSeek: {type(error).__name__}") from error

        message = response.choices[0].message
        return AssistantMessage(
            text=message.content or "",
            tool_calls=tuple(
                ToolCall(
                    id=call.id,
                    name=call.function.name,
                    arguments=parse_tool_arguments(call.function.arguments),
                )
                for call in message.tool_calls or ()
            ),
            provider=self.name,
        )
