"""Adapter do Google Gemini (SDK google-genai)."""

from collections.abc import Sequence
from typing import Any

import httpx
from google import genai
from google.genai import errors, types

from app.llm.base import (
    MAX_OUTPUT_TOKENS,
    REQUEST_TIMEOUT_SECONDS,
    AssistantMessage,
    LLMAuthError,
    LLMProviderError,
    LLMRateLimitError,
    Message,
    ToolCall,
    ToolResultMessage,
)


def _to_contents(messages: Sequence[Message]) -> list[types.Content]:
    contents: list[types.Content] = []
    for message in messages:
        if isinstance(message, AssistantMessage):
            # O conteúdo original preserva as assinaturas de raciocínio das chamadas de função.
            contents.append(
                message.provider_state
                or types.Content(role="model", parts=[types.Part(text=message.text)])
            )
        elif isinstance(message, ToolResultMessage):
            part = types.Part(
                function_response=types.FunctionResponse(
                    id=message.call_id, name=message.name, response=message.result
                )
            )
            # Respostas de chamadas paralelas precisam ir no mesmo turno.
            previous = contents[-1] if contents else None
            if previous and previous.parts and previous.parts[-1].function_response:
                previous.parts.append(part)
            else:
                contents.append(types.Content(role="user", parts=[part]))
        else:
            contents.append(types.Content(role="user", parts=[types.Part(text=message.text)]))
    return contents


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str, model: str, http_client: httpx.Client | None = None) -> None:
        self.model = model
        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=int(REQUEST_TIMEOUT_SECONDS * 1000), httpx_client=http_client
            ),
        )

    def complete(
        self, system: str, messages: Sequence[Message], tools: Sequence[dict[str, Any]]
    ) -> AssistantMessage:
        config = types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=MAX_OUTPUT_TOKENS,
            tools=[
                types.Tool(
                    function_declarations=[
                        types.FunctionDeclaration(
                            name=tool["name"],
                            description=tool["description"],
                            parameters_json_schema=tool["parameters"],
                        )
                        for tool in tools
                    ]
                )
            ],
        )
        try:
            response = self._client.models.generate_content(
                model=self.model, contents=_to_contents(messages), config=config
            )
        except errors.APIError as error:
            raise _map_error(error) from error
        except httpx.HTTPError as error:
            raise LLMProviderError(f"Falha na API do Gemini: {type(error).__name__}") from error

        candidates = response.candidates or []
        content = candidates[0].content if candidates else None
        parts = (content.parts if content else None) or []
        calls = [part.function_call for part in parts if part.function_call]
        return AssistantMessage(
            text="".join(part.text for part in parts if part.text and not part.thought),
            tool_calls=tuple(
                ToolCall(
                    id=call.id or f"gemini-call-{index}", name=call.name, arguments=call.args or {}
                )
                for index, call in enumerate(calls)
            ),
            provider=self.name,
            provider_state=content,
        )


def _map_error(error: errors.APIError) -> Exception:
    # O Gemini responde 400 com API_KEY_INVALID para chaves inválidas.
    if error.code in (401, 403) or "API_KEY_INVALID" in str(error):
        return LLMAuthError("Chave do Gemini inválida ou sem permissão")
    if error.code == 429:
        return LLMRateLimitError("Limite de uso do Gemini atingido")
    return LLMProviderError(f"Falha na API do Gemini: {error.code}")
