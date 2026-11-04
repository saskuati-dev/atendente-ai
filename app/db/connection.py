"""Conexão somente leitura com o banco do catálogo."""

import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_DB_PATH = DATA_DIR / "emporio.db"
SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def connect_readonly(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Abre o banco em modo read-only; falha se o arquivo não existir, em vez de criá-lo."""
    uri = f"{db_path.resolve().as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn
