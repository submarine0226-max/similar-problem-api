"""모델 호출을 가짜로 바꿔 API 흐름과 제공사별 요청 형식을 검사한다 (키·과금 없음)."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app, get_provider
from app.providers import GenerationError, MissingCredentialsError, make_provider
from app.providers.claude import ClaudeProvider
from app.providers.gemini import GeminiProvider
from app.schemas import (
    DraftProblem,
    GenerationResult,
    SourceProblem,
    VerificationItem,
    VerificationResult,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32

SOURCE = SourceProblem(found=True, text="$x^2-5x+6=0$의 두 근의 합은?", topic="이차방정식", grade_level="중3", reason_if_not_found="")
NOT_FOUND = SourceProblem(found=False, text="", topic="", grade_level="", reason_if_not_found="7번 문제가 없어요")
PROBLEMS = [
    DraftProblem(design="d", problem="$x^2-7x+12=0$의 두 근의 합은?", choices=[], answer="7", solution_steps=["근과 계수의 관계", "7"]),
    DraftProblem(design="d", problem="$x^2-3x+2=0$의 두 근의 합은?", choices=[], answer="4", solution_steps=["잘못된 답"]),
]
GEN = GenerationResult(source=SOURCE, problems=PROBLEMS)
VER = VerificationResult(items=[
    VerificationItem(index=0, work="w", independent_answer="7", is_consistent=True, note=""),
    VerificationItem(index=1, work="w", independent_answer="3", is_consistent=False, note="근의 합은 3"),
])


# ---------- API 흐름 (가짜 제공사) ----------


class FakeProvider:
    name, model = "fake", "fake-model"

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    async def structured(self, **kwargs):
        self.calls.append(kwargs)
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


@pytest.fixture
def api():
    def _make(outputs):
        provider = FakeProvider(outputs)
        app.dependency_overrides[get_provider] = lambda: provider
        return TestClient(app), provider

    yield _make
    app.dependency_overrides.clear()


def test_health():
    assert TestClient(app).get("/health").json() == {"status": "ok"}


def test_generate_image(api):
    http, provider = api([GEN, VER])
    r = http.post("/v1/similar-problems", files={"file": ("p.png", PNG, "image/png")}, data={"count": "2", "difficulty": "harder"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["difficulty"] == "harder" and body["provider"] == "fake"
    assert [p["verification"]["verified"] for p in body["problems"]] == [True, False]
    first = provider.calls[0]
    assert first["media_type"] == "image/png" and first["schema"] is GenerationResult
    assert "2개" in first["text"]
    assert provider.calls[1].get("file_bytes") is None  # 검증 호출엔 파일을 다시 보내지 않음


def test_pdf_problem_not_found(api):
    http, provider = api([GenerationResult(source=NOT_FOUND, problems=[])])
    r = http.post("/v1/similar-problems", files={"file": ("p.pdf", b"%PDF-1.4", "application/pdf")}, data={"problem_number": "7"})
    assert r.status_code == 422 and "7번" in r.json()["detail"]
    assert "PDF에서 7번" in provider.calls[0]["text"]
    assert len(provider.calls) == 1


def test_pdf_requires_problem_number(api):
    http, _ = api([])
    assert http.post("/v1/similar-problems", files={"file": ("p.pdf", b"%PDF", "application/pdf")}).status_code == 422


def test_rejects_unsupported_type(api):
    http, _ = api([])
    assert http.post("/v1/similar-problems", files={"file": ("p.txt", b"hi", "text/plain")}).status_code == 415


def test_provider_error_maps_status(api):
    http, _ = api([GenerationError("모델이 요청을 거부했어요")])
    r = http.post("/v1/similar-problems", files={"file": ("p.png", PNG, "image/png")})
    assert r.status_code == 422 and "거부" in r.json()["detail"]


# ---------- 제공사 선택 ----------


def test_make_provider_auto(monkeypatch):
    for k in ("LLM_PROVIDER", "LLM_MODEL", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(MissingCredentialsError):
        make_provider()
    monkeypatch.setenv("GEMINI_API_KEY", "g-test")
    p = make_provider()
    assert p.name == "gemini" and p.model == "gemini-3.5-flash-lite"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert make_provider().name == "claude"
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("LLM_MODEL", "gemini-3.8-flash")
    p = make_provider()
    assert p.name == "gemini" and p.model == "gemini-3.8-flash"


# ---------- 제공사별 요청 형식 ----------


def test_claude_request_shape():
    calls = []

    async def parse(**kw):
        calls.append(kw)
        return SimpleNamespace(parsed_output=GEN, stop_reason="end_turn", stop_details=None)

    fake = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(parse=parse)))
    out = asyncio.run(ClaudeProvider(client=fake).structured(system="s", text="t", schema=GenerationResult, file_bytes=b"%PDF", media_type="application/pdf"))
    assert out is GEN
    kw = calls[0]
    assert kw["model"] == "claude-opus-5-5" and kw["fallbacks"] == "default"
    assert kw["messages"][0]["content"][0]["type"] == "document"


def test_claude_refusal():
    async def parse(**kw):
        return SimpleNamespace(parsed_output=None, stop_reason="refusal", stop_details=None)

    fake = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(parse=parse)))
    with pytest.raises(GenerationError):
        asyncio.run(ClaudeProvider(client=fake).structured(system="s", text="t", schema=GenerationResult))


def _gemini_fake(text, finish="STOP"):
    calls = []

    async def generate_content(**kw):
        calls.append(kw)
        cand = SimpleNamespace(finish_reason=SimpleNamespace(name=finish))
        return SimpleNamespace(candidates=[cand], text=text)

    client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content)))
    return client, calls


def test_gemini_request_shape():
    client, calls = _gemini_fake(GEN.model_dump_json())
    out = asyncio.run(GeminiProvider(client=client).structured(system="s", text="t", schema=GenerationResult, file_bytes=PNG, media_type="image/png"))
    assert out == GEN
    kw = calls[0]
    assert kw["model"] == "gemini-3.5-flash-lite"
    assert kw["contents"][0].inline_data.mime_type == "image/png"
    assert kw["config"].response_mime_type == "application/json"
    assert kw["config"].system_instruction == "s"


def test_gemini_blocked_and_bad_json():
    client, _ = _gemini_fake("", finish="SAFETY")
    with pytest.raises(GenerationError):
        asyncio.run(GeminiProvider(client=client).structured(system="s", text="t", schema=GenerationResult))
    client, _ = _gemini_fake('{"oops": 1}')
    with pytest.raises(GenerationError):
        asyncio.run(GeminiProvider(client=client).structured(system="s", text="t", schema=GenerationResult))


# ---------- 텍스트 입력 ----------

# problem-extractor의 ProblemOut 그대로 (id, bbox, image_url 같은 필드는 무시돼야 함)
EXTRACTOR_PROBLEM = {
    "id": 12, "source_id": 3, "number": "3", "problem_type": "short_answer", "shared_context": None,
    "stem": "오른쪽 그림과 같이 이차함수 $y=x^2-4$의 그래프가 x축과 만나는 두 점을 A, B라 할 때, 선분 AB의 길이를 구하시오.",
    "choices": [], "answer": None, "solution": None, "topic": "II. 이차방정식 | 2. 근의 공식", "tags": ["이차함수", "x절편"],
    "points": None, "confidence": "high", "notes": None, "reviewed": True, "created_at": "x", "updated_at": "x",
    "bbox": {"x0": 0, "y0": 0, "x1": 1, "y1": 1}, "image_url": "/x.png",
    "figures": [{
        "id": 5, "problem_id": 12, "kind": "graph", "description": "아래로 볼록한 포물선이 x축과 두 점 A, B에서 만남",
        "bbox": {"x0": 0, "y0": 0, "x1": 1, "y1": 1}, "equations": [], "points": [{"label": "A", "coordinates": None}, {"label": "B"}, {"label": "O"}],
        "table_markdown": None, "url": "/f.png",
    }],
}


def test_from_text_accepts_extractor_json(api):
    http, provider = api([GEN, VER])
    r = http.post("/v1/similar-problems/from-text", json={"problem": EXTRACTOR_PROBLEM, "count": 2, "difficulty": "easier"})
    assert r.status_code == 200, r.text
    assert r.json()["difficulty"] == "easier" and len(r.json()["problems"]) == 2
    first = provider.calls[0]
    assert first.get("file_bytes") is None  # 이미지 안 보냄
    text = first["text"]
    assert "$y=x^2-4$" in text and "[그림 1] 종류: graph" in text and "이름 붙은 점: A, B, O" in text
    assert "#이차함수" in text


def test_from_text_plain_choices(api):
    http, provider = api([GEN, VER])
    r = http.post("/v1/similar-problems/from-text", json={"problem": {"stem": "$x^2-5x+6=0$의 두 근의 합은?", "choices": ["① 2", "② 5"], "answer": "②"}})
    assert r.status_code == 200, r.text
    text = provider.calls[0]["text"]
    assert "① 2" in text and "[원본 정답(참고)] ②" in text


def test_from_text_validation(api):
    http, _ = api([])
    assert http.post("/v1/similar-problems/from-text", json={"problem": {"stem": ""}}).status_code == 422
    assert http.post("/v1/similar-problems/from-text", json={"problem": {"stem": "x"}, "count": 50}).status_code == 422
