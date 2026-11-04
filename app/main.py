"""API do agente de atendimento. Execução: uv run uvicorn app.main:app"""

import logging
import sqlite3
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field

from app.agent.loop import run_agent
from app.api.sessions import InMemorySessionStore, SessionStore
from app.config import Settings
from app.db.connection import connect_readonly
from app.llm.base import LLMAuthError, LLMError, LLMProvider, LLMRateLimitError
from app.llm.factory import (
    DEFAULT_MODELS,
    PROVIDER_ALIASES,
    PROVIDERS,
    UnknownProviderError,
    create_provider,
    resolve_provider_name,
)
from app.tools.complaints import ComplaintStore
from app.tools.context import ToolContext

logger = logging.getLogger(__name__)

ProviderFactory = Callable[[str, str, str | None], LLMProvider]


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000, description="Mensagem do cliente.")
    session_id: str | None = Field(
        default=None,
        description="Omita na primeira mensagem e reenvie o valor retornado nas seguintes.",
    )


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    provider: str
    model: str
    tools_used: list[str] = Field(description="Tools chamadas neste turno, em ordem.")


class ProviderInfo(BaseModel):
    name: str
    default_model: str
    aliases: list[str]
    server_key_configured: bool


class ProvidersResponse(BaseModel):
    default_provider: str
    providers: list[ProviderInfo]


def create_app(
    settings: Settings | None = None,
    sessions: SessionStore | None = None,
    provider_factory: ProviderFactory = create_provider,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> FastAPI:
    config = settings or Settings()
    store = sessions or InMemorySessionStore(config.session_ttl_seconds, config.max_sessions)
    complaints = ComplaintStore(config.state_db_path)
    app = FastAPI(
        title="Atendente AI — Empório da Música",
        description="Agente de atendimento por mensagens, com BYOK de provedores de LLM.",
    )

    def build_provider(name: str | None, key: str | None, model: str | None) -> LLMProvider:
        """Resolve o provedor: os headers têm prioridade sobre a configuração do servidor."""
        requested = name.strip() if name and name.strip() else config.llm_provider
        try:
            resolved = resolve_provider_name(requested)
        except UnknownProviderError as error:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error

        api_key = key.strip() if key and key.strip() else config.server_key(resolved)
        if not api_key:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                f"Informe a chave de {resolved} no header X-LLM-Api-Key.",
            )
        if not model and resolved == resolve_provider_name(config.llm_provider):
            model = config.llm_model
        return provider_factory(resolved, api_key, model or None)

    # Rota síncrona: o FastAPI a executa em uma thread, e os SDKs e o SQLite são síncronos.
    @app.post("/chat", response_model=ChatResponse)
    def chat(
        request: ChatRequest,
        x_llm_provider: Annotated[str | None, Header()] = None,
        x_llm_api_key: Annotated[str | None, Header()] = None,
        x_llm_model: Annotated[str | None, Header()] = None,
    ) -> ChatResponse:
        if not request.message.strip():
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Mensagem vazia.")
        provider = build_provider(x_llm_provider, x_llm_api_key, x_llm_model)

        session = store.get(request.session_id) if request.session_id else store.create()
        if session is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                "Sessão inexistente ou expirada. Envie a mensagem sem session_id para iniciar.",
            )
        if not session.lock.acquire(blocking=False):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "A mensagem anterior desta sessão ainda está em andamento.",
            )
        try:
            with closing(connect_readonly(config.catalog_db_path)) as catalog:
                ctx = ToolContext(catalog=catalog, complaints=complaints, now=clock())
                reply = run_agent(provider, session.messages, request.message, ctx)
            store.append(session, reply.messages)
        except LLMAuthError as error:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(error)) from error
        except LLMRateLimitError as error:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(error)) from error
        except LLMError as error:
            logger.warning("Falha do provedor %s: %s", provider.name, error)
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(error)) from error
        except sqlite3.OperationalError as error:
            logger.exception("Catálogo indisponível")
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "Catálogo indisponível."
            ) from error
        finally:
            session.lock.release()

        return ChatResponse(
            session_id=session.session_id,
            reply=reply.text,
            provider=provider.name,
            model=provider.model,
            tools_used=[tool.name for tool in reply.tool_trace],
        )

    @app.get("/health")
    def health() -> dict[str, str]:
        try:
            with closing(connect_readonly(config.catalog_db_path)) as catalog:
                catalog.execute("SELECT 1 FROM products LIMIT 1").fetchone()
        except sqlite3.Error as error:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Catálogo indisponível. Gere o banco com: uv run python scripts/build_db.py",
            ) from error
        return {"status": "ok"}

    @app.get("/providers", response_model=ProvidersResponse)
    def providers() -> ProvidersResponse:
        return ProvidersResponse(
            default_provider=config.llm_provider,
            providers=[
                ProviderInfo(
                    name=name,
                    default_model=DEFAULT_MODELS[name],
                    aliases=[a for a, target in PROVIDER_ALIASES.items() if target == name],
                    server_key_configured=config.server_key(name) is not None,
                )
                for name in PROVIDERS
            ],
        )

    return app


app = create_app()
