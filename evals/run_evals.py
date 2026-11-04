"""Roda os casos de avaliação contra um provedor real. Consome a cota da chave usada.

Uso: uv run python evals/run_evals.py --provider deepseek --model deepseek-flash [--only ID ...]

A chave vem do .env. Os resultados completos ficam em evals/results/.
"""

import argparse
import json
import re
import sqlite3
import sys
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.agent.loop import run_agent  # noqa: E402
from app.config import Settings  # noqa: E402
from app.db.connection import DEFAULT_DB_PATH, connect_readonly  # noqa: E402
from app.db.text import normalize_text  # noqa: E402
from app.llm.base import LLMError, LLMProvider, Message  # noqa: E402
from app.llm.factory import DEFAULT_MODELS, create_provider, resolve_provider_name  # noqa: E402
from app.tools.complaints import ComplaintStore  # noqa: E402
from app.tools.context import ToolContext  # noqa: E402
from evals.cases import CASES, Case  # noqa: E402

RESULTS_DIR = PROJECT_ROOT / "evals" / "results"


@dataclass(frozen=True)
class CaseResult:
    case: Case
    replies: tuple[str, ...]
    tools: tuple[str, ...]
    failures: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.failures


def score(case: Case, replies: tuple[str, ...], tools: tuple[str, ...]) -> tuple[str, ...]:
    """Compara uma conversa com o esperado e devolve a lista de falhas."""
    failures = []
    used = set(tools)
    for tool in case.tools_required:
        if tool not in used:
            failures.append(f"não chamou {tool}")
    if case.tools_any and not used.intersection(case.tools_any):
        failures.append(f"não chamou nenhuma de {', '.join(case.tools_any)}")
    for tool in used.intersection(case.tools_forbidden):
        failures.append(f"chamou {tool}, que não deveria")

    last = normalize_text(replies[-1]) if replies else ""
    for pattern in case.must_match:
        if not re.search(pattern, last):
            failures.append(f"faltou /{pattern}/")
    if any("**" in reply for reply in replies):
        failures.append("usou negrito com asteriscos")
    everything = normalize_text(" ".join(replies))
    for pattern in case.must_not_match:
        if re.search(pattern, everything):
            failures.append(f"apareceu /{pattern}/")
    return tuple(failures)


def run_case(case: Case, provider: LLMProvider, state_dir: Path) -> CaseResult:
    history: list[Message] = []
    replies: list[str] = []
    tools: list[str] = []
    complaints = ComplaintStore(state_dir / f"{case.id}.db")
    try:
        with closing(connect_readonly(DEFAULT_DB_PATH)) as catalog:
            ctx = ToolContext(catalog=catalog, complaints=complaints, now=case.now)
            for turn in case.turns:
                reply = run_agent(provider, history, turn, ctx)
                history.extend(reply.messages)
                replies.append(reply.text)
                tools.extend(tool.name for tool in reply.tool_trace)
    except (LLMError, sqlite3.Error) as error:
        return CaseResult(case, tuple(replies), tuple(tools), (f"erro: {error}",))
    return CaseResult(case, tuple(replies), tuple(tools), score(case, tuple(replies), tuple(tools)))


def to_json(result: CaseResult) -> dict[str, Any]:
    return {
        "id": result.case.id,
        "group": result.case.group,
        "passed": result.passed,
        "failures": result.failures,
        "tools": result.tools,
        "turns": result.case.turns,
        "replies": result.replies,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    settings = Settings()
    parser.add_argument("--provider", default=settings.llm_provider)
    parser.add_argument("--model", default=None)
    parser.add_argument("--only", nargs="*", help="IDs dos casos a rodar")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    name = resolve_provider_name(args.provider)
    key = settings.server_key(name)
    if not key:
        print(f"Sem chave de {name} no .env.", file=sys.stderr)
        return 2
    provider = create_provider(name, key, args.model)
    cases = [c for c in CASES if not args.only or c.id in args.only]
    print(f"{len(cases)} casos | {name} | {provider.model or DEFAULT_MODELS[name]}\n")

    with tempfile.TemporaryDirectory() as state_dir, ThreadPoolExecutor(args.workers) as pool:
        results = list(pool.map(lambda c: run_case(c, provider, Path(state_dir)), cases))

    for result in results:
        print(f"{'OK  ' if result.passed else 'FALHA'} {result.case.group:10} {result.case.id}")
        if not result.passed:
            print(f"      tools: {', '.join(result.tools) or '(nenhuma)'}")
            for failure in result.failures:
                print(f"      - {failure}")
            print(f"      resposta: {(result.replies or ('',))[-1][:400]!r}")

    passed = sum(r.passed for r in results)
    print(f"\n{passed}/{len(results)} passaram")
    totals, oks = (
        Counter(r.case.group for r in results),
        Counter(r.case.group for r in results if r.passed),
    )
    for group, total in totals.items():
        print(f"  {group}: {oks[group]}/{total}")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output = RESULTS_DIR / f"{stamp}-{name}.json"
    output.write_text(
        json.dumps(
            {"provider": name, "model": provider.model, "results": [to_json(r) for r in results]},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nResultados em {output}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
