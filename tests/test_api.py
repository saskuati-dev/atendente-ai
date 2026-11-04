"""API de ponta a ponta, com o banco real, um provedor falso e sem chaves de verdade."""

import logging
import threading
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api.sessions import InMemorySessionStore, Session, trim_history
from app.config import Settings
from app.llm.base import (
    AssistantMessage,
    LLMAuthError,
    LLMError,
    LLMProviderError,
    LLMRateLimitError,
    Message,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from app.main import create_app

NOW = datetime(2026, 11, 4, 16, 0, tzinfo=UTC)
KEY_HEADERS = {"X-LLM-Provider": "openai", "X-LLM-Api-Key": "chave-do-cliente"}


class ScriptedProvider:
    def __init__(self, name: str, model: str, script: list[Any]) -> None:
        self.name = name
        self.model = model
        self.script = script
        self.requests: list[list[Message]] = []

    def complete(
        self, system: str, messages: Sequence[Message], tools: Sequence[dict[str, Any]]
    ) -> AssistantMessage:
        self.requests.append(list(messages))
        step = self.script.pop(0) if self.script else AssistantMessage("ok", provider=self.name)
        if isinstance(step, LLMError):
            raise step
        if callable(step):
            return step()
        return step


class Harness:
    def __init__(self, db_path: Path, tmp_path: Path, **settings: Any) -> None:
        self.script: list[Any] = []
        self.created: list[tuple[str, str, str | None]] = []
        self.provider: ScriptedProvider | None = None
        self.store = InMemorySessionStore()
        config = Settings(
            _env_file=None,
            catalog_db_path=db_path,
            state_db_path=tmp_path / "state.db",
            **settings,
        )
        app = create_app(config, self.store, self._factory, lambda: NOW)
        self.client = TestClient(app, raise_server_exceptions=False)

    def _factory(self, name: str, key: str, model: str | None) -> ScriptedProvider:
        self.created.append((name, key, model))
        self.provider = ScriptedProvider(name, model or "padrao", self.script)
        return self.provider

    def chat(self, message: str = "oi", headers: dict | None = None, **body: Any) -> Any:
        return self.client.post(
            "/chat", json={"message": message, **body}, headers=headers or KEY_HEADERS
        )


@pytest.fixture
def api(db_path: Path, tmp_path: Path) -> Harness:
    return Harness(db_path, tmp_path)


# Chat


def test_chat_runs_real_tools_against_the_catalog(api: Harness) -> None:
    api.script.extend(
        [
            AssistantMessage(
                "",
                tool_calls=(ToolCall("c1", "get_product", {"product_id": 81}),),
                provider="openai",
            ),
            AssistantMessage("Custa R$ 599,90!", provider="openai"),
        ]
    )
    response = api.chat("quanto custa o Yamaha C40?")
    assert response.status_code == 200
    body = response.json()
    assert body["reply"] == "Custa R$ 599,90!"
    assert body["tools_used"] == ["get_product"]
    assert body["provider"] == "openai"
    assert body["session_id"]

    assert api.provider is not None
    result = api.provider.requests[1][-1]
    assert isinstance(result, ToolResultMessage)
    assert result.result["current_price"] == "R$ 599,90"


def test_conversation_continues_with_session_id(api: Harness) -> None:
    session_id = api.chat("oi").json()["session_id"]
    second = api.chat("tudo bem?", session_id=session_id)
    assert second.json()["session_id"] == session_id
    assert api.provider is not None
    assert [m.text for m in api.provider.requests[0] if isinstance(m, UserMessage)] == [
        "oi",
        "tudo bem?",
    ]


def test_sessions_are_isolated(api: Harness) -> None:
    first = api.chat("meu telefone é 67999990000").json()["session_id"]
    second = api.chat("oi").json()["session_id"]
    assert first != second
    assert api.provider is not None
    assert len(api.provider.requests[0]) == 1


def test_unknown_session_is_rejected_instead_of_created(api: Harness) -> None:
    response = api.chat("oi", session_id="escolhida-pelo-cliente")
    assert response.status_code == 404
    assert api.provider is not None
    assert api.provider.requests == []


def test_complaint_is_persisted_through_the_api(api: Harness, tmp_path: Path) -> None:
    arguments = {"customer_name": "Ana", "contact": "ana@x.com", "summary": "Atraso"}
    api.script.extend(
        [
            AssistantMessage(
                "",
                tool_calls=(ToolCall("c1", "register_complaint", arguments),),
                provider="openai",
            ),
            AssistantMessage("Protocolo REC-000001", provider="openai"),
        ]
    )
    assert api.chat("quero reclamar").json()["tools_used"] == ["register_complaint"]
    assert (tmp_path / "state.db").exists()


@pytest.mark.parametrize("message", ["", "   ", "x" * 4001])
def test_invalid_messages_are_rejected(api: Harness, message: str) -> None:
    assert api.chat(message).status_code == 422
    assert api.created == []


# BYOK


def test_header_key_and_model_are_used(api: Harness) -> None:
    api.chat(headers={**KEY_HEADERS, "X-LLM-Model": "gpt-x"})
    assert api.created == [("openai", "chave-do-cliente", "gpt-x")]


@pytest.mark.parametrize(
    ("header", "resolved"),
    [
        ("google", "gemini"),
        ("ChatGPT", "openai"),
        ("claude", "anthropic"),
        ("deepseek", "deepseek"),
    ],
)
def test_provider_aliases_in_header(api: Harness, header: str, resolved: str) -> None:
    api.chat(headers={"X-LLM-Provider": header, "X-LLM-Api-Key": "k"})
    assert api.created[0][0] == resolved


def test_unknown_provider_returns_400(api: Harness) -> None:
    response = api.chat(headers={"X-LLM-Provider": "mistral", "X-LLM-Api-Key": "k"})
    assert response.status_code == 400
    assert "anthropic" in response.json()["detail"]


def test_missing_key_returns_401(api: Harness) -> None:
    response = api.chat(headers={"X-LLM-Provider": "openai"})
    assert response.status_code == 401
    assert "X-LLM-Api-Key" in response.json()["detail"]


def test_server_key_is_the_fallback(db_path: Path, tmp_path: Path) -> None:
    api = Harness(db_path, tmp_path, openai_api_key=SecretStr("chave-do-servidor"))
    api.chat(headers={"X-LLM-Provider": "openai"})
    assert api.created == [("openai", "chave-do-servidor", None)]


def test_request_key_overrides_server_key(db_path: Path, tmp_path: Path) -> None:
    api = Harness(db_path, tmp_path, openai_api_key=SecretStr("chave-do-servidor"))
    api.chat()
    assert api.created[0][1] == "chave-do-cliente"


def test_default_provider_and_model_from_settings(db_path: Path, tmp_path: Path) -> None:
    api = Harness(
        db_path,
        tmp_path,
        llm_provider="deepseek",
        llm_model="modelo-config",
        deepseek_api_key=SecretStr("k"),
    )
    assert api.client.post("/chat", json={"message": "oi"}).status_code == 200
    assert api.created == [("deepseek", "k", "modelo-config")]


def test_configured_model_is_not_sent_to_another_provider(db_path: Path, tmp_path: Path) -> None:
    api = Harness(db_path, tmp_path, llm_provider="deepseek", llm_model="modelo-deepseek")
    api.chat()
    assert api.created[0] == ("openai", "chave-do-cliente", None)


def test_keys_never_appear_in_responses_or_logs(
    api: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    api.script.append(LLMProviderError("Falha na API"))
    responses = [api.chat(), api.client.get("/providers"), api.client.get("/health")]
    for response in responses:
        assert "chave-do-cliente" not in response.text
    assert "chave-do-cliente" not in caplog.text


def test_settings_do_not_expose_keys_in_repr() -> None:
    settings = Settings(_env_file=None, openai_api_key=SecretStr("segredo"))
    assert "segredo" not in repr(settings)
    assert settings.server_key("openai") == "segredo"
    assert settings.server_key("gemini") is None


# Erros do provedor


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (LLMAuthError("Chave inválida"), 401),
        (LLMRateLimitError("Limite atingido"), 429),
        (LLMProviderError("Falha"), 502),
    ],
)
def test_provider_errors_are_mapped(api: Harness, error: LLMError, expected: int) -> None:
    api.script.append(error)
    response = api.chat()
    assert response.status_code == expected
    assert response.json()["detail"] == str(error)


def test_failed_turn_does_not_change_the_history(api: Harness) -> None:
    session_id = api.chat("primeira").json()["session_id"]
    api.script.append(LLMProviderError("Falha"))
    assert api.chat("com erro", session_id=session_id).status_code == 502
    api.chat("depois", session_id=session_id)
    assert api.provider is not None
    texts = [m.text for m in api.provider.requests[0] if isinstance(m, UserMessage)]
    assert texts == ["primeira", "depois"]


def test_missing_catalog_returns_503(tmp_path: Path) -> None:
    api = Harness(tmp_path / "nao-existe.db", tmp_path)
    assert api.chat().status_code == 503
    assert api.client.get("/health").status_code == 503


def test_concurrent_message_in_same_session_returns_409(api: Harness) -> None:
    session_id = api.chat("oi").json()["session_id"]
    entered, release = threading.Event(), threading.Event()

    def slow() -> AssistantMessage:
        entered.set()
        release.wait(timeout=5)
        return AssistantMessage("ok", provider="openai")

    api.script.append(slow)
    thread = threading.Thread(target=api.chat, kwargs={"session_id": session_id})
    thread.start()
    assert entered.wait(timeout=5)
    try:
        assert api.chat("outra", session_id=session_id).status_code == 409
    finally:
        release.set()
        thread.join()
    assert api.chat("depois", session_id=session_id).status_code == 200


# Outras rotas


def test_health(api: Harness) -> None:
    assert api.client.get("/health").json() == {"status": "ok"}


def test_providers_lists_all_without_keys(db_path: Path, tmp_path: Path) -> None:
    api = Harness(db_path, tmp_path, gemini_api_key=SecretStr("segredo"))
    response = api.client.get("/providers")
    body = response.json()
    assert "segredo" not in response.text
    assert body["default_provider"] == "anthropic"
    info = {p["name"]: p for p in body["providers"]}
    assert set(info) == {"anthropic", "gemini", "openai", "deepseek"}
    assert info["gemini"]["server_key_configured"] is True
    assert info["openai"]["server_key_configured"] is False
    assert "chatgpt" in info["openai"]["aliases"]


# Sessões


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_session_expires_after_ttl() -> None:
    clock = FakeClock()
    store = InMemorySessionStore(ttl_seconds=100, clock=clock)
    session = store.create()
    clock.now = 100
    assert store.get(session.session_id) is session  # o acesso renova o prazo
    clock.now = 201
    assert store.get(session.session_id) is None


def test_oldest_session_is_evicted_at_capacity() -> None:
    clock = FakeClock()
    store = InMemorySessionStore(max_sessions=2, clock=clock)
    first = store.create()
    clock.now = 1
    second = store.create()
    clock.now = 2
    third = store.create()
    assert store.get(first.session_id) is None
    assert store.get(second.session_id) is second
    assert store.get(third.session_id) is third


def test_trim_history_always_starts_on_a_customer_message() -> None:
    turn: list[Message] = [
        UserMessage("pergunta"),
        AssistantMessage("", tool_calls=(ToolCall("c", "t", {}),)),
        ToolResultMessage("c", "t", {}),
        AssistantMessage("resposta"),
    ]
    trimmed = trim_history(turn * 5, max_user_turns=2)
    assert len(trimmed) == 8
    assert isinstance(trimmed[0], UserMessage)


def test_store_trims_on_append() -> None:
    store = InMemorySessionStore()
    session: Session = store.create()
    store.append(session, [UserMessage(str(i)) for i in range(40)])
    assert len(session.messages) == 30
    assert session.messages[-1] == UserMessage("39")
