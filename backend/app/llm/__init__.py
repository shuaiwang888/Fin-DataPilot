"""LLM provider factory. Returns a LangChain BaseChatModel compatible with
the configured provider; defaults to MiniMax-M3 over the OpenAI-compatible API."""
from __future__ import annotations

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from app.config import Settings, get_settings


def build_chat_model(settings: Settings | None = None, **overrides: object) -> BaseChatModel:
    """Build a ChatModel based on settings. Accepts per-call overrides (model, temperature, ...)."""
    s = settings or get_settings()

    common: dict[str, object] = {
        "temperature": s.llm_temperature,
        "max_tokens": s.llm_max_tokens,
        "streaming": True,
    }
    common.update(overrides)

    if s.llm_provider in ("minimax", "openai", "custom"):
        # MiniMax is OpenAI-compatible; ChatOpenAI handles it transparently
        return ChatOpenAI(
            base_url=s.llm_base_url,
            api_key=s.llm_api_key,
            model=s.llm_model,
            **common,
        )
    if s.llm_provider == "anthropic":
        from langchain_anthropic import ChatAnthropic  # type: ignore[import-not-found]

        return ChatAnthropic(
            api_key=s.llm_api_key,
            model=s.llm_model,
            **common,
        )
    raise ValueError(f"Unsupported LLM provider: {s.llm_provider}")


def get_default_chat_model() -> BaseChatModel:
    """Cached default model. Recreated per request when settings change."""
    return build_chat_model()
