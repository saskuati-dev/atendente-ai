"""Adapters com os SDKs reais e HTTP simulado: confere o que vai e o que volta pela rede."""

import json
from collections.abc import Callable
from typing import Any

import httpx
import httpx2
import pytest

from app.llm.anthropic_provider import AnthropicProvider
from app.llm.base import (
    AssistantMessage,
    LLMAuthError,
    LLMBadRequestError,
    LLMProviderError,
    LLMRateLimitError,
    Message,
    ToolCall,
    ToolResultMessage,
    UserMessage,
    parse_tool_arguments,
)
from app.llm.deepseek_provider import DeepSeekProvider
from app.llm.factory import (
    DEFAULT_MODELS,
    PROVIDERS,
    UnknownProviderError,
    create_provider,
    resolve_provider_name,
)
from app.llm.gemini_provider import GeminiProvider
from app.llm.openai_provider import OpenAIProvider
from app.tools.registry import tool_schemas

SECRET_KEY = "chave-secreta-de-teste"
TOOLS = [
    {
        "name": "get_policy",
        "description": "Consulta políticas",
        "parameters": {"type": "object", "properties": {"topic": {"type": "string"}}},
    }
]


class Recorder:
    """Transporte HTTP que grava as requisições e devolve respostas prontas.

    O SDK da Anthropic usa httpx2, e os demais usam httpx.
    """

    def __init__(self, *responses: tuple[int, dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.requests: list[Any] = []
        self.client = httpx.Client(transport=httpx.MockTransport(self._handler(httpx)))
        self.client2 = httpx2.Client(transport=httpx2.MockTransport(self._handler(httpx2)))

    def _handler(self, library: Any) -> Callable[[Any], Any]:
        def handle(request: Any) -> Any:
            self.requests.append(request)
            if not self.responses:
                raise library.ConnectError("sem rede")
            status, body = self.responses.pop(0)
            return library.Response(status, json=body)

        return handle

    def body(self, index: int = -1) -> dict[str, Any]:
        return json.loads(self.requests[index].content)


# Anthropic


def anthropic_response(content: list[dict[str, Any]], stop_reason: str = "end_turn") -> dict:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "m",
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


def make_anthropic(recorder: Recorder) -> AnthropicProvider:
    return AnthropicProvider(SECRET_KEY, "modelo", http_client=recorder.client2, max_retries=0)


def test_anthropic_request_and_tool_call() -> None:
    thinking = {"type": "thinking", "thinking": "hmm", "signature": "sig"}
    tool_use = {"type": "tool_use", "id": "t1", "name": "get_policy", "input": {"topic": "frete"}}
    recorder = Recorder(
        (200, anthropic_response([thinking, tool_use], "tool_use")),
        (200, anthropic_response([{"type": "text", "text": "Frete grátis"}])),
    )
    provider = make_anthropic(recorder)

    first = provider.complete("sistema", [UserMessage("frete?")], TOOLS)
    assert first.tool_calls == (ToolCall("t1", "get_policy", {"topic": "frete"}),)

    body = recorder.body()
    assert body["system"] == "sistema"
    assert body["model"] == "modelo"
    assert body["tools"][0]["input_schema"] == TOOLS[0]["parameters"]
    assert body["messages"] == [{"role": "user", "content": "frete?"}]

    history: list[Message] = [
        UserMessage("frete?"),
        first,
        ToolResultMessage("t1", "get_policy", {"content": "Grátis"}),
    ]
    second = provider.complete("sistema", history, TOOLS)
    assert second.text == "Frete grátis"
    assert second.tool_calls == ()

    sent = recorder.body()["messages"]
    assert sent[1]["role"] == "assistant"
    assert sent[1]["content"][0]["signature"] == "sig"  # raciocínio volta intacto
    assert sent[1]["content"][1] == tool_use
    assert sent[2]["content"] == [
        {"type": "tool_result", "tool_use_id": "t1", "content": '{"content": "Grátis"}'}
    ]


def test_anthropic_parallel_results_share_one_message() -> None:
    recorder = Recorder((200, anthropic_response([{"type": "text", "text": "ok"}])))
    assistant = AssistantMessage(
        text="",
        tool_calls=(ToolCall("a", "t", {}), ToolCall("b", "t", {})),
        provider="anthropic",
        provider_state=[
            {"type": "tool_use", "id": "a", "name": "t", "input": {}},
            {"type": "tool_use", "id": "b", "name": "t", "input": {}},
        ],
    )
    make_anthropic(recorder).complete(
        "s",
        [
            UserMessage("oi"),
            assistant,
            ToolResultMessage("a", "t", {}),
            ToolResultMessage("b", "t", {}),
        ],
        TOOLS,
    )
    sent = recorder.body()["messages"]
    assert len(sent) == 3
    assert [b["tool_use_id"] for b in sent[2]["content"]] == ["a", "b"]


# OpenAI


def openai_response(output: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": "resp_1",
        "object": "response",
        "created_at": 1,
        "model": "m",
        "status": "completed",
        "output": output,
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
    }


def openai_text(text: str) -> dict[str, Any]:
    return {
        "type": "message",
        "id": "m1",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }


def make_openai(recorder: Recorder) -> OpenAIProvider:
    return OpenAIProvider(SECRET_KEY, "modelo", http_client=recorder.client, max_retries=0)


def test_openai_uses_responses_api_and_replays_reasoning() -> None:
    reasoning = {"type": "reasoning", "id": "r1", "summary": [], "encrypted_content": "enc"}
    function_call = {
        "type": "function_call",
        "id": "fc1",
        "call_id": "call_1",
        "name": "get_policy",
        "arguments": '{"topic": "frete"}',
        "status": "completed",
    }
    recorder = Recorder(
        (200, openai_response([reasoning, function_call])),
        (200, openai_response([openai_text("Frete grátis")])),
    )
    provider = make_openai(recorder)

    first = provider.complete("sistema", [UserMessage("frete?")], TOOLS)
    assert first.tool_calls == (ToolCall("call_1", "get_policy", {"topic": "frete"}),)
    assert recorder.requests[0].url.path.endswith("/responses")

    body = recorder.body()
    assert body["instructions"] == "sistema"
    assert body["store"] is False
    assert body["include"] == ["reasoning.encrypted_content"]
    assert body["tools"][0]["name"] == "get_policy"
    assert body["tools"][0]["strict"] is False

    second = provider.complete(
        "sistema",
        [UserMessage("frete?"), first, ToolResultMessage("call_1", "get_policy", {"ok": True})],
        TOOLS,
    )
    assert second.text == "Frete grátis"
    sent = recorder.body()["input"]
    assert sent[1]["encrypted_content"] == "enc"
    assert sent[2]["call_id"] == "call_1"
    assert sent[3] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": '{"ok": true}',
    }


def test_openai_invalid_tool_json_becomes_none() -> None:
    bad_call = {
        "type": "function_call",
        "id": "fc1",
        "call_id": "c",
        "name": "get_policy",
        "arguments": "{quebrado",
        "status": "completed",
    }
    recorder = Recorder((200, openai_response([bad_call])))
    result = make_openai(recorder).complete("s", [UserMessage("x")], TOOLS)
    assert result.tool_calls[0].arguments is None


# DeepSeek


def deepseek_response(message: dict[str, Any], finish_reason: str = "stop") -> dict[str, Any]:
    return {
        "id": "c1",
        "object": "chat.completion",
        "created": 1,
        "model": "m",
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
    }


def make_deepseek(recorder: Recorder) -> DeepSeekProvider:
    return DeepSeekProvider(SECRET_KEY, "modelo", http_client=recorder.client, max_retries=0)


def test_deepseek_uses_chat_completions_without_thinking() -> None:
    tool_message = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "c1",
                "type": "function",
                "function": {"name": "get_policy", "arguments": '{"topic": "frete"}'},
            }
        ],
    }
    recorder = Recorder(
        (200, deepseek_response(tool_message, "tool_calls")),
        (200, deepseek_response({"role": "assistant", "content": "Frete grátis"})),
    )
    provider = make_deepseek(recorder)

    first = provider.complete("sistema", [UserMessage("frete?")], TOOLS)
    assert first.tool_calls == (ToolCall("c1", "get_policy", {"topic": "frete"}),)
    assert recorder.requests[0].url.host == "api.deepseek.com"
    assert recorder.requests[0].url.path.endswith("/chat/completions")

    body = recorder.body()
    assert body["thinking"] == {"type": "disabled"}
    assert body["max_tokens"] > 0
    assert body["messages"][0] == {"role": "system", "content": "sistema"}
    assert body["tools"][0]["function"]["name"] == "get_policy"

    second = provider.complete(
        "sistema",
        [UserMessage("frete?"), first, ToolResultMessage("c1", "get_policy", {"ok": True})],
        TOOLS,
    )
    assert second.text == "Frete grátis"
    sent = recorder.body()["messages"]
    assert sent[2]["tool_calls"][0]["function"]["arguments"] == '{"topic": "frete"}'
    assert sent[3] == {"role": "tool", "tool_call_id": "c1", "content": '{"ok": true}'}


def test_deepseek_insufficient_balance_is_a_limit_error() -> None:
    recorder = Recorder((402, {"error": {"message": "Insufficient Balance"}}))
    with pytest.raises(LLMRateLimitError):
        make_deepseek(recorder).complete("s", [UserMessage("x")], TOOLS)


# Gemini


def gemini_response(parts: list[dict[str, Any]]) -> dict[str, Any]:
    return {"candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": "STOP"}]}


def make_gemini(recorder: Recorder) -> GeminiProvider:
    return GeminiProvider(SECRET_KEY, "modelo", http_client=recorder.client)


def test_gemini_request_and_thought_signature_replay() -> None:
    call_part = {
        "functionCall": {"id": "g1", "name": "get_policy", "args": {"topic": "frete"}},
        "thoughtSignature": "c2lnbmF0dXJl",
    }
    recorder = Recorder(
        (200, gemini_response([call_part])),
        (200, gemini_response([{"text": "Frete grátis"}])),
    )
    provider = make_gemini(recorder)

    first = provider.complete("sistema", [UserMessage("frete?")], TOOLS)
    assert first.tool_calls == (ToolCall("g1", "get_policy", {"topic": "frete"}),)
    assert "modelo" in recorder.requests[0].url.path

    body = recorder.body()
    assert body["systemInstruction"]["parts"][0]["text"] == "sistema"
    declaration = body["tools"][0]["functionDeclarations"][0]
    assert declaration["name"] == "get_policy"
    schema = declaration.get("parametersJsonSchema") or declaration.get("parameters_json_schema")
    assert schema == TOOLS[0]["parameters"]

    second = provider.complete(
        "sistema",
        [UserMessage("frete?"), first, ToolResultMessage("g1", "get_policy", {"ok": True})],
        TOOLS,
    )
    assert second.text == "Frete grátis"
    contents = recorder.body()["contents"]
    assert contents[1]["role"] == "model"
    assert contents[1]["parts"][0]["thoughtSignature"]
    assert contents[1]["parts"][0]["functionCall"]["name"] == "get_policy"
    assert contents[2]["parts"][0]["functionResponse"] == {
        "id": "g1",
        "name": "get_policy",
        "response": {"ok": True},
    }


def test_gemini_ignores_thought_text_and_generates_missing_call_id() -> None:
    recorder = Recorder(
        (
            200,
            gemini_response(
                [
                    {"text": "raciocínio interno", "thought": True},
                    {"text": "Resposta"},
                    {"functionCall": {"name": "get_policy", "args": {}}},
                ]
            ),
        )
    )
    result = make_gemini(recorder).complete("s", [UserMessage("x")], TOOLS)
    assert result.text == "Resposta"
    assert result.tool_calls[0].id == "gemini-call-0"


def test_gemini_parallel_results_share_one_turn() -> None:
    call = {"functionCall": {"id": "a", "name": "t", "args": {}}}
    recorder = Recorder(
        (200, gemini_response([call, call])), (200, gemini_response([{"text": "ok"}]))
    )
    provider = make_gemini(recorder)
    first = provider.complete("s", [UserMessage("x")], TOOLS)
    provider.complete(
        "s",
        [UserMessage("x"), first, ToolResultMessage("a", "t", {}), ToolResultMessage("b", "t", {})],
        TOOLS,
    )
    contents = recorder.body()["contents"]
    assert len(contents) == 3
    assert len(contents[2]["parts"]) == 2


def test_gemini_no_candidates_returns_empty_text() -> None:
    recorder = Recorder((200, {"candidates": []}))
    result = make_gemini(recorder).complete("s", [UserMessage("x")], TOOLS)
    assert result.text == ""
    assert result.tool_calls == ()


def test_gemini_invalid_key_on_status_400() -> None:
    body = {
        "error": {
            "code": 400,
            "message": "API key not valid",
            "status": "INVALID_ARGUMENT",
            "details": [{"reason": "API_KEY_INVALID"}],
        }
    }
    with pytest.raises(LLMAuthError):
        make_gemini(Recorder((400, body))).complete("s", [UserMessage("x")], TOOLS)


# Comum a todos


FACTORIES: dict[str, Callable[[Recorder], Any]] = {
    "anthropic": make_anthropic,
    "openai": make_openai,
    "deepseek": make_deepseek,
    "gemini": make_gemini,
}


@pytest.mark.parametrize("name", FACTORIES)
@pytest.mark.parametrize(
    ("status", "expected"),
    [(401, LLMAuthError), (429, LLMRateLimitError), (500, LLMProviderError)],
)
def test_http_errors_are_mapped_without_leaking_the_key(
    name: str, status: int, expected: type[Exception]
) -> None:
    recorder = Recorder((status, {"error": {"code": status, "message": "falha"}}))
    with pytest.raises(expected) as caught:
        FACTORIES[name](recorder).complete("s", [UserMessage("x")], TOOLS)
    assert SECRET_KEY not in str(caught.value)


@pytest.mark.parametrize("name", FACTORIES)
@pytest.mark.parametrize("status", [400, 404])
def test_rejected_request_explains_the_reason(name: str, status: int) -> None:
    message = "Modelo inexistente: use outro"
    body = {"error": {"code": status, "message": message}, "message": message}
    with pytest.raises(LLMBadRequestError, match="Modelo inexistente"):
        FACTORIES[name](Recorder((status, body))).complete("s", [UserMessage("x")], TOOLS)


@pytest.mark.parametrize("name", FACTORIES)
def test_network_failure_is_a_provider_error(name: str) -> None:
    with pytest.raises(LLMProviderError):
        FACTORIES[name](Recorder()).complete("s", [UserMessage("x")], TOOLS)


@pytest.mark.parametrize("name", FACTORIES)
def test_customer_key_is_sent_only_in_headers(name: str) -> None:
    responses = {
        "anthropic": anthropic_response([{"type": "text", "text": "ok"}]),
        "openai": openai_response([openai_text("ok")]),
        "deepseek": deepseek_response({"role": "assistant", "content": "ok"}),
        "gemini": gemini_response([{"text": "ok"}]),
    }
    recorder = Recorder((200, responses[name]))
    FACTORIES[name](recorder).complete("s", [UserMessage("x")], TOOLS)
    request = recorder.requests[0]
    assert any(SECRET_KEY in value for value in request.headers.values())
    assert SECRET_KEY not in str(request.url)
    assert SECRET_KEY not in request.content.decode()


@pytest.mark.parametrize("name", FACTORIES)
def test_real_tool_schemas_are_accepted_by_the_sdk(name: str) -> None:
    responses = {
        "anthropic": anthropic_response([{"type": "text", "text": "ok"}]),
        "openai": openai_response([openai_text("ok")]),
        "deepseek": deepseek_response({"role": "assistant", "content": "ok"}),
        "gemini": gemini_response([{"text": "ok"}]),
    }
    recorder = Recorder((200, responses[name]))
    result = FACTORIES[name](recorder).complete("s", [UserMessage("x")], tool_schemas())
    assert result.text == "ok"


@pytest.mark.parametrize("name", FACTORIES)
def test_history_from_foreign_or_plain_text_assistant(name: str) -> None:
    responses = {
        "anthropic": anthropic_response([{"type": "text", "text": "ok"}]),
        "openai": openai_response([openai_text("ok")]),
        "deepseek": deepseek_response({"role": "assistant", "content": "ok"}),
        "gemini": gemini_response([{"text": "ok"}]),
    }
    recorder = Recorder((200, responses[name]))
    history: list[Message] = [
        UserMessage("oi"),
        AssistantMessage(text="Olá!"),
        UserMessage("tudo?"),
    ]
    assert FACTORIES[name](recorder).complete("s", history, TOOLS).text == "ok"
    assert "Olá!" in json.dumps(recorder.body(), ensure_ascii=False)


# Fábrica


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("anthropic", "anthropic"),
        ("claude", "anthropic"),
        (" GOOGLE ", "gemini"),
        ("gemini", "gemini"),
        ("ChatGPT", "openai"),
        ("deepseek", "deepseek"),
    ],
)
def test_provider_names_and_aliases(raw: str, expected: str) -> None:
    assert resolve_provider_name(raw) == expected


def test_unknown_provider() -> None:
    with pytest.raises(UnknownProviderError):
        create_provider("mistral", "chave")


@pytest.mark.parametrize("name", PROVIDERS)
def test_factory_uses_default_and_custom_model(name: str) -> None:
    assert create_provider(name, "chave").model == DEFAULT_MODELS[name]
    assert create_provider(name, "chave", model="outro").model == "outro"


# Argumentos de tools


@pytest.mark.parametrize(
    ("raw", "expected"),
    [('{"a": 1}', {"a": 1}), ("", {}), (None, {}), ("{ruim", None), ("[1]", None)],
)
def test_parse_tool_arguments(raw: str | None, expected: dict[str, Any] | None) -> None:
    assert parse_tool_arguments(raw) == expected
