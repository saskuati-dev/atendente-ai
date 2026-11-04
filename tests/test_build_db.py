"""Integridade do banco gerado a partir dos CSVs."""

import csv
import shutil
import sqlite3
from pathlib import Path

import pytest

from app.db.connection import DATA_DIR
from scripts.build_db import build_database, to_cents

TABLES = ["categories", "products", "customers", "orders", "order_items", "promotions"]


@pytest.mark.parametrize("table", TABLES)
def test_row_count_matches_csv(conn: sqlite3.Connection, table: str) -> None:
    with (DATA_DIR / f"{table}.csv").open(encoding="utf-8", newline="") as file:
        expected = sum(1 for _ in csv.DictReader(file))
    assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == expected


def test_row_counts_are_the_known_ones(conn: sqlite3.Connection) -> None:
    counts = [conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES]
    assert counts == [9, 65, 50, 20, 22, 25]


def test_no_foreign_key_violations(conn: sqlite3.Connection) -> None:
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize(
    ("value", "cents"),
    [("799.9", 79990), ("159.9", 15990), ("11499", 1149900), ("0.07", 7), ("19567.0", 1956700)],
)
def test_to_cents_is_exact(value: str, cents: int) -> None:
    assert to_cents(value) == cents


def test_prices_preserved_in_cents(conn: sqlite3.Connection) -> None:
    with (DATA_DIR / "products.csv").open(encoding="utf-8", newline="") as file:
        expected = {int(r["product_id"]): to_cents(r["price_brl"]) for r in csv.DictReader(file)}
    rows = conn.execute("SELECT product_id, price_cents FROM products").fetchall()
    assert {r["product_id"]: r["price_cents"] for r in rows} == expected


def test_empty_csv_fields_become_null(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT tracking_code, estimated_delivery, notes FROM orders WHERE order_id = 9"
    ).fetchone()
    assert tuple(row) == (None, None, None)


def _copy_csvs(target: Path) -> None:
    for path in DATA_DIR.glob("*.csv"):
        shutil.copy(path, target)


def test_invalid_data_aborts_and_keeps_previous_database(tmp_path: Path) -> None:
    csv_dir = tmp_path / "csv"
    csv_dir.mkdir()
    _copy_csvs(csv_dir)
    output = tmp_path / "out.db"
    build_database(csv_dir, output)

    items = csv_dir / "order_items.csv"
    items.write_text(items.read_text(encoding="utf-8") + "1,1,99999\n", encoding="utf-8")

    with pytest.raises(sqlite3.IntegrityError):
        build_database(csv_dir, output)

    conn = sqlite3.connect(output)
    assert conn.execute("SELECT COUNT(*) FROM order_items").fetchone()[0] == 22
    conn.close()
    assert not output.with_name("out.db.tmp").exists()


def test_two_active_promotions_for_same_product_are_rejected(tmp_path: Path) -> None:
    csv_dir = tmp_path / "csv"
    csv_dir.mkdir()
    _copy_csvs(csv_dir)
    promotions = csv_dir / "promotions.csv"
    promotions.write_text(
        promotions.read_text(encoding="utf-8") + "26,127,5,Duplicada,1\n", encoding="utf-8"
    )

    with pytest.raises(sqlite3.IntegrityError):
        build_database(csv_dir, tmp_path / "out.db")
