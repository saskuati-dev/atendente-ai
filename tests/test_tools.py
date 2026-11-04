"""Tools e regras de negócio, sem LLM, cobrindo as armadilhas dos dados."""

import json
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.db import repository as repo
from app.db.quality import PRODUCTS_WITH_CONFLICTING_DATA
from app.tools.complaints import ComplaintStore
from app.tools.context import ToolContext
from app.tools.policies import POLICY_TOPICS, load_policies
from app.tools.pricing import (
    allows_combined_payment,
    apply_discount,
    format_brl,
    installment_options,
)
from app.tools.registry import TOOLS, execute_tool, tool_schemas

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


@pytest.fixture
def ctx(conn: sqlite3.Connection, tmp_path: Path) -> Iterator[ToolContext]:
    yield ToolContext(catalog=conn, complaints=ComplaintStore(tmp_path / "state.db"), now=NOW)


def run(ctx: ToolContext, name: str, **args: Any) -> dict[str, Any]:
    return execute_tool(name, args, ctx)


def product_ids(result: dict[str, Any]) -> set[int]:
    return {p["product_id"] for p in result["products"]}


def text_of(result: dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False)


# Preços


@pytest.mark.parametrize(
    ("cents", "expected"),
    [
        (59990, "R$ 599,90"),
        (1956700, "R$ 19.567,00"),
        (5, "R$ 0,05"),
        (100000000, "R$ 1.000.000,00"),
    ],
)
def test_format_brl(cents: int, expected: str) -> None:
    assert format_brl(cents) == expected


def test_apply_discount_rounds_half_cent_up() -> None:
    assert apply_discount(59990, 5) == 56991  # 569,905
    assert apply_discount(599900, 8) == 551908


@pytest.mark.parametrize(
    ("amount", "max_installments"),
    [
        (4000, 1),  # R$ 40: parcela mínima de R$ 50 impede 2x
        (10000, 2),  # R$ 100: 2x de R$ 50
        (15990, 3),  # R$ 159,90: 3x de R$ 53,30
        (34990, 4),  # R$ 349,90: 4x de R$ 87,48; 5x de R$ 69,98 não pode
        (48000, 6),  # R$ 480: 6x de exatamente R$ 80
        (59990, 6),  # R$ 599,90: 7x de R$ 85,70 não pode
        (70000, 7),  # R$ 700: 7x de exatamente R$ 100
        (120000, 12),  # R$ 1.200: 12x de exatamente R$ 100
        (119999, 11),  # um centavo a menos perde a 12x
    ],
)
def test_installment_tiers(amount: int, max_installments: int) -> None:
    options = installment_options(amount)
    assert options[-1].count == max_installments
    assert [o.count for o in options] == list(range(1, max_installments + 1))


def test_combined_payment_only_above_two_thousand() -> None:
    assert not allows_combined_payment(200000)
    assert allows_combined_payment(200001)


def test_payment_with_pix_discount(ctx: ToolContext) -> None:
    result = run(ctx, "get_payment_options", product_id=81)
    assert result["total"] == "R$ 599,90"
    assert result["pix"] == {
        "total": "R$ 569,91",
        "discount_applied": True,
        "note": "5% de desconto.",
    }
    assert result["credit_card"]["max_installments"] == 6
    assert result["combined_payment_allowed"] is False


def test_pix_discount_does_not_stack_with_promotion(ctx: ToolContext) -> None:
    result = run(ctx, "get_payment_options", product_id=94)
    assert result["total"] == "R$ 5.519,08"  # R$ 5.999,00 com 8%
    assert result["pix"]["total"] == "R$ 5.519,08"
    assert result["pix"]["discount_applied"] is False
    assert result["combined_payment_allowed"] is True


def test_payment_multiplies_quantity(ctx: ToolContext) -> None:
    assert run(ctx, "get_payment_options", product_id=81, quantity=2)["total"] == "R$ 1.199,80"


def test_payment_for_amount(ctx: ToolContext) -> None:
    result = run(ctx, "get_payment_options", amount=2500)
    assert result["pix"]["total"] == "R$ 2.375,00"
    assert result["credit_card"]["max_installments"] == 12


@pytest.mark.parametrize("product_id", [96, 113, 130, 136])
def test_no_payment_options_for_unavailable_products(ctx: ToolContext, product_id: int) -> None:
    result = run(ctx, "get_payment_options", product_id=product_id)
    assert result["available"] is False
    assert "credit_card" not in result


@pytest.mark.parametrize("args", [{}, {"product_id": 81, "amount": 100}])
def test_payment_requires_exactly_one_source(ctx: ToolContext, args: dict[str, Any]) -> None:
    assert "error" in execute_tool("get_payment_options", args, ctx)


# Busca


@pytest.mark.parametrize(
    "query",
    [
        "cordas",
        "cordas para violão",
        "encordoamento de nylon",
        "palheta",
        "Pedal pra guitarra",
        "amplificador",
        "case de violão",
        "cabo",
    ],
)
def test_accessory_requests_do_not_offer_instruments(ctx: ToolContext, query: str) -> None:
    result = run(ctx, "search_products", query=query)
    assert result["accessory_request"] is True
    assert result["products"] == []
    assert "não vende acessórios" in result["message"]


@pytest.mark.parametrize(
    "query", ["violão 7 cordas", "7 cordas", "violão com afinador", "violão com pré-amplificador"]
)
def test_instrument_features_are_not_accessory_requests(ctx: ToolContext, query: str) -> None:
    result = run(ctx, "search_products", query=query)
    assert "accessory_request" not in result
    assert result["products"]


@pytest.mark.parametrize(
    "query", ["cordas de aço para violão", "corda de nylon para ukulele", "encordoamento de aço"]
)
def test_buying_strings_is_an_accessory_request(ctx: ToolContext, query: str) -> None:
    result = run(ctx, "search_products", query=query)
    assert result["accessory_request"] is True
    assert result["products"] == []


@pytest.mark.parametrize(
    ("query", "strings", "total"),
    [
        ("cordas de aço", "steel", 19),
        ("cordas de nylon", "nylon", 31),
        ("corda de aço", "steel", 19),
    ],
)
def test_string_type_shows_instruments_and_warns_about_loose_strings(
    ctx: ToolContext, conn: sqlite3.Connection, query: str, strings: str, total: int
) -> None:
    result = run(ctx, "search_products", query=query, limit=20)
    assert "accessory_request" not in result
    assert result["total_matches"] == total
    assert "não vende cordas avulsas" in result["accessory_note"]
    for found in result["products"]:
        product = repo.get_product(conn, found["product_id"])
        assert product is not None
        assert product.specs["strings"] == strings


@pytest.mark.parametrize(
    "query", ["violão com cordas de aço", "violão de aço", "com cordas de aço", "violão 7 cordas"]
)
def test_instrument_with_string_feature_has_no_warning(ctx: ToolContext, query: str) -> None:
    result = run(ctx, "search_products", query=query)
    assert result["products"]
    assert "accessory_note" not in result
    assert "accessory_request" not in result


def test_steel_guitars_without_the_word_in_text_are_found(ctx: ToolContext) -> None:
    found = product_ids(run(ctx, "search_products", query="violão com cordas de aço", limit=20))
    assert set(range(91, 98)) <= found
    assert not found & {98, 109, 113}


def test_punctuation_only_query_returns_nothing(ctx: ToolContext) -> None:
    assert run(ctx, "search_products", query="%")["products"] == []


def test_stopwords_do_not_empty_the_search(ctx: ToolContext) -> None:
    assert run(ctx, "search_products", query="violão para iniciantes")["products"]
    assert run(ctx, "search_products", query="um violão barato")["products"]


def test_search_filters_and_reports_total(ctx: ToolContext) -> None:
    result = run(
        ctx, "search_products", category="violoes", max_price=700, only_available=True, limit=2
    )
    assert result["total_matches"] > len(result["products"]) == 2
    assert f"2 de {result['total_matches']} produtos" in result["more_results_note"]
    assert all(p["category"] == "Violões" for p in result["products"])


def test_min_price_finds_high_end_products(ctx: ToolContext) -> None:
    result = run(ctx, "search_products", min_price=15000)
    assert product_ids(result) == {131, 143}


@pytest.mark.parametrize("category", ["sopros_madeiras", "sopros_metais", "cordas_orquestrais"])
def test_empty_categories_are_reported_as_empty(ctx: ToolContext, category: str) -> None:
    result = run(ctx, "search_products", category=category)
    assert result["total_matches"] == 0
    assert "não cite produtos" in result["message"]


@pytest.mark.parametrize("query", ["violino", "flauta", "saxofone", "trompete"])
def test_instruments_outside_current_catalog_are_not_found(ctx: ToolContext, query: str) -> None:
    assert run(ctx, "search_products", query=query)["products"] == []


def test_only_available_hides_unsellable_products(ctx: ToolContext) -> None:
    result = run(ctx, "search_products", only_available=True, limit=20)
    assert result["total_matches"] == 61  # 62 ativos, menos o 96, que está sem estoque
    assert all(p["availability"] == "available" for p in result["products"])
    for model in ["Giannini GF-3D", "Shelby SN-7C", "Kala KA-ABP", "Ibanez Bass 2X"]:
        assert run(ctx, "search_products", query=model, only_available=True)["products"] == []


def test_named_lookup_finds_discontinued_product_flagged(ctx: ToolContext) -> None:
    result = run(ctx, "search_products", query="Shelby SN-7C")
    assert [(p["product_id"], p["availability"]) for p in result["products"]] == [
        (113, "discontinued")
    ]
    assert "current_price" not in result["products"][0]


def test_available_products_come_first(ctx: ToolContext) -> None:
    availability = [
        p["availability"]
        for p in run(ctx, "search_products", query="7 cordas", limit=20)["products"]
    ]
    assert availability[0] == "available"
    assert availability[-1] == "discontinued"


# Produto e disponibilidade


def test_alternatives_prefer_the_same_kind_of_instrument(ctx: ToolContext) -> None:
    seven_string = run(ctx, "get_product", product_id=113)["alternatives"]
    assert all("7 Cordas" in a["name"] for a in seven_string)
    steel = run(ctx, "get_product", product_id=96)["alternatives"]
    assert not any("Nylon" in a["name"] for a in steel)


def test_empty_search_lists_only_categories_with_products(ctx: ToolContext) -> None:
    categories = run(ctx, "search_products", query="violino")["categories_with_products"]
    assert "Violões" in categories
    assert "Cordas Orquestrais" not in categories
    assert not any("Sopro" in c for c in categories)


def test_tracking_note_does_not_assume_a_carrier(ctx: ToolContext, order_8_phone: str) -> None:
    order = run(ctx, "get_order", order_id=8, phone=order_8_phone)["orders"][0]
    assert "Correios ou da Jadlog" in order["tracking_note"]
    assert "order_date" not in order
    assert order["placed_on"] == "2026-02-15"


def test_available_product_with_details(ctx: ToolContext) -> None:
    result = run(ctx, "get_product", product_id=81)
    assert result["availability"] == "available"
    assert result["current_price"] == "R$ 599,90"
    assert result["promotion"] is None
    assert result["stock_quantity"] == 12
    assert result["specs"]["strings"] == "nylon"
    assert "alternatives" not in result


def test_promotion_shows_original_price_and_percent(ctx: ToolContext) -> None:
    assert run(ctx, "get_product", product_id=90)["promotion"] == {
        "name": "Desconto Exclusivo",
        "discount_percent": 18,
        "original_price": "R$ 2.399,00",
        "promotional_price": "R$ 1.967,18",
    }


@pytest.mark.parametrize("product_id", [93, 99])
def test_inactive_promotions_do_not_change_price(ctx: ToolContext, product_id: int) -> None:
    result = run(ctx, "get_product", product_id=product_id)
    assert result["promotion"] is None


def test_out_of_stock_suggests_available_alternatives(ctx: ToolContext) -> None:
    result = run(ctx, "get_product", product_id=96)
    assert result["availability"] == "out_of_stock"
    assert "stock_quantity" not in result
    alternatives = result["alternatives"]
    assert len(alternatives) == 3
    assert all(a["availability"] == "available" for a in alternatives)
    assert 96 not in {a["product_id"] for a in alternatives}


@pytest.mark.parametrize(
    ("product_id", "availability"),
    [(113, "discontinued"), (136, "discontinued"), (130, "coming_soon")],
)
def test_unsellable_products_hide_price_and_stock(
    ctx: ToolContext, product_id: int, availability: str
) -> None:
    result = run(ctx, "get_product", product_id=product_id)
    assert result["availability"] == availability
    assert "current_price" not in result
    assert "stock_quantity" not in result
    assert result["alternatives"]


@pytest.mark.parametrize("product_id", sorted(PRODUCTS_WITH_CONFLICTING_DATA))
def test_conflicting_products_never_expose_description_or_specs(
    ctx: ToolContext, conn: sqlite3.Connection, product_id: int
) -> None:
    result = run(ctx, "get_product", product_id=product_id)
    product = repo.get_product(conn, product_id)
    assert product is not None
    assert "description" not in result
    assert "specs" not in result
    assert "em revisão" in result["data_note"]
    assert result["name"] == product.name
    assert result["current_price"] == format_brl(product.price_cents)
    assert product.description not in text_of(result)


def test_missing_product(ctx: ToolContext) -> None:
    assert run(ctx, "get_product", product_id=1)["found"] is False


# Promoções


def test_only_active_promotions_are_listed(ctx: ToolContext) -> None:
    result = run(ctx, "list_active_promotions")
    assert {p["product_id"] for p in result["promotions"]} == {90, 94, 121, 127}
    assert all(p["promotion"]["original_price"] for p in result["promotions"])
    assert "Black Friday" not in text_of(result)


# Pedidos


@pytest.fixture
def order_8_phone(conn: sqlite3.Connection) -> str:
    customer = repo.get_customer(conn, 2)
    assert customer is not None
    return customer.phone


@pytest.mark.parametrize(
    "args",
    [
        {"order_id": 8},
        {},
        {"order_id": 8, "phone": ""},
        {"order_id": 8, "phone": "(11) 90000-0000"},
        {"order_id": 8, "email": "outra.pessoa@jmail.com"},
    ],
)
def test_order_requires_matching_contact(ctx: ToolContext, args: dict[str, Any]) -> None:
    result = execute_tool("get_order", args, ctx)
    assert result["verified"] is False
    assert "orders" not in result
    assert "BRJL5544332BR" not in text_of(result)


def test_wrong_contact_and_missing_order_are_indistinguishable(
    ctx: ToolContext, order_8_phone: str
) -> None:
    wrong_contact = run(ctx, "get_order", order_id=7, phone=order_8_phone)
    missing_order = run(ctx, "get_order", order_id=999, phone=order_8_phone)
    assert wrong_contact == missing_order


def test_order_with_phone_in_any_format(ctx: ToolContext) -> None:
    for phone in ["(67) 99654-7821", "67996547821", "+55 67 99654-7821"]:
        result = run(ctx, "get_order", order_id=8, phone=phone)
        assert result["verified"] is True
        assert result["orders"][0]["tracking_code"] == "BRJL5544332BR"


def test_order_with_email_ignores_case(ctx: ToolContext) -> None:
    result = run(ctx, "get_order", order_id=8, email=" AnaCarol.Ferreira@COLDMAIL.com ")
    assert result["verified"] is True


def test_order_view_does_not_expose_personal_data(ctx: ToolContext, order_8_phone: str) -> None:
    result = run(ctx, "get_order", order_id=8, phone=order_8_phone)
    assert result["customer_first_name"] == "Ana"
    text = text_of(result)
    assert "Ferreira" not in text
    assert "coldmail" not in text
    assert "7821" not in text
    assert "Campo Grande" not in text


def test_shared_email_needs_order_number(ctx: ToolContext) -> None:
    result = run(ctx, "get_order", email="bruno.martins@jmail.com")
    assert result["verified"] is False
    assert "E-mail sozinho" in result["message"]
    # Com o número, o e-mail compartilhado identifica o pedido do cliente 11.
    assert run(ctx, "get_order", order_id=10, email="bruno.martins@jmail.com")["verified"] is True


def test_customer_with_shared_email_does_not_access_order_by_own_phone(
    ctx: ToolContext, conn: sqlite3.Connection
) -> None:
    other_customer = repo.get_customer(conn, 43)
    assert other_customer is not None
    assert run(ctx, "get_order", order_id=10, phone=other_customer.phone)["verified"] is False


def test_phone_only_lists_customer_orders(ctx: ToolContext, conn: sqlite3.Connection) -> None:
    customer = repo.get_customer(conn, 3)
    assert customer is not None
    result = run(ctx, "get_order", phone=customer.phone)
    assert [o["order_id"] for o in result["orders"]] == [19, 1]
    assert run(ctx, "get_order", phone="(11) 90000-0000")["verified"] is False


def test_order_reports_registered_total(ctx: ToolContext, conn: sqlite3.Connection) -> None:
    customer = repo.get_customer(conn, 12)
    assert customer is not None
    order = run(ctx, "get_order", order_id=3, phone=customer.phone)["orders"][0]
    assert order["total"] == "R$ 3.450,00"  # o catálogo atual daria R$ 3.498,00
    assert order["items"][0]["quantity"] == 2


def test_overdue_shipment_is_flagged(ctx: ToolContext, order_8_phone: str) -> None:
    order = run(ctx, "get_order", order_id=8, phone=order_8_phone)["orders"][0]
    assert order["status"] == "Enviado"
    assert order["delivery_overdue"] is True
    assert "Não prometa nova data" in order["guidance"]


def test_shipment_within_deadline_is_not_flagged(
    conn: sqlite3.Connection, tmp_path: Path, order_8_phone: str
) -> None:
    ctx = ToolContext(
        catalog=conn,
        complaints=ComplaintStore(tmp_path / "state.db"),
        now=datetime(2026, 2, 28, 12, 0, tzinfo=UTC),
    )
    order = run(ctx, "get_order", order_id=8, phone=order_8_phone)["orders"][0]
    assert "delivery_overdue" not in order


def test_today_uses_store_timezone(conn: sqlite3.Connection, tmp_path: Path) -> None:
    # 01:00 UTC ainda é o dia anterior em Campo Grande (UTC-4).
    ctx = ToolContext(
        catalog=conn,
        complaints=ComplaintStore(tmp_path / "state.db"),
        now=datetime(2026, 3, 1, 1, 0, tzinfo=UTC),
    )
    assert ctx.today.isoformat() == "2026-02-28"


def test_cancelled_order_shows_reason(ctx: ToolContext, conn: sqlite3.Connection) -> None:
    customer = repo.get_customer(conn, 17)
    assert customer is not None
    order = run(ctx, "get_order", order_id=18, phone=customer.phone)["orders"][0]
    assert order["status"] == "Cancelado"
    assert order["notes"] == "Item fora de estoque no momento do pedido"


# Políticas


def test_every_topic_has_a_section_and_nothing_extra() -> None:
    assert set(load_policies()) == set(POLICY_TOPICS)


@pytest.mark.parametrize(
    ("topic", "expected"),
    [
        ("horario", "09:00 às 13:00"),
        ("pagamento", "parcela mínima de R$ 80,00"),
        ("trocas", "Boquilhas"),
        ("trocas", "30 dias corridos"),
        ("frete", "Frete grátis para pedidos acima de R$ 500,00"),
        ("promocoes", "não indica que uma campanha esteja ativa"),
        ("garantia", "90 dias"),
        ("privacidade", "LGPD"),
        ("sobre", "Telefone e WhatsApp: (67) 3341-4444"),
    ],
)
def test_policy_content(ctx: ToolContext, topic: str, expected: str) -> None:
    assert expected in run(ctx, "get_policy", topic=topic)["content"]


def test_policy_sections_do_not_leak_into_each_other(ctx: ToolContext) -> None:
    content = run(ctx, "get_policy", topic="horario")["content"]
    assert "PAC" not in content
    assert "curadoria" not in content.lower()


def test_contradictions_are_resolved() -> None:
    text = "\n".join(load_policies().values())
    assert "empóriodamusica" not in text
    assert "Parcela mínima de R$ 100,00" not in text
    assert "3321-4500" not in text


def test_invalid_policy_topic(ctx: ToolContext) -> None:
    assert "error" in run(ctx, "get_policy", topic="receita de bolo")


# Reclamações


def test_register_complaint_persists_and_returns_protocol(ctx: ToolContext) -> None:
    first = run(
        ctx,
        "register_complaint",
        customer_name="Ana",
        contact="(67) 99654-7821",
        summary="Pedido atrasado",
        order_id=8,
    )
    second = run(ctx, "register_complaint", customer_name="Beto", contact="b@x.com", summary="Ruim")
    assert first == {
        "registered": True,
        "protocol": "REC-000001",
        "response_deadline": "24 horas úteis",
    }
    assert second["protocol"] == "REC-000002"

    state = sqlite3.connect(ctx.complaints.db_path)
    rows = state.execute("SELECT customer_name, order_id FROM complaints ORDER BY 1").fetchall()
    state.close()
    assert rows == [("Ana", 8), ("Beto", None)]


def test_complaint_with_injection_attempt_is_stored_as_text(ctx: ToolContext) -> None:
    malicious = "'); DROP TABLE complaints; --"
    run(ctx, "register_complaint", customer_name="Ana", contact="x", summary=malicious)
    state = sqlite3.connect(ctx.complaints.db_path)
    assert state.execute("SELECT summary FROM complaints").fetchall() == [(malicious,)]
    state.close()


def test_complaint_requires_fields(ctx: ToolContext) -> None:
    assert "error" in run(ctx, "register_complaint", customer_name="", contact="x", summary="y")


# Registro


def test_every_tool_has_a_schema() -> None:
    schemas = {s["name"]: s for s in tool_schemas()}
    assert set(schemas) == {
        "search_products",
        "get_product",
        "list_active_promotions",
        "get_payment_options",
        "get_order",
        "get_policy",
        "register_complaint",
    }
    assert set(schemas) == set(TOOLS)
    assert all(s["description"] and s["parameters"]["type"] == "object" for s in schemas.values())


def test_policy_schema_lists_all_topics() -> None:
    schema = next(s for s in tool_schemas() if s["name"] == "get_policy")
    assert schema["parameters"]["properties"]["topic"]["enum"] == list(POLICY_TOPICS)


def test_schemas_have_no_titles() -> None:
    assert '"title"' not in json.dumps(tool_schemas())


def test_unknown_tool_returns_error(ctx: ToolContext) -> None:
    assert run(ctx, "drop_database")["error"] == "Tool desconhecida: drop_database"


def test_invalid_arguments_return_error(ctx: ToolContext) -> None:
    assert "Argumentos inválidos" in run(ctx, "get_product", product_id="abc")["error"]


def test_database_failure_returns_error_without_data(tmp_path: Path, db_path: Path) -> None:
    broken = sqlite3.connect(db_path)
    broken.close()
    ctx = ToolContext(catalog=broken, complaints=ComplaintStore(tmp_path / "s.db"), now=NOW)
    result = run(ctx, "get_product", product_id=81)
    assert "Sistema indisponível" in result["error"]


def test_complete_search_has_no_more_results_note(ctx: ToolContext) -> None:
    result = run(ctx, "search_products", category="violoes", max_price=1000, limit=20)
    assert len(result["products"]) == result["total_matches"]
    assert "more_results_note" not in result
