"""Tool de políticas: devolve a seção inteira do manual para o tópico pedido."""

import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, get_args

from pydantic import BaseModel, Field

from app.db.connection import DATA_DIR
from app.tools.context import ToolContext

POLICIES_PATH = DATA_DIR / "policies.md"

PolicyTopic = Literal[
    "sobre", "horario", "pagamento", "trocas", "frete", "promocoes", "garantia", "privacidade"
]
POLICY_TOPICS: tuple[str, ...] = get_args(PolicyTopic)

_SECTION_HEADER = re.compile(r"^## \[(\w+)\] .*$", re.MULTILINE)


@lru_cache
def load_policies(path: Path = POLICIES_PATH) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    matches = list(_SECTION_HEADER.finditer(text))
    sections = {}
    for match, following in zip(matches, [*matches[1:], None], strict=True):
        end = following.start() if following else len(text)
        sections[match.group(1)] = text[match.start() : end].strip()
    return sections


class GetPolicyArgs(BaseModel):
    topic: PolicyTopic = Field(
        description="sobre: a loja, endereço, contatos e o que vende. horario: funcionamento. "
        "pagamento: formas e regras de parcelamento. trocas: trocas, devoluções e "
        "arrependimento. frete: entregas, prazos e rastreamento. promocoes: regras e calendário "
        "(promoções vigentes vêm de list_active_promotions). garantia: garantia legal e do "
        "fabricante. privacidade: dados pessoais e LGPD."
    )


def get_policy(ctx: ToolContext, args: GetPolicyArgs) -> dict[str, Any]:
    return {"topic": args.topic, "content": load_policies()[args.topic]}
