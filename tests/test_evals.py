"""Pontuação das evals e validade dos casos, sem chamar nenhum modelo."""

import re

import pytest

from app.db.text import normalize_text
from app.tools.registry import TOOLS
from evals.cases import CASES, Case
from evals.run_evals import score

CASE = Case(
    "exemplo",
    "grupo",
    ("oi",),
    tools_required=("get_policy",),
    tools_forbidden=("get_order",),
    must_match=(r"horario",),
    must_not_match=(r"proibido",),
)


def test_passing_conversation_has_no_failures() -> None:
    assert score(CASE, ("O Horário é este",), ("get_policy",)) == ()


def test_each_rule_is_reported() -> None:
    failures = score(CASE, ("proibido",), ("get_order",))
    assert len(failures) == 4


def test_must_match_only_looks_at_the_last_reply() -> None:
    assert score(CASE, ("horario", "outra coisa"), ("get_policy",)) == ("faltou /horario/",)


def test_must_not_match_looks_at_all_replies() -> None:
    assert score(CASE, ("proibido", "horario"), ("get_policy",)) == ("apareceu /proibido/",)


def test_replies_are_compared_without_accents() -> None:
    assert score(CASE, ("HORÁRIO",), ("get_policy",)) == ()


def test_case_ids_are_unique() -> None:
    ids = [case.id for case in CASES]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_cases_reference_real_tools_and_normalized_patterns(case: Case) -> None:
    for tool in (*case.tools_required, *case.tools_any, *case.tools_forbidden):
        assert tool in TOOLS
    for pattern in (*case.must_match, *case.must_not_match):
        re.compile(pattern)
        # Os padrões são comparados com texto normalizado: com acento, nunca casariam.
        assert pattern == normalize_text(pattern)
