"""유사 문제 생성 API."""

from dotenv import load_dotenv

load_dotenv()  # 프로젝트 폴더의 .env에서 API 키를 읽는다 (이미 설정된 환경 변수가 우선)

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile

from .generator import IMAGE_TYPES, PDF_TYPE, generate_from_text, generate_similar_problems
from .providers import Provider, ProviderError, make_provider
from .schemas import Difficulty, GenerateResponse, TextGenerateRequest

MAX_FILE_BYTES = 20 * 1024 * 1024  # 20MB
MAX_COUNT = 10

app = FastAPI(title="Similar Problem API", version="0.2.0")

_provider: Provider | None = None


def get_provider() -> Provider:
    global _provider
    if _provider is None:
        try:
            _provider = make_provider()
        except ProviderError as e:
            raise HTTPException(e.status, str(e))
    return _provider


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/v1/similar-problems", response_model=GenerateResponse)
async def create_similar_problems(
    file: UploadFile = File(..., description="문제 이미지(png/jpeg/webp/gif) 또는 PDF"),
    count: int = Form(3, ge=1, le=MAX_COUNT, description="만들 유사 문제 개수"),
    difficulty: Difficulty = Form(Difficulty.same, description="easier | same | harder"),
    problem_number: str | None = Form(None, description="PDF(또는 여러 문제가 있는 이미지)에서 처리할 문제 번호"),
    provider: Provider = Depends(get_provider),
) -> GenerateResponse:
    media_type = (file.content_type or "").lower()
    if media_type == "image/jpg":
        media_type = "image/jpeg"
    if media_type not in IMAGE_TYPES | {PDF_TYPE}:
        raise HTTPException(415, f"지원하지 않는 파일 형식이에요: {file.content_type}")
    if media_type == PDF_TYPE and not (problem_number and problem_number.strip()):
        raise HTTPException(422, "PDF는 problem_number(문제 번호)를 지정해 주세요.")

    data = await file.read()
    if not data:
        raise HTTPException(400, "빈 파일이에요.")
    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(413, "파일이 너무 커요 (최대 20MB).")

    try:
        return await generate_similar_problems(
            provider, data, media_type, count, difficulty, problem_number.strip() if problem_number else None
        )
    except ProviderError as e:
        raise HTTPException(e.status, str(e))


@app.post("/v1/similar-problems/from-text", response_model=GenerateResponse)
async def create_similar_problems_from_text(
    body: TextGenerateRequest,
    provider: Provider = Depends(get_provider),
) -> GenerateResponse:
    """이미 추출된 문제(텍스트)로 생성. problem-extractor의 문제 JSON(ProblemOut)을 problem에 그대로 넣어도 된다."""
    try:
        return await generate_from_text(provider, body.problem, body.count, body.difficulty)
    except ProviderError as e:
        raise HTTPException(e.status, str(e))
