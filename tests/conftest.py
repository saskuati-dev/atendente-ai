import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.db.connection import connect_readonly
from scripts.build_db import build_database


@pytest.fixture(scope="session")
def db_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Banco gerado dos CSVs reais em um diretório temporário, sem depender de data/emporio.db."""
    return build_database(output=tmp_path_factory.mktemp("db") / "test.db")


@pytest.fixture
def conn(db_path: Path) -> Iterator[sqlite3.Connection]:
    connection = connect_readonly(db_path)
    yield connection
    connection.close()
