"""모델 제공사(Claude/Gemini) 공통 인터페이스와 오류."""

from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class ProviderError(Exception):
    """제공사 호출 실패의 공통 부모. status는 API가 돌려줄 HTTP 코드."""

    status = 502


class GenerationError(ProviderError):
    """원본 문제를 읽지 못했거나, 모델이 거부했거나, 응답을 해석하지 못한 경우."""

    status = 422


class MissingCredentialsError(ProviderError):
    status = 500


class RateLimitedError(ProviderError):
    status = 429


class UpstreamError(ProviderError):
    status = 502


class ServiceBusyError(ProviderError):
    """모델 서버 일시 과부하. 재시도 후에도 실패한 경우."""

    status = 503


class Provider(Protocol):
    name: str
    model: str

    async def structured(
        self,
        *,
        system: str,
        text: str,
        schema: type[T],
        file_bytes: bytes | None = None,
        media_type: str | None = None,
    ) -> T:
        """시스템 프롬프트 + (선택) 파일 + 텍스트를 보내고 schema 형태로 검증된 결과를 돌려준다."""
        ...
