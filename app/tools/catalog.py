"""Tools de catálogo: produtos, disponibilidade, promoções e formas de pagamento."""

import re
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.db import repository as repo
from app.db.quality import PRODUCTS_WITH_CONFLICTING_DATA
from app.db.text import normalize_text
from app.tools.context import ToolContext
from app.tools.pricing import (
    PIX_DISCOUNT_PERCENT,
    allows_combined_payment,
    apply_discount,
    format_brl,
    installment_options,
)

CategorySlug = Literal[
    "guitarras",
    "baixos",
    "baterias_percussao",
    "teclados_pianos",
    "violoes",
    "sopros_madeiras",
    "sopros_metais",
    "cordas_orquestrais",
    "ukuleles",
]
CATEGORY_IDS: dict[str, int] = {
    "guitarras": 1,
    "baixos": 2,
    "baterias_percussao": 3,
    "teclados_pianos": 4,
    "violoes": 5,
    "sopros_madeiras": 6,
    "sopros_metais": 7,
    "cordas_orquestrais": 8,
    "ukuleles": 9,
}

ACCESSORY_TERMS = frozenset(
    [
        "acessorio",
        "acessorios",
        "corda",
        "cordas",
        "encordoamento",
        "encordoamentos",
        "palheta",
        "palhetas",
        "cabo",
        "cabos",
        "case",
        "cases",
        "estojo",
        "estojos",
        "capa",
        "capas",
        "bag",
        "bags",
        "pedal",
        "pedais",
        "amplificador",
        "amplificadores",
        "ampli",
        "amp",
        "correia",
        "correias",
        "baqueta",
        "baquetas",
        "afinador",
        "afinadores",
        "capotraste",
        "suporte",
        "suportes",
        "banqueta",
        "banquetas",
        "metronomo",
        "metronomos",
        "microfone",
        "microfones",
    ]
)
INSTRUMENT_TERMS = frozenset(
    [
        "violao",
        "violoes",
        "guitarra",
        "guitarras",
        "baixo",
        "baixos",
        "contrabaixo",
        "contrabaixos",
        "bateria",
        "baterias",
        "percussao",
        "teclado",
        "teclados",
        "piano",
        "pianos",
        "sintetizador",
        "sintetizadores",
        "ukulele",
        "ukuleles",
    ]
)
# Termos sem valor de busca: como todos precisam aparecer no produto, derrubariam resultados.
SEARCH_STOPWORDS = frozenset(
    [
        "a",
        "o",
        "as",
        "os",
        "um",
        "uma",
        "uns",
        "umas",
        "de",
        "do",
        "da",
        "dos",
        "das",
        "para",
        "pra",
        "pro",
        "com",
        "e",
        "em",
        "no",
        "na",
        "nos",
        "nas",
        "que",
        "tem",
        "quero",
        "preciso",
        "procuro",
        "barato",
        "barata",
        "baratos",
        "baratas",
        "bom",
        "boa",
        "bons",
        "boas",
    ]
)

MSG_ACCESSORY = (
    "A loja trabalha exclusivamente com instrumentos musicais e não vende acessórios. Informe "
    "isso ao cliente, sem oferecer instrumentos no lugar e sem citar nomes de outras lojas."
)
MSG_AMBIGUOUS_STRINGS = (
    "Estes são instrumentos com cordas desse tipo. A loja não vende cordas avulsas; se o cliente "
    "queria cordas avulsas, explique isso, sem citar outras lojas."
)
MSG_NOT_FOUND = (
    "Nenhum produto encontrado no catálogo. Diga isso ao cliente e não cite produtos, marcas "
    "ou preços que não tenham vindo do sistema."
)
MSG_CONFLICTING_DATA = (
    "A descrição e as especificações deste produto estão em revisão. Informe só o nome, o "
    "preço e a disponibilidade. Não confirme nem negue marca, modelo ou especificações, mesmo "
    "as citadas pelo cliente, e oriente-o a confirmar os detalhes com a equipe."
)
AVAILABILITY_GUIDANCE = {
    "out_of_stock": "Temporariamente indisponível. Ofereça as alternativas listadas.",
    "discontinued": "Não faz mais parte do catálogo. Ofereça as alternativas listadas.",
    "coming_soon": "Ainda não está à venda. Ofereça as alternativas listadas.",
}


def _availability(product: repo.Product) -> str:
    if product.status != "active":
        return product.status
    return "available" if product.stock_quantity > 0 else "out_of_stock"


def _classify_accessory_query(query: str) -> Literal["accessory", "ambiguous_strings"] | None:
    """Classifica a busca pelo primeiro termo reconhecido.

    - "accessory": pedido de acessório, como "palheta" ou "cordas para violão".
    - "ambiguous_strings": "cordas de aço" pode ser a peça ou uma característica do instrumento.
    - None: busca normal, como "violão com afinador", "7 cordas" ou "com cordas de aço".
    """
    tokens = re.findall(r"[\w-]+", normalize_text(query))
    for index, token in enumerate(tokens):
        if token in INSTRUMENT_TERMS:
            return None
        if token not in ACCESSORY_TERMS:
            continue
        previous = tokens[index - 1] if index else ""
        if previous.isdigit() or previous in ("pre", "com"):
            return None
        following = [t for t in tokens[index + 1 :] if t not in SEARCH_STOPWORDS]
        if (
            token in ("corda", "cordas")
            and following[:1] in (["aco"], ["nylon"])
            and not INSTRUMENT_TERMS.intersection(tokens)
        ):
            return "ambiguous_strings"
        return "accessory"
    return None


def _strip_stopwords(query: str) -> str:
    return " ".join(t for t in normalize_text(query).split() if t not in SEARCH_STOPWORDS)


def _reais_to_cents(value: float | None) -> int | None:
    return None if value is None else int(Decimal(str(value)) * 100)


def _price_info(ctx: ToolContext, product: repo.Product) -> dict[str, Any]:
    """Preço vigente. A promoção vem sempre com preço original e percentual (seção 6.2)."""
    promotion = repo.get_active_promotion(ctx.catalog, product.product_id)
    if promotion is None:
        return {"current_price": format_brl(product.price_cents), "promotion": None}
    promotional = apply_discount(product.price_cents, promotion.discount_percent)
    return {
        "current_price": format_brl(promotional),
        "promotion": {
            "name": promotion.description,
            "discount_percent": promotion.discount_percent,
            "original_price": format_brl(product.price_cents),
            "promotional_price": format_brl(promotional),
        },
    }


def _summary(ctx: ToolContext, product: repo.Product) -> dict[str, Any]:
    """Resumo do produto. Item fora de venda não expõe preço, para não parecer uma oferta."""
    availability = _availability(product)
    data: dict[str, Any] = {
        "product_id": product.product_id,
        "name": product.name,
        "category": product.category_name,
        "availability": availability,
    }
    if availability in ("available", "out_of_stock"):
        data.update(_price_info(ctx, product))
    return data


def _alternatives(ctx: ToolContext, product: repo.Product, limit: int = 3) -> list[dict[str, Any]]:
    """Produtos da mesma categoria com estoque, dos mais semelhantes aos menos semelhantes.

    Prioriza o mesmo tipo de instrumento (7 cordas, depois tipo de corda) e, por fim, o preço.
    """

    def is_seven_string(item: repo.Product) -> bool:
        return "7 cordas" in item.name.lower()

    def similarity(item: repo.Product) -> tuple[bool, bool, int]:
        return (
            is_seven_string(item) != is_seven_string(product),
            item.specs.get("strings") != product.specs.get("strings"),
            abs(item.price_cents - product.price_cents),
        )

    candidates = repo.search_products(
        ctx.catalog,
        category_id=product.category_id,
        in_stock_only=True,
        limit=repo.MAX_SEARCH_RESULTS,
    )
    candidates = [c for c in candidates if c.product_id != product.product_id]
    candidates.sort(key=similarity)
    return [_summary(ctx, c) for c in candidates[:limit]]


class SearchProductsArgs(BaseModel):
    query: str | None = Field(
        default=None,
        description="Palavras-chave como marca, modelo ou tipo (ex.: 'yamaha nylon'). Não "
        "coloque aqui preço nem adjetivos como 'barato'.",
    )
    category: CategorySlug | None = Field(default=None, description="Filtra por categoria.")
    min_price: float | None = Field(default=None, ge=0, description="Preço mínimo em reais.")
    max_price: float | None = Field(default=None, ge=0, description="Preço máximo em reais.")
    only_available: bool = Field(
        default=False,
        description="Use true para sugerir produtos. Use false para verificar um modelo "
        "específico que pode estar esgotado ou ter saído de linha.",
    )
    limit: int = Field(default=10, ge=1, le=20, description="Máximo de produtos retornados.")


def search_products(ctx: ToolContext, args: SearchProductsArgs) -> dict[str, Any]:
    classification = _classify_accessory_query(args.query) if args.query else None
    if classification == "accessory":
        return {"accessory_request": True, "products": [], "message": MSG_ACCESSORY}

    found = repo.search_products(
        ctx.catalog,
        query=_strip_stopwords(args.query or ""),
        category_id=CATEGORY_IDS[args.category] if args.category else None,
        min_price_cents=_reais_to_cents(args.min_price),
        max_price_cents=_reais_to_cents(args.max_price),
        in_stock_only=args.only_available,
        active_only=args.only_available,
        limit=repo.MAX_SEARCH_RESULTS,
    )
    if not found:
        return {
            "total_matches": 0,
            "products": [],
            "message": MSG_NOT_FOUND,
            "categories_with_products": [
                c.name for c in repo.list_categories(ctx.catalog) if c.product_count > 0
            ],
        }

    # Disponíveis primeiro; a ordem por preço do repositório se mantém em cada grupo.
    found.sort(key=lambda p: _availability(p) != "available")
    result: dict[str, Any] = {
        "total_matches": len(found),
        "products": [_summary(ctx, p) for p in found[: args.limit]],
    }
    if classification == "ambiguous_strings":
        result["accessory_note"] = MSG_AMBIGUOUS_STRINGS
    return result


class GetProductArgs(BaseModel):
    product_id: int = Field(description="ID do produto, obtido em search_products.")


def get_product(ctx: ToolContext, args: GetProductArgs) -> dict[str, Any]:
    product = repo.get_product(ctx.catalog, args.product_id)
    if product is None:
        return {"found": False, "message": MSG_NOT_FOUND}

    data: dict[str, Any] = {"found": True, **_summary(ctx, product)}
    availability = data["availability"]
    if availability == "available":
        data["stock_quantity"] = product.stock_quantity
    else:
        data["guidance"] = AVAILABILITY_GUIDANCE[availability]
        data["alternatives"] = _alternatives(ctx, product)

    if product.product_id in PRODUCTS_WITH_CONFLICTING_DATA:
        data["data_note"] = MSG_CONFLICTING_DATA
    else:
        data["description"] = product.description
        data["specs"] = product.specs
    return data


class ListActivePromotionsArgs(BaseModel):
    pass


def list_active_promotions(ctx: ToolContext, args: ListActivePromotionsArgs) -> dict[str, Any]:
    products = []
    for promotion in repo.list_active_promotions(ctx.catalog):
        product = repo.get_product(ctx.catalog, promotion.product_id)
        if product is not None and _availability(product) == "available":
            products.append(_summary(ctx, product))
    if not products:
        return {"promotions": [], "message": "Não há promoções ativas no momento."}
    return {"promotions": products}


class GetPaymentOptionsArgs(BaseModel):
    product_id: int | None = Field(
        default=None, description="Produto; considera a promoção ativa, se houver."
    )
    quantity: int = Field(default=1, ge=1, le=20, description="Quantidade do produto.")
    amount: float | None = Field(
        default=None,
        gt=0,
        description="Valor total em reais, sem promoção, quando não for um único produto.",
    )

    @model_validator(mode="after")
    def exactly_one_source(self) -> "GetPaymentOptionsArgs":
        if (self.product_id is None) == (self.amount is None):
            raise ValueError("Informe product_id ou amount, nunca os dois")
        return self


def get_payment_options(ctx: ToolContext, args: GetPaymentOptionsArgs) -> dict[str, Any]:
    has_promotion = False
    if args.product_id is not None:
        product = repo.get_product(ctx.catalog, args.product_id)
        if product is None:
            return {"found": False, "message": MSG_NOT_FOUND}
        if _availability(product) != "available":
            return {
                "found": True,
                "available": False,
                "availability": _availability(product),
                "message": "Produto indisponível para compra: não há condições de pagamento.",
            }
        promotion = repo.get_active_promotion(ctx.catalog, product.product_id)
        has_promotion = promotion is not None
        unit_price = (
            apply_discount(product.price_cents, promotion.discount_percent)
            if promotion
            else product.price_cents
        )
        total = unit_price * args.quantity
    else:
        total = _reais_to_cents(args.amount) or 0

    pix_total = total if has_promotion else apply_discount(total, PIX_DISCOUNT_PERCENT)
    options = installment_options(total)
    return {
        "found": True,
        "available": True,
        "total": format_brl(total),
        "pix": {
            "total": format_brl(pix_total),
            "discount_applied": not has_promotion,
            "note": "PIX não tem desconto adicional sobre preço promocional."
            if has_promotion
            else f"{PIX_DISCOUNT_PERCENT}% de desconto.",
        },
        "debit": {"total": format_brl(total)},
        "boleto": {"total": format_brl(total), "note": "Compensação em até 3 dias úteis."},
        "credit_card": {
            "max_installments": options[-1].count,
            "interest_free": True,
            "options": [
                {"installments": o.count, "installment_value": format_brl(o.value_cents)}
                for o in options
            ],
        },
        "combined_payment_allowed": allows_combined_payment(total),
    }
