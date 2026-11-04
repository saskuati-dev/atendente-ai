"""Histórico das conversas no servidor, para o cliente não poder forjar resultados de tools.

Fica em memória, então vale para um único processo e é perdido ao reiniciar. Para usar vários
workers, basta outra implementação de SessionStore.
"""

import threading
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from app.llm.base import Message, UserMessage

MAX_USER_TURNS = 30


@dataclass
class Session:
    session_id: str
    messages: list[Message] = field(default_factory=list)
    last_used: float = 0.0
    # Impede duas mensagens simultâneas de disputarem o mesmo histórico.
    lock: threading.Lock = field(default_factory=threading.Lock)


class SessionStore(Protocol):
    def create(self) -> Session: ...

    def get(self, session_id: str) -> Session | None: ...

    def append(self, session: Session, messages: Sequence[Message]) -> None: ...


def trim_history(messages: list[Message], max_user_turns: int = MAX_USER_TURNS) -> list[Message]:
    """Mantém os últimos turnos, sempre cortando em uma mensagem do cliente.

    Cortar em outro ponto deixaria resultados de tools sem a chamada correspondente.
    """
    starts = [i for i, message in enumerate(messages) if isinstance(message, UserMessage)]
    if len(starts) <= max_user_turns:
        return messages
    return messages[starts[-max_user_turns] :]


class InMemorySessionStore:
    def __init__(
        self,
        ttl_seconds: float = 3600,
        max_sessions: int = 10_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl_seconds
        self._max_sessions = max_sessions
        self._clock = clock
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def _is_expired(self, session: Session) -> bool:
        return self._clock() - session.last_used > self._ttl

    def _evict(self) -> None:
        for session_id, session in list(self._sessions.items()):
            if self._is_expired(session):
                del self._sessions[session_id]
        while len(self._sessions) >= self._max_sessions:
            oldest = min(self._sessions.values(), key=lambda s: s.last_used)
            del self._sessions[oldest.session_id]

    def create(self) -> Session:
        session = Session(session_id=str(uuid.uuid4()), last_used=self._clock())
        with self._lock:
            self._evict()
            self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str) -> Session | None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            if self._is_expired(session):
                del self._sessions[session_id]
                return None
            session.last_used = self._clock()
            return session

    def append(self, session: Session, messages: Sequence[Message]) -> None:
        with self._lock:
            session.messages = trim_history([*session.messages, *messages])
            session.last_used = self._clock()
