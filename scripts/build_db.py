"""Gera o banco SQLite do catálogo a partir de data/*.csv.

Uso: uv run python scripts/build_db.py [--csv-dir DIR] [--output ARQUIVO]

O banco é montado em um arquivo temporário e só substitui o anterior se a carga
inteira for válida, então uma falha nunca deixa um banco pela metade.
"""

import argparse
import csv
import json
import sqlite3
from decimal import Decimal
from pathlib import Path

from app.db.connection import DATA_DIR, DEFAULT_DB_PATH, SCHEMA_PATH
from app.db.quality import PRODUCTS_WITH_CONFLICTING_DATA
from app.db.text import normalize_phone, search_tokens

# Formas singulares e sinônimos que os clientes usam, mas que não aparecem em todos os
# produtos da categoria. A busca compara palavras, então "violao" não acharia "violoes".
CATEGORY_SEARCH_TERMS = {
    1: "guitarra",
    2: "baixo contrabaixo",
    3: "bateria percussao",
    4: "teclado piano",
    5: "violao",
    9: "ukulele",
}

# O tipo de corda nem sempre está no nome ou na descrição: os produtos 91 a 97 são de aço só
# nos specs. Os valores numéricos de "strings" (guitarras e baixos) viram "N cordas".
STRING_TYPE_SEARCH_TERMS = {"steel": "aco cordas", "nylon": "nylon cordas"}


def strings_search_terms(specs: dict[str, object]) -> str:
    strings = str(specs.get("strings", ""))
    if strings.isdigit():
        return f"{strings} cordas"
    return STRING_TYPE_SEARCH_TERMS.get(strings, "")


def read_csv(csv_dir: Path, name: str) -> list[dict[str, str]]:
    with (csv_dir / f"{name}.csv").open(encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def to_cents(value: str) -> int:
    """Converte reais em centavos via Decimal: float("799.9") * 100 não dá 79990 exato."""
    return int(Decimal(value) * 100)


def empty_to_none(value: str) -> str | None:
    return value.strip() or None


def build_search_text(product: dict[str, str], category: dict[str, str]) -> str:
    parts: list[str] = [
        product["name"],
        category["name"],
        CATEGORY_SEARCH_TERMS.get(int(category["category_id"]), ""),
    ]
    if int(product["product_id"]) not in PRODUCTS_WITH_CONFLICTING_DATA:
        specs = json.loads(product["specs"])
        parts.append(product["description"])
        parts.extend(str(value) for value in specs.values())
        parts.append(strings_search_terms(specs))
    return " ".join(search_tokens(" ".join(parts)))


def load(conn: sqlite3.Connection, csv_dir: Path) -> None:
    categories = read_csv(csv_dir, "categories")
    categories_by_id = {row["category_id"]: row for row in categories}

    conn.executemany(
        "INSERT INTO categories VALUES (?, ?, ?)",
        [(int(r["category_id"]), r["name"], r["description"]) for r in categories],
    )
    conn.executemany(
        "INSERT INTO products VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                int(r["product_id"]),
                r["name"],
                int(r["category_id"]),
                r["description"],
                to_cents(r["price_brl"]),
                int(r["stock_quantity"]),
                r["status"],
                r["specs"],
                r["created_at"],
                build_search_text(r, categories_by_id[r["category_id"]]),
            )
            for r in read_csv(csv_dir, "products")
        ],
    )
    conn.executemany(
        "INSERT INTO customers VALUES (?, ?, ?, ?, ?, ?)",
        [
            (
                int(r["customer_id"]),
                r["name"],
                r["phone"],
                normalize_phone(r["phone"]),
                r["email"].strip().casefold(),
                r["city"],
            )
            for r in read_csv(csv_dir, "customers")
        ],
    )
    conn.executemany(
        "INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                int(r["order_id"]),
                int(r["customer_id"]),
                r["order_date"],
                r["status"],
                to_cents(r["total_brl"]),
                r["payment_method"],
                empty_to_none(r["tracking_code"]),
                empty_to_none(r["estimated_delivery"]),
                empty_to_none(r["notes"]),
            )
            for r in read_csv(csv_dir, "orders")
        ],
    )
    conn.executemany(
        "INSERT INTO order_items VALUES (?, ?, ?)",
        [
            (int(r["order_id"]), int(r["product_id"]), int(r["quantity"]))
            for r in read_csv(csv_dir, "order_items")
        ],
    )
    conn.executemany(
        "INSERT INTO promotions VALUES (?, ?, ?, ?, ?)",
        [
            (
                int(r["promotion_id"]),
                int(r["product_id"]),
                int(r["discount_percent"]),
                r["description"],
                int(r["is_active"]),
            )
            for r in read_csv(csv_dir, "promotions")
        ],
    )


def build_database(csv_dir: Path = DATA_DIR, output: Path = DEFAULT_DB_PATH) -> Path:
    tmp_path = output.with_name(f"{output.name}.tmp")
    tmp_path.unlink(missing_ok=True)
    try:
        conn = sqlite3.connect(tmp_path)
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
            with conn:
                load(conn, csv_dir)
        finally:
            conn.close()
        tmp_path.replace(output)
    finally:
        tmp_path.unlink(missing_ok=True)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--csv-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()
    print(f"Banco gerado em {build_database(args.csv_dir, args.output)}")


if __name__ == "__main__":
    main()
