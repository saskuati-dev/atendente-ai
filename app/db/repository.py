"""Consultas parametrizadas ao catálogo. Sem regras de negócio: isso fica nas tools (Fase 2).

Valores monetários estão sempre em centavos.
"""

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from app.db.text import normalize_phone, search_tokens

MAX_SEARCH_RESULTS = 100


@dataclass(frozen=True)
class Category:
    category_id: int
    name: str
    description: str
    product_count: int


@dataclass(frozen=True)
class Product:
    product_id: int
    name: str
    category_id: int
    category_name: str
    description: str
    price_cents: int
    stock_quantity: int
    status: str
    specs: dict[str, Any]


@dataclass(frozen=True)
class Promotion:
    promotion_id: int
    product_id: int
    discount_percent: int
    description: str


@dataclass(frozen=True)
class Customer:
    customer_id: int
    name: str
    phone: str
    email: str
    city: str


@dataclass(frozen=True)
class Order:
    order_id: int
    customer_id: int
    order_date: str
    status: str
    total_cents: int
    payment_method: str
    tracking_code: str | None
    estimated_delivery: str | None
    notes: str | None


@dataclass(frozen=True)
class OrderItem:
    product_id: int
    product_name: str
    quantity: int


_PRODUCT_SELECT = """
    SELECT p.product_id, p.name, p.category_id, c.name AS category_name, p.description,
           p.price_cents, p.stock_quantity, p.status, p.specs
    FROM products p
    JOIN categories c ON c.category_id = p.category_id
"""


def _to_product(row: sqlite3.Row) -> Product:
    return Product(**{**dict(row), "specs": json.loads(row["specs"])})


def _word_variants(token: str) -> list[str]:
    """Singular e plural com ou sem "s": "iniciante" também acha "iniciantes" e vice-versa."""
    variants = [token, f"{token}s"]
    if token.endswith("s") and len(token) > 3:
        variants.append(token[:-1])
    return variants


def list_categories(conn: sqlite3.Connection) -> list[Category]:
    rows = conn.execute(
        """
        SELECT c.category_id, c.name, c.description, COUNT(p.product_id) AS product_count
        FROM categories c
        LEFT JOIN products p ON p.category_id = c.category_id AND p.status = 'active'
        GROUP BY c.category_id
        ORDER BY c.category_id
        """
    ).fetchall()
    return [Category(**dict(row)) for row in rows]


def search_products(
    conn: sqlite3.Connection,
    query: str | None = None,
    category_id: int | None = None,
    min_price_cents: int | None = None,
    max_price_cents: int | None = None,
    in_stock_only: bool = False,
    active_only: bool = True,
    limit: int = 10,
) -> list[Product]:
    """Busca produtos, do mais barato ao mais caro.

    Todos os termos de `query` devem aparecer como palavras inteiras: "aco" não acha "braço".
    Uma busca preenchida, mas sem nenhuma palavra (ex.: "%"), não retorna produtos.
    """
    conditions: list[str] = []
    params: list[Any] = []

    tokens = search_tokens(query or "")
    if query and query.strip() and not tokens:
        return []
    for token in tokens:
        variants = _word_variants(token)
        conditions.append(
            "(" + " OR ".join(["' ' || p.search_text || ' ' LIKE ?"] * len(variants)) + ")"
        )
        params.extend(f"% {variant} %" for variant in variants)
    if category_id is not None:
        conditions.append("p.category_id = ?")
        params.append(category_id)
    if min_price_cents is not None:
        conditions.append("p.price_cents >= ?")
        params.append(min_price_cents)
    if max_price_cents is not None:
        conditions.append("p.price_cents <= ?")
        params.append(max_price_cents)
    if in_stock_only:
        conditions.append("p.stock_quantity > 0")
    if active_only:
        conditions.append("p.status = 'active'")

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(max(1, min(limit, MAX_SEARCH_RESULTS)))
    rows = conn.execute(
        f"{_PRODUCT_SELECT} {where} ORDER BY p.price_cents, p.product_id LIMIT ?", params
    ).fetchall()
    return [_to_product(row) for row in rows]


def get_product(conn: sqlite3.Connection, product_id: int) -> Product | None:
    row = conn.execute(f"{_PRODUCT_SELECT} WHERE p.product_id = ?", (product_id,)).fetchone()
    return _to_product(row) if row else None


def get_active_promotion(conn: sqlite3.Connection, product_id: int) -> Promotion | None:
    row = conn.execute(
        """
        SELECT promotion_id, product_id, discount_percent, description
        FROM promotions
        WHERE product_id = ? AND is_active = 1
        """,
        (product_id,),
    ).fetchone()
    return Promotion(**dict(row)) if row else None


def list_active_promotions(conn: sqlite3.Connection) -> list[Promotion]:
    rows = conn.execute(
        """
        SELECT promotion_id, product_id, discount_percent, description
        FROM promotions
        WHERE is_active = 1
        ORDER BY discount_percent DESC, promotion_id
        """
    ).fetchall()
    return [Promotion(**dict(row)) for row in rows]


def get_customer(conn: sqlite3.Connection, customer_id: int) -> Customer | None:
    row = conn.execute(
        "SELECT customer_id, name, phone, email, city FROM customers WHERE customer_id = ?",
        (customer_id,),
    ).fetchone()
    return Customer(**dict(row)) if row else None


def find_customer_by_phone(conn: sqlite3.Connection, phone: str) -> Customer | None:
    row = conn.execute(
        "SELECT customer_id, name, phone, email, city FROM customers WHERE phone_digits = ?",
        (normalize_phone(phone),),
    ).fetchone()
    return Customer(**dict(row)) if row else None


def get_order(conn: sqlite3.Connection, order_id: int) -> Order | None:
    """Retorna o pedido sem verificar identidade: a verificação é da tool `get_order` (Fase 2)."""
    row = conn.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
    return Order(**dict(row)) if row else None


def list_orders_by_customer(conn: sqlite3.Connection, customer_id: int) -> list[Order]:
    rows = conn.execute(
        "SELECT * FROM orders WHERE customer_id = ? ORDER BY order_date DESC, order_id DESC",
        (customer_id,),
    ).fetchall()
    return [Order(**dict(row)) for row in rows]


def get_order_items(conn: sqlite3.Connection, order_id: int) -> list[OrderItem]:
    rows = conn.execute(
        """
        SELECT oi.product_id, p.name AS product_name, oi.quantity
        FROM order_items oi
        JOIN products p ON p.product_id = oi.product_id
        WHERE oi.order_id = ?
        ORDER BY oi.product_id
        """,
        (order_id,),
    ).fetchall()
    return [OrderItem(**dict(row)) for row in rows]
