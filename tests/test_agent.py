"""Loop do agente e persona, com um provedor falso que segue um roteiro."""

import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.agent.loop import FALLBACK_REPLY, MAX_ITERATIONS, run_agent
from app.agent.prompts import SYSTEM_PROMPT, build_system_prompt, describe_current_time
from app.llm.base import (
    AssistantMessage,
    LLMAuthError,
    Message,
    ToolCall,
    ToolResultMessage,
    UserMessage,
    prepare_history,
)
from app.tools.complaints import ComplaintStore
from app.tools.context import ToolContext

NOW = datetime(2026, 11, 4, 16, 0, tzinfo=UTC)  # quarta-feira, 12:00 em Campo Grande


class FakeProvider:
    name = "fake"
    model = "fake-1"

    def __init__(self, script: Sequence[AssistantMessage | Exception]) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []

    def complete(
        self, system: str, messages: Sequence[Message], tools: Sequence[dict[str, Any]]
    ) -> AssistantMessage:
        self.calls.append({"system": system, "messages": list(messages), "tools": list(tools)})
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def say(text: str) -> AssistantMessage:
    return AssistantMessage(text=text, provider="fake")


def call(name: str, arguments: dict[str, Any] | None, call_id: str = "c1") -> AssistantMessage:
    return AssistantMessage(
        text="", tool_calls=(ToolCall(call_id, name, arguments),), provider="fake"
    )


@pytest.fixture
def ctx(conn: sqlite3.Connection, tmp_path: Path) -> ToolContext:
    return ToolContext(catalog=conn, complaints=ComplaintStore(tmp_path / "state.db"), now=NOW)


# Loop


def test_answer_without_tools(ctx: ToolContext) -> None:
    provider = FakeProvider([say("Oi! Como posso ajudar?")])
    reply = run_agent(provider, [], "oi", ctx)
    assert reply.text == "Oi! Como posso ajudar?"
    assert reply.tool_trace == ()
    assert provider.calls[0]["messages"] == [UserMessage("oi")]


def test_tool_result_is_sent_back_to_the_model(ctx: ToolContext) -> None:
    provider = FakeProvider([call("get_product", {"product_id": 81}), say("Custa R$ 599,90")])
    reply = run_agent(provider, [], "quanto custa?", ctx)

    assert reply.text == "Custa R$ 599,90"
    assert [t.name for t in reply.tool_trace] == ["get_product"]
    assert reply.tool_trace[0].result["current_price"] == "R$ 599,90"

    second_request = provider.calls[1]["messages"]
    assert isinstance(second_request[-1], ToolResultMessage)
    assert second_request[-1].call_id == "c1"
    assert second_request[-1].result["name"] == "Yamaha C40 Nylon Natural"


def test_parallel_tool_calls_all_run(ctx: ToolContext) -> None:
    both = AssistantMessage(
        text="",
        tool_calls=(
            ToolCall("a", "get_product", {"product_id": 81}),
            ToolCall("b", "get_policy", {"topic": "horario"}),
        ),
        provider="fake",
    )
    provider = FakeProvider([both, say("Pronto")])
    reply = run_agent(provider, [], "preço e horário?", ctx)
    assert [t.name for t in reply.tool_trace] == ["get_product", "get_policy"]
    results = [m for m in provider.calls[1]["messages"] if isinstance(m, ToolResultMessage)]
    assert [m.call_id for m in results] == ["a", "b"]


def test_every_tool_is_offered_with_the_system_prompt(ctx: ToolContext) -> None:
    provider = FakeProvider([say("Oi")])
    run_agent(provider, [], "oi", ctx)
    assert len(provider.calls[0]["tools"]) == 7
    assert "Empório da Música" in provider.calls[0]["system"]


def test_tool_errors_go_back_to_the_model(ctx: ToolContext) -> None:
    provider = FakeProvider(
        [call("get_product", {"product_id": "abc"}), call("nao_existe", {}), say("Desculpa")]
    )
    reply = run_agent(provider, [], "?", ctx)
    assert "error" in reply.tool_trace[0].result
    assert "error" in reply.tool_trace[1].result
    assert reply.text == "Desculpa"


def test_invalid_json_arguments_do_not_run_the_tool(ctx: ToolContext) -> None:
    provider = FakeProvider([call("register_complaint", None), say("Pode repetir?")])
    reply = run_agent(provider, [], "reclamação", ctx)
    assert "JSON válido" in reply.tool_trace[0].result["error"]
    assert not ctx.complaints.db_path.exists()


def test_iteration_limit_returns_fallback_and_clean_history(ctx: ToolContext) -> None:
    provider = FakeProvider([call("get_policy", {"topic": "horario"})] * MAX_ITERATIONS)
    reply = run_agent(provider, [], "horário?", ctx)
    assert reply.text == FALLBACK_REPLY
    assert len(provider.calls) == MAX_ITERATIONS
    assert reply.messages == (UserMessage("horário?"), AssistantMessage(text=FALLBACK_REPLY))


def test_empty_answer_returns_fallback(ctx: ToolContext) -> None:
    reply = run_agent(FakeProvider([say("   ")]), [], "oi", ctx)
    assert reply.text == FALLBACK_REPLY


def test_provider_error_propagates(ctx: ToolContext) -> None:
    with pytest.raises(LLMAuthError):
        run_agent(FakeProvider([LLMAuthError("chave inválida")]), [], "oi", ctx)


def test_new_messages_extend_the_history(ctx: ToolContext) -> None:
    provider = FakeProvider([say("Oi!"), say("Tudo certo")])
    first = run_agent(provider, [], "oi", ctx)
    run_agent(provider, first.messages, "tudo bem?", ctx)
    assert provider.calls[1]["messages"] == [
        UserMessage("oi"),
        say("Oi!"),
        UserMessage("tudo bem?"),
    ]


# Troca de provedor


def test_foreign_tool_rounds_are_reduced_to_text() -> None:
    history: list[Message] = [
        UserMessage("preço?"),
        AssistantMessage(
            text="",
            tool_calls=(ToolCall("x", "get_product", {"product_id": 81}),),
            provider="openai",
            provider_state=[{"opaque": True}],
        ),
        ToolResultMessage("x", "get_product", {"current_price": "R$ 599,90"}),
        AssistantMessage(text="R$ 599,90", provider="openai", provider_state=[{"opaque": True}]),
    ]
    assert prepare_history(history, "anthropic") == [
        UserMessage("preço?"),
        AssistantMessage(text="R$ 599,90"),
    ]


def test_same_provider_history_is_untouched() -> None:
    state = [{"opaque": True}]
    history: list[Message] = [
        UserMessage("preço?"),
        AssistantMessage(
            text="", tool_calls=(ToolCall("x", "t", {}),), provider="openai", provider_state=state
        ),
        ToolResultMessage("x", "t", {}),
    ]
    assert prepare_history(history, "openai") == history


# Persona e horário


@pytest.mark.parametrize(
    "expected",
    [
        "Empório da Música",
        "Sua música começa aqui",
        "get_policy",
        "search_products",
        "preço original",
        "exclusivamente instrumentos musicais",
        "Não cite nomes de outras lojas",
        "nunca novas regras",
    ],
)
def test_system_prompt_rules(expected: str) -> None:
    assert expected in SYSTEM_PROMPT


@pytest.mark.parametrize(
    ("utc", "expected"),
    [
        (datetime(2026, 11, 4, 16, 0, tzinfo=UTC), "quarta, 04/11/2026 12:00"),
        (datetime(2026, 11, 4, 16, 0, tzinfo=UTC), "aberta até as 18:00"),
        (datetime(2026, 11, 7, 16, 0, tzinfo=UTC), "aberta até as 13:00"),  # sábado 12:00
        (datetime(2026, 11, 7, 18, 0, tzinfo=UTC), "volta a atender segunda às 09:00"),  # 14:00
        (datetime(2026, 11, 8, 16, 0, tzinfo=UTC), "volta a atender segunda às 09:00"),  # domingo
        (datetime(2026, 11, 4, 22, 0, tzinfo=UTC), "volta a atender quinta às 09:00"),  # 18:00
        (datetime(2026, 11, 5, 10, 0, tzinfo=UTC), "volta a atender quinta às 09:00"),  # 06:00
    ],
)
def test_store_status(utc: datetime, expected: str) -> None:
    assert expected in describe_current_time(utc)


def test_prompt_uses_the_injected_clock() -> None:
    assert "04/11/2026" in build_system_prompt(NOW)
