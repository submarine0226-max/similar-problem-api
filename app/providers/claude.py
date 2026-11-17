"""Claude(Anthropic) 제공사. 키: ANTHROPIC_API_KEY"""

import base64

import anthropic

from .base import GenerationError, MissingCredentialsError, RateLimitedError, T, UpstreamError

DEFAULT_MODEL = "claude-opus-5-5"
BETAS = ["server-side-fallback-2026-07-01"]


class ClaudeProvider:
    name = "claude"

    def __init__(self, model: str | None = None, client: anthropic.AsyncAnthropic | None = None):
        self.model = model or DEFAULT_MODEL
        self.client = client or anthropic.AsyncAnthropic()

    async def structured(self, *, system, text, schema: type[T], file_bytes=None, media_type=None) -> T:
        content: list[dict] = []
        if file_bytes is not None:
            block_type = "document" if media_type == "application/pdf" else "image"
            data = base64.standard_b64encode(file_bytes).decode("utf-8")
            content.append({"type": block_type, "source": {"type": "base64", "media_type": media_type, "data": data}})
        content.append({"type": "text", "text": text})

        try:
            response = await self.client.beta.messages.parse(
                model=self.model,
                max_tokens=16000,
                betas=BETAS,
                fallbacks="default",
                output_config={"effort": "high"},
                system=system,
                messages=[{"role": "user", "content": content}],
                output_format=schema,
            )
        except anthropic.AuthenticationError as e:
            raise MissingCredentialsError("Claude API 인증에 실패했어요. ANTHROPIC_API_KEY를 확인해 주세요.") from e
        except anthropic.RateLimitError as e:
            raise RateLimitedError("Claude 요청 한도에 걸렸어요. 잠시 후 다시 시도해 주세요.") from e
        except anthropic.BadRequestError as e:
            raise UpstreamError(f"Claude 요청 오류: {e.message}") from e
        except anthropic.APIStatusError as e:
            raise UpstreamError(f"Claude API 오류 ({e.status_code})") from e
        except anthropic.APIConnectionError as e:
            raise UpstreamError("Claude API에 연결하지 못했어요.") from e

        if response.stop_reason == "refusal":
            category = response.stop_details.category if response.stop_details else None
            raise GenerationError(f"모델이 요청을 거부했어요 (category={category})")
        if response.stop_reason == "max_tokens":
            raise GenerationError("응답이 길어서 잘렸어요. 문제 개수를 줄여 다시 시도해 주세요.")
        if response.parsed_output is None:
            raise GenerationError("모델 응답을 해석하지 못했어요.")
        return response.parsed_output
