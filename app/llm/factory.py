"""Criação dos provedores a partir do nome, da chave do cliente (BYOK) e do modelo."""

from app.llm.anthropic_provider import AnthropicProvider
from app.llm.base import LLMProvider
from app.llm.deepseek_provider import DeepSeekProvider
from app.llm.gemini_provider import GeminiProvider
from app.llm.openai_provider import OpenAIProvider

PROVIDERS = {
    "anthropic": AnthropicProvider,
    "gemini": GeminiProvider,
    "openai": OpenAIProvider,
    "deepseek": DeepSeekProvider,
}
PROVIDER_ALIASES = {"google": "gemini", "chatgpt": "openai", "claude": "anthropic"}

# Modelos de bom custo-benefício para atendimento, conferidos em novembro de 2026.
DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-5-5",
    "gemini": "gemini-3.6-flash",
    "openai": "gpt-6-luna",
    "deepseek": "deepseek-flash",
}


class UnknownProviderError(ValueError):
    pass


def resolve_provider_name(name: str) -> str:
    normalized = name.strip().lower()
    normalized = PROVIDER_ALIASES.get(normalized, normalized)
    if normalized not in PROVIDERS:
        raise UnknownProviderError(f"Provedor desconhecido. Opções: {', '.join(PROVIDERS)}")
    return normalized


def create_provider(name: str, api_key: str, model: str | None = None) -> LLMProvider:
    resolved = resolve_provider_name(name)
    return PROVIDERS[resolved](api_key=api_key, model=model or DEFAULT_MODELS[resolved])
