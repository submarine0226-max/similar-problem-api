"""Gemini(Google AI Studio) 제공사. 무료 등급 키 사용 가능. 키: GEMINI_API_KEY (또는 GOOGLE_API_KEY)

무료 등급에서는 보낸 데이터를 Google이 서비스 개선에 쓸 수 있고, 분당·하루 요청 한도가 있어요.
"""

import os

from google import genai
from google.genai import errors, types
from pydantic import ValidationError

from .base import GenerationError, MissingCredentialsError, RateLimitedError, ServiceBusyError, T, UpstreamError

# problem-extractor와 같은 기본값: 무료 등급 하루 한도가 넉넉한 Flash-Lite.
# 수학 정확도가 아쉬우면 LLM_MODEL=gemini-3.8-flash
DEFAULT_MODEL = "gemini-3.5-flash-lite"

BLOCKED = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "RECITATION", "SPII"}


class GeminiProvider:
    name = "gemini"

    def __init__(self, model: str | None = None, client: genai.Client | None = None):
        self.model = model or DEFAULT_MODEL
        if client is None:
            key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
            if not key:
                raise MissingCredentialsError("GEMINI_API_KEY가 설정되지 않았어요.")
            # Gemini는 사용자가 몰릴 때 503(과부하)을 자주 돌려줘서, 일시적 서버 오류는 자동으로 몇 번 재시도한다.
            retry = types.HttpRetryOptions(
                attempts=5, initial_delay=2.0, max_delay=30.0, exp_base=2.0, http_status_codes=[500, 502, 503, 504]
            )
            client = genai.Client(api_key=key, http_options=types.HttpOptions(retry_options=retry))
        self.client = client

    async def structured(self, *, system, text, schema: type[T], file_bytes=None, media_type=None) -> T:
        contents: list = []
        if file_bytes is not None:
            contents.append(types.Part.from_bytes(data=file_bytes, mime_type=media_type))
        contents.append(text)

        try:
            response = await self.client.aio.models.generate_content(
                model=self.model,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    response_mime_type="application/json",
                    response_json_schema=schema.model_json_schema(),
                    max_output_tokens=32768,
                    # 수학 출제·검산은 생각할수록 정확해져서 thinking을 높게 둔다.
                    thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.HIGH),
                ),
            )
        except errors.ClientError as e:
            if e.code == 429:
                raise RateLimitedError("Gemini 요청 한도에 걸렸어요 (무료 등급은 분당·하루 한도가 있어요).") from e
            if e.code in (401, 403) or (e.code == 400 and "API key" in str(e)):
                raise MissingCredentialsError("Gemini API 키가 잘못됐어요. GEMINI_API_KEY를 확인해 주세요.") from e
            if e.code == 404:
                raise UpstreamError(f"Gemini 모델 '{self.model}'을 찾을 수 없어요. LLM_MODEL을 확인해 주세요.") from e
            raise UpstreamError(f"Gemini 요청 오류 ({e.code}): {e.message}") from e
        except errors.ServerError as e:
            if e.code == 503:
                raise ServiceBusyError("Gemini 서버가 지금 바빠요 (503). 잠시 후 다시 시도해 주세요.") from e
            raise UpstreamError(f"Gemini 서버 오류 ({e.code}): {e.message}") from e
        except errors.APIError as e:
            raise UpstreamError(f"Gemini API 오류 ({e.code}): {e.message}") from e

        candidate = (response.candidates or [None])[0]
        finish = getattr(candidate, "finish_reason", None)
        finish_name = getattr(finish, "name", str(finish))
        if finish_name == "MAX_TOKENS":
            raise GenerationError("응답이 길어서 잘렸어요. 문제 개수를 줄여 다시 시도해 주세요.")
        if finish_name in BLOCKED:
            raise GenerationError(f"모델이 요청을 거부했어요 ({finish_name}).")
        if not response.text:
            raise GenerationError("모델이 빈 응답을 돌려줬어요.")
        try:
            return schema.model_validate_json(response.text)
        except ValidationError as e:
            raise GenerationError("모델 응답이 스키마와 맞지 않아요. 다시 시도해 주세요.") from e
