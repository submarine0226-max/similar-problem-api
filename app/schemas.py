"""요청/응답 및 Claude 구조화 출력 스키마."""

from enum import Enum

from pydantic import BaseModel, Field


class Difficulty(str, Enum):
    easier = "easier"
    same = "same"
    harder = "harder"


# ---- 텍스트 입력 (problem-extractor의 ProblemOut을 그대로 넣어도 됨, 모르는 필드는 무시) ----


class ChoiceIn(BaseModel):
    label: str = ""
    text: str


class PointIn(BaseModel):
    label: str
    coordinates: str | None = None


class FigureIn(BaseModel):
    kind: str = "other"
    description: str = ""
    equations: list[str] = []
    points: list[PointIn] = []
    table_markdown: str | None = None


class ProblemInput(BaseModel):
    stem: str = Field(min_length=1, description="발문. 수식은 $...$ LaTeX")
    shared_context: str | None = Field(None, description="여러 문제가 공유하는 지문/조건")
    choices: list[ChoiceIn | str] = Field(default_factory=list, description="객관식 보기 ({label, text} 또는 문자열)")
    answer: str | None = Field(None, description="원본 정답 (있으면 참고용)")
    solution: str | None = None
    topic: str | None = None
    tags: list[str] = Field(default_factory=list)
    problem_type: str | None = None
    figures: list[FigureIn] = Field(default_factory=list, description="그림 정보 (종류, 설명, 그림 속 식, 점, 표)")


class TextGenerateRequest(BaseModel):
    problem: ProblemInput
    count: int = Field(3, ge=1, le=10)
    difficulty: Difficulty = Difficulty.same


# ---- Claude 구조화 출력: 1단계(인식 + 생성) ----


class SourceProblem(BaseModel):
    found: bool = Field(description="요청한 원본 문제를 찾아 읽었는지 여부")
    text: str = Field(description="원본 문제 전문. 수식은 LaTeX($...$). 못 찾았으면 빈 문자열")
    topic: str = Field(description="단원/유형 (예: 이차방정식의 근과 계수의 관계)")
    grade_level: str = Field(description="추정 학년 (예: 중3, 고1)")
    reason_if_not_found: str = Field(description="found가 false일 때 이유, 아니면 빈 문자열")


class GeneratedProblem(BaseModel):
    problem: str = Field(description="유사 문제 본문. 수식은 LaTeX($...$)")
    choices: list[str] = Field(description="객관식이면 보기 목록, 주관식이면 빈 배열")
    answer: str = Field(description="최종 정답 (객관식이면 보기 번호와 값)")
    solution_steps: list[str] = Field(description="단계별 풀이")


class DraftProblem(BaseModel):
    """모델 출력용. design을 맨 앞에 둬서 정답부터 정하고 숫자를 역산하게 한다 (design은 응답에 안 나감)."""

    design: str = Field(
        description="출제 설계 메모. 먼저 정답을 정하고, 그 정답이 나오도록 계수·조건을 역산한 계산 과정. "
        "여기서 계산이 맞는지 끝까지 확인한 뒤에 problem을 쓴다."
    )
    problem: str = Field(description="유사 문제 본문. 수식은 LaTeX($...$)")
    choices: list[str] = Field(description="객관식이면 보기 목록, 주관식이면 빈 배열")
    answer: str = Field(description="최종 정답 (객관식이면 보기 번호와 값)")
    solution_steps: list[str] = Field(description="단계별 풀이. 문제를 중간에 바꾸지 않고 problem 그대로 푼다")

    def to_public(self) -> GeneratedProblem:
        return GeneratedProblem(**self.model_dump(exclude={"design"}))


class GenerationResult(BaseModel):
    source: SourceProblem
    problems: list[DraftProblem]


# ---- Claude 구조화 출력: 2단계(검증) ----


class VerificationItem(BaseModel):
    index: int = Field(description="검증한 문제 번호 (0부터)")
    work: str = Field(description="주어진 정답을 보기 전에 문제를 직접 푼 계산 과정")
    independent_answer: str = Field(description="주어진 정답을 보기 전에 직접 계산한 답")
    is_consistent: bool = Field(description="직접 계산한 답과 주어진 정답이 수학적으로 같은지")
    note: str = Field(description="불일치하거나 문제에 오류가 있을 때 설명, 아니면 빈 문자열")


class VerificationResult(BaseModel):
    items: list[VerificationItem]


# ---- API 응답 ----


class Verification(BaseModel):
    verified: bool
    independent_answer: str
    note: str


class SimilarProblem(GeneratedProblem):
    verification: Verification


class GenerateResponse(BaseModel):
    source: SourceProblem
    difficulty: Difficulty
    provider: str
    model: str
    problems: list[SimilarProblem]
