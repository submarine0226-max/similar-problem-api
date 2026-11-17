"""환경 변수로 제공사를 고른다.

LLM_PROVIDER=claude | gemini  (비우면 설정된 키로 자동 선택: ANTHROPIC_API_KEY → claude, GEMINI_API_KEY → gemini)
LLM_MODEL=...                 (비우면 제공사별 기본 모델)
"""

import os

from .base import (
    GenerationError,
    MissingCredentialsError,
    Provider,
    ProviderError,
    RateLimitedError,
    UpstreamError,
)

__all__ = [
    "GenerationError",
    "MissingCredentialsError",
    "Provider",
    "ProviderError",
    "RateLimitedError",
    "UpstreamError",
    "make_provider",
]


def make_provider() -> Provider:
    name = os.environ.get("LLM_PROVIDER", "").strip().lower()
    model = os.environ.get("LLM_MODEL", "").strip() or None
    if not name:
        if os.environ.get("ANTHROPIC_API_KEY"):
            name = "claude"
        elif os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
            name = "gemini"
        else:
            raise MissingCredentialsError("API 키가 없어요. ANTHROPIC_API_KEY 또는 GEMINI_API_KEY를 설정해 주세요.")

    if name == "claude":
        from .claude import ClaudeProvider

        return ClaudeProvider(model=model)
    if name == "gemini":
        from .gemini import GeminiProvider

        return GeminiProvider(model=model)
    raise MissingCredentialsError(f"알 수 없는 LLM_PROVIDER예요: {name} (claude 또는 gemini)")
