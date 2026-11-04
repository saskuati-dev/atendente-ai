"""Consultas read-only e as armadilhas dos dados que dependem delas."""

import sqlite3
from pathlib import Path

import pytest

from app.db import repository as repo
from app.db.connection import connect_readonly
from app.db.text import normalize_phone, normalize_text

VIOLOES = 5


def ids(products: list[repo.Product]) -> set[int]:
    return {p.product_id for p in products}


# Conexão


def test_connection_rejects_writes(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("UPDATE products SET price_cents = 1")


def test_connection_does_not_create_missing_database(tmp_path: Path) -> None:
    missing = tmp_path / "missing.db"
    with pytest.raises(sqlite3.OperationalError):
        connect_readonly(missing)
    assert not missing.exists()


def test_connection_accepts_path_with_accents_and_spaces(tmp_path: Path, db_path: Path) -> None:
    target = tmp_path / "políticas da loja.db"
    target.write_bytes(db_path.read_bytes())
    conn = connect_readonly(target)
    assert repo.get_product(conn, 81) is not None
    conn.close()


# Normalização


@pytest.mark.parametrize(
    ("raw", "expected"), [("Violão  AÇO", "violao aco"), ("Órgão", "orgao"), ("  ", "")]
)
def test_normalize_text(raw: str, expected: str) -> None:
    assert normalize_text(raw) == expected


@pytest.mark.parametrize(
    "raw", ["(67) 99812-3456", "67998123456", "+55 67 99812-3456", "5567998123456"]
)
def test_normalize_phone(raw: str) -> None:
    assert normalize_phone(raw) == "67998123456"


# Busca


@pytest.mark.parametrize("query", ["violão", "violao", "VIOLÃO", "violões", "violoes"])
def test_search_finds_whole_category_regardless_of_accent_or_plural(
    conn: sqlite3.Connection, query: str
) -> None:
    found = repo.search_products(conn, query=query, active_only=False, limit=50)
    in_category = {p.product_id for p in found if p.category_id == VIOLOES}
    outside_category = {p.product_id for p in found if p.category_id != VIOLOES}
    assert len(in_category) == 35
    # O ukulele barítono 128 cita "violão" (no singular) na descrição: correspondência legítima.
    assert outside_category <= {128}


def test_search_requires_all_terms(conn: sqlite3.Connection) -> None:
    found = repo.search_products(conn, query="yamaha nylon", limit=50)
    assert found
    assert all("yamaha" in p.name.lower() for p in found)
    assert 81 in ids(found)
    assert 87 not in ids(found)  # Yamaha F310 é de aço


def test_search_is_safe_against_like_wildcards_and_sql(conn: sqlite3.Connection) -> None:
    # Pontuação não forma palavras: nem casa com o catálogo inteiro, nem vira curinga do LIKE.
    assert repo.search_products(conn, query="%", active_only=False) == []
    assert repo.search_products(conn, query="_", active_only=False) == []
    assert repo.search_products(conn, query="'; DROP TABLE products; --") == []
    assert repo.get_product(conn, 81) is not None


def test_search_filters_by_price_and_sorts_ascending(conn: sqlite3.Connection) -> None:
    found = repo.search_products(conn, max_price_cents=50000, limit=50)
    prices = [p.price_cents for p in found]
    assert found
    assert max(prices) <= 50000
    assert prices == sorted(prices)


def test_search_limit_is_capped(conn: sqlite3.Connection) -> None:
    assert len(repo.search_products(conn, limit=repo.MAX_SEARCH_RESULTS + 1)) == 62  # ativos
    assert len(repo.search_products(conn, limit=0)) == 1


@pytest.mark.parametrize(
    ("query", "expected"),
    [("fender", {134, 145}), ("tama", set()), ("jazz bass", set()), ("nord", {143})],
)
def test_search_ignores_conflicting_descriptions(
    conn: sqlite3.Connection, query: str, expected: set[int]
) -> None:
    # O 135 ("Music Man") é descrito como Fender Jazz Bass e o 140 ("Pearl") como Tama.
    assert ids(repo.search_products(conn, query=query, active_only=False, limit=50)) == expected


@pytest.mark.parametrize("accessory", ["palheta", "pedal", "cabo", "case"])
def test_accessories_are_not_in_catalog(conn: sqlite3.Connection, accessory: str) -> None:
    assert repo.search_products(conn, query=accessory, active_only=False) == []


def test_amplifier_search_only_matches_instruments_with_preamp(conn: sqlite3.Connection) -> None:
    # Falso positivo conhecido: a descrição cita "pré-amplificador". A tool da Fase 2 precisa
    # impedir que esses instrumentos sejam oferecidos a quem pediu um amplificador.
    found = repo.search_products(conn, query="amplificador", active_only=False, limit=50)
    assert ids(found) == {99, 105, 122}


def test_seven_string_guitars_are_instruments(conn: sqlite3.Connection) -> None:
    # A busca por "cordas" acha violões de 7 cordas; cabe às tools e ao prompt explicar
    # que a loja não vende cordas avulsas.
    found = repo.search_products(conn, query="7 cordas", active_only=False)
    assert 113 in ids(found)
    assert {p.category_id for p in found} == {VIOLOES}


def steel_or_nylon(conn: sqlite3.Connection, value: str) -> set[int]:
    products = repo.search_products(conn, active_only=False, limit=repo.MAX_SEARCH_RESULTS)
    return {p.product_id for p in products if p.specs.get("strings") == value}


@pytest.mark.parametrize("query", ["aco", "aço", "AÇO"])
def test_steel_search_matches_specs_not_parts_of_words(
    conn: sqlite3.Connection, query: str
) -> None:
    found = ids(repo.search_products(conn, query=query, active_only=False, limit=100))
    assert found == steel_or_nylon(conn, "steel")
    assert len(found) == 19
    assert set(range(91, 98)) <= found  # aço só nos specs
    assert not found & {98, 109, 115, 132, 136}  # "braço" e "eletroacústico" não contam


def test_nylon_search_matches_specs(conn: sqlite3.Connection) -> None:
    found = ids(repo.search_products(conn, query="nylon", active_only=False, limit=100))
    assert found == steel_or_nylon(conn, "nylon")
    assert len(found) == 31


def test_number_of_strings_is_a_whole_word(conn: sqlite3.Connection) -> None:
    def search(query: str) -> set[int]:
        return ids(repo.search_products(conn, query=query, active_only=False, limit=100))

    assert search("6 cordas") == {131, 132, 133, 134, 145}  # não casa com "650mm"
    assert search("7 cordas") == {109, 110, 111, 112, 113}
    assert search("12 cordas") == set()


def test_singular_and_plural_find_the_same_products(conn: sqlite3.Connection) -> None:
    singular = repo.search_products(conn, query="iniciante", limit=100)
    assert singular
    assert singular == repo.search_products(conn, query="iniciantes", limit=100)


@pytest.mark.parametrize(
    ("query", "product_id"), [("SN-7C", 113), ("sn 7c", 113), ("KA-ABP-CTG", 130)]
)
def test_models_with_hyphen(conn: sqlite3.Connection, query: str, product_id: int) -> None:
    assert ids(repo.search_products(conn, query=query, active_only=False)) == {product_id}


# Status e estoque


def test_active_without_stock_is_excluded_only_by_stock_filter(conn: sqlite3.Connection) -> None:
    giannini = "giannini gf-3d"
    assert ids(repo.search_products(conn, query=giannini)) == {96}
    assert repo.search_products(conn, query=giannini, in_stock_only=True) == []


@pytest.mark.parametrize(
    ("product_id", "status", "stock"),
    [(113, "discontinued", 0), (136, "discontinued", 13), (130, "coming_soon", 2)],
)
def test_unsellable_products(
    conn: sqlite3.Connection, product_id: int, status: str, stock: int
) -> None:
    product = repo.get_product(conn, product_id)
    assert product is not None
    assert (product.status, product.stock_quantity) == (status, stock)
    # Fora da busca padrão, mesmo com estoque; acessível com active_only=False.
    assert product_id not in ids(repo.search_products(conn, limit=50, in_stock_only=True))
    found = repo.search_products(conn, query=product.name, active_only=False)
    assert product_id in ids(found)


def test_get_product_parses_specs_and_category(conn: sqlite3.Connection) -> None:
    product = repo.get_product(conn, 81)
    assert product is not None
    assert product.price_cents == 59990
    assert product.category_name == "Violões"
    assert product.specs["strings"] == "nylon"


def test_get_missing_product_returns_none(conn: sqlite3.Connection) -> None:
    assert repo.get_product(conn, 1) is None


def test_categories_without_products(conn: sqlite3.Connection) -> None:
    counts = {c.category_id: c.product_count for c in repo.list_categories(conn)}
    assert len(counts) == 9
    assert counts[6] == counts[7] == counts[8] == 0
    assert counts[VIOLOES] > 0


# Promoções


def test_only_four_promotions_are_active(conn: sqlite3.Connection) -> None:
    active = {p.product_id: p.discount_percent for p in repo.list_active_promotions(conn)}
    assert active == {90: 18, 94: 8, 121: 20, 127: 10}


@pytest.mark.parametrize("product_id", [93, 96, 99, 102, 91])
def test_inactive_promotions_are_ignored(conn: sqlite3.Connection, product_id: int) -> None:
    assert repo.get_active_promotion(conn, product_id) is None


def test_active_promotion_for_product(conn: sqlite3.Connection) -> None:
    promotion = repo.get_active_promotion(conn, 94)
    assert promotion is not None
    assert (promotion.discount_percent, promotion.description) == (8, "Desconto Exclusivo")


# Clientes e pedidos


def test_find_customer_by_phone_in_any_format(conn: sqlite3.Connection) -> None:
    for phone in ["(67) 99812-3456", "67998123456", "+55 67 99812-3456"]:
        customer = repo.find_customer_by_phone(conn, phone)
        assert customer is not None
        assert customer.customer_id == 1


def test_unknown_phone_finds_nobody(conn: sqlite3.Connection) -> None:
    assert repo.find_customer_by_phone(conn, "(11) 90000-0000") is None


def test_customers_sharing_email_stay_distinct(conn: sqlite3.Connection) -> None:
    first, second = repo.get_customer(conn, 11), repo.get_customer(conn, 43)
    assert first is not None and second is not None
    assert first.email == second.email == "bruno.martins@jmail.com"
    assert first.phone != second.phone


def test_order_keeps_registered_total_even_when_it_differs_from_catalog(
    conn: sqlite3.Connection,
) -> None:
    order = repo.get_order(conn, 3)
    items = repo.get_order_items(conn, 3)
    assert order is not None
    assert order.total_cents == 345000
    assert [(i.product_id, i.quantity) for i in items] == [(130, 2)]
    product = repo.get_product(conn, 130)
    assert product is not None
    assert product.price_cents * 2 == 349800


def test_order_with_multiple_items(conn: sqlite3.Connection) -> None:
    assert {i.product_id for i in repo.get_order_items(conn, 15)} == {82, 106}


def test_shipped_order_with_tracking(conn: sqlite3.Connection) -> None:
    order = repo.get_order(conn, 8)
    assert order is not None
    assert order.status == "shipped"
    assert order.tracking_code == "BRJL5544332BR"
    assert order.estimated_delivery == "2026-02-28"


def test_cancelled_order_has_reason(conn: sqlite3.Connection) -> None:
    order = repo.get_order(conn, 18)
    assert order is not None
    assert order.status == "cancelled"
    assert order.notes == "Item fora de estoque no momento do pedido"


def test_orders_by_customer_most_recent_first(conn: sqlite3.Connection) -> None:
    orders = repo.list_orders_by_customer(conn, 3)
    assert [o.order_id for o in orders] == [19, 1]


def test_missing_order_returns_none(conn: sqlite3.Connection) -> None:
    assert repo.get_order(conn, 999) is None
