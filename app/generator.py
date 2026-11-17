"""원본 문제 인식 → 유사 문제 생성 → 정답 검증. 모델 호출은 providers가 맡는다."""

import json

from .providers import GenerationError, Provider
from .schemas import (
    Difficulty,
    GenerateResponse,
    GenerationResult,
    ProblemInput,
    SimilarProblem,
    Verification,
    VerificationResult,
)

IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
PDF_TYPE = "application/pdf"

DIFFICULTY_TEXT = {
    Difficulty.easier: "원본보다 한 단계 쉽게 (계산량을 줄이거나 단계 수를 줄임)",
    Difficulty.same: "원본과 같은 난이도로 (같은 개념·같은 풀이 단계 수, 숫자와 상황만 바꿈)",
    Difficulty.harder: "원본보다 한 단계 어렵게 (조건을 하나 더하거나 개념을 한 번 더 응용)",
}

GENERATE_SYSTEM = """당신은 초중고 수학 문제 출제 전문가입니다.
첨부된 이미지·PDF 또는 텍스트로 주어진 원본 수학 문제를 정확히 읽고, 같은 개념을 평가하는 유사 문제를 만듭니다.

규칙:
- 원본 문제를 먼저 그대로 옮겨 적고(source.text), 단원/유형과 학년을 추정합니다.
- 텍스트로 받은 원본에 [그림] 정보가 있으면 그것이 원본 그림의 내용입니다. 유사 문제에서는 그림 없이도 풀 수 있게 필요한 조건을 글로 씁니다.
- 유사 문제는 원본과 같은 개념·유형을 다루되 숫자, 상황, 표현을 바꿉니다. 원본을 그대로 베끼지 않습니다.
- 원본이 객관식이면 유사 문제도 객관식(보기 5개), 주관식이면 주관식으로 만듭니다.
- 모든 문제는 정답이 하나로 정해져야 하고, 정답이 깔끔하게 떨어져야 합니다.
- 문제마다 design에서 먼저 정답을 정하고, 그 정답이 나오도록 계수·조건을 역산합니다.
  예: 중근 조건 문제에서 정답 k=3을 원하면, 판별식이 (k-3)(k+a)=0 꼴이 되도록 상수항을 정합니다.
  design에서 계산을 끝까지 검산해 맞는 것을 확인한 뒤에만 problem을 씁니다.
- problem을 쓴 뒤에는 절대 바꾸지 않습니다. 풀이(solution_steps)는 problem에 적힌 식 그대로 풀고,
  "식을 수정합니다", "다시 설정합니다" 같은 말이 나오면 안 됩니다.
- 객관식이면 정답이 보기 중 정확히 하나에만 있어야 합니다.
- 그림이 꼭 필요한 문제라도 그림은 만들지 말고, 필요한 정보를 모두 글로 설명합니다.
- 수식은 LaTeX로 $...$ 안에 씁니다. 문장은 한국어로 씁니다.
- 요청한 원본 문제를 찾을 수 없거나 수학 문제가 아니면 source.found를 false로 하고 problems는 빈 배열로 둡니다."""

VERIFY_SYSTEM = """당신은 수학 문제 검수자입니다.
받은 문제 하나하나마다 items에 정확히 하나씩, 같은 index로 결과를 적습니다 (빠뜨리지 않습니다).
각 문제를 주어진 정답을 보기 전에 work에 직접 끝까지 계산하고, 그 답을 independent_answer에 적습니다.
그다음 주어진 정답과 수학적으로 같은지(표기 차이는 같다고 봄) 판단해 is_consistent에 적습니다.
문제 조건이 모순되거나, 정답이 하나로 정해지지 않거나, 객관식에서 정답이 보기에 없으면 is_consistent를 false로 합니다.
note에는 불일치 이유를 한국어 한두 문장으로 적고, 문제가 없으면 빈 문자열로 둡니다."""


def render_problem(p: ProblemInput) -> str:
    """텍스트 입력을 모델에게 보낼 원본 문제 설명으로 바꾼다."""
    lines: list[str] = []
    if p.topic or p.tags:
        lines.append(f"[단원] {p.topic or ''} {' '.join('#' + t for t in p.tags)}".strip())
    if p.shared_context:
        lines.append(f"[공통 지문] {p.shared_context}")
    lines.append(f"[발문] {p.stem}")
    if p.choices:
        lines.append("[보기]")
        for i, c in enumerate(p.choices, 1):
            if isinstance(c, str):
                lines.append(f"  {c}")
            else:
                lines.append(f"  {c.label or f'({i})'} {c.text}")
    for i, f in enumerate(p.figures, 1):
        lines.append(f"[그림 {i}] 종류: {f.kind}. {f.description}")
        if f.equations:
            lines.append(f"  그림 속 식: {', '.join(f.equations)}")
        if f.points:
            pts = ", ".join(pt.label + (f"{pt.coordinates}" if pt.coordinates else "") for pt in f.points)
            lines.append(f"  이름 붙은 점: {pts}")
        if f.table_markdown:
            lines.append(f"  표:\n{f.table_markdown}")
    if p.answer:
        lines.append(f"[원본 정답(참고)] {p.answer}")
    if p.solution:
        lines.append(f"[원본 해설(참고)] {p.solution}")
    return "\n".join(lines)


async def generate_similar_problems(
    provider: Provider,
    file_bytes: bytes,
    media_type: str,
    count: int,
    difficulty: Difficulty,
    problem_number: str | None = None,
) -> GenerateResponse:
    """이미지·PDF 업로드로 생성."""
    if problem_number:
        where = "PDF" if media_type == PDF_TYPE else "이미지"
        target = f"{where}에서 {problem_number}번 문제"
    else:
        target = "첨부한 문제"
    instruction = (
        f"{target}를 원본으로 삼아 유사 문제 {count}개를 만들어 주세요.\n"
        f"난이도: {DIFFICULTY_TEXT[difficulty]}"
    )
    return await _generate_and_verify(provider, instruction, count, difficulty, file_bytes, media_type)


async def generate_from_text(
    provider: Provider, problem: ProblemInput, count: int, difficulty: Difficulty
) -> GenerateResponse:
    """이미 추출된 문제(텍스트)로 생성. 이미지를 보내지 않는다."""
    instruction = (
        f"아래 원본 문제를 바탕으로 유사 문제 {count}개를 만들어 주세요.\n"
        f"난이도: {DIFFICULTY_TEXT[difficulty]}\n\n"
        f"<원본 문제>\n{render_problem(problem)}\n</원본 문제>"
    )
    return await _generate_and_verify(provider, instruction, count, difficulty)


async def _generate_and_verify(
    provider: Provider,
    instruction: str,
    count: int,
    difficulty: Difficulty,
    file_bytes: bytes | None = None,
    media_type: str | None = None,
) -> GenerateResponse:
    generated = await provider.structured(
        system=GENERATE_SYSTEM,
        text=instruction,
        schema=GenerationResult,
        file_bytes=file_bytes,
        media_type=media_type,
    )
    if not generated.source.found:
        raise GenerationError(generated.source.reason_if_not_found or "원본 문제를 찾지 못했어요.")
    problems = [p.to_public() for p in generated.problems[:count]]
    if not problems:
        raise GenerationError("유사 문제를 만들지 못했어요. 다시 시도해 주세요.")

    payload = [
        {"index": i, "problem": p.problem, "choices": p.choices, "given_answer": p.answer}
        for i, p in enumerate(problems)
    ]
    verification = await provider.structured(
        system=VERIFY_SYSTEM,
        text="다음 문제들을 검증해 주세요.\n" + json.dumps(payload, ensure_ascii=False, indent=2),
        schema=VerificationResult,
    )
    by_index = {item.index: item for item in verification.items}

    results = []
    for i, p in enumerate(problems):
        item = by_index.get(i)
        check = (
            Verification(verified=item.is_consistent, independent_answer=item.independent_answer, note=item.note)
            if item
            else Verification(verified=False, independent_answer="", note="검증 결과가 없어요.")
        )
        results.append(SimilarProblem(**p.model_dump(), verification=check))

    return GenerateResponse(
        source=generated.source,
        difficulty=difficulty,
        provider=provider.name,
        model=provider.model,
        problems=results,
    )
