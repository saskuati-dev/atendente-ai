"""Registro de reclamações (seção 7.3), em um banco separado do catálogo read-only."""

import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from app.db.connection import DATA_DIR

DEFAULT_STATE_DB_PATH = DATA_DIR / "state.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS complaints (
    complaint_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_name TEXT NOT NULL,
    contact       TEXT NOT NULL,
    summary       TEXT NOT NULL,
    order_id      INTEGER,
    created_at    TEXT NOT NULL
) STRICT
"""


class ComplaintStore:
    """Abre uma conexão por operação, para poder ser usada por várias threads."""

    def __init__(self, db_path: Path = DEFAULT_STATE_DB_PATH) -> None:
        self.db_path = db_path

    def add(
        self,
        customer_name: str,
        contact: str,
        summary: str,
        order_id: int | None,
        created_at: datetime,
    ) -> str:
        """Grava a reclamação e devolve o protocolo."""
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute(_SCHEMA)
            cursor = conn.execute(
                """
                INSERT INTO complaints (customer_name, contact, summary, order_id, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (customer_name, contact, summary, order_id, created_at.isoformat()),
            )
            return f"REC-{cursor.lastrowid:06d}"
