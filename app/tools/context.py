"""Dependências recebidas pelas tools."""

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.tools.complaints import ComplaintStore

STORE_TIMEZONE = ZoneInfo("America/Campo_Grande")


@dataclass(frozen=True)
class ToolContext:
    catalog: sqlite3.Connection
    complaints: ComplaintStore
    now: datetime  # com fuso horário; injetado para que os testes controlem o relógio

    @property
    def today(self) -> date:
        return self.now.astimezone(STORE_TIMEZONE).date()
