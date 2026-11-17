# Similar Problem API

수학 문제(이미지·PDF, 또는 이미 추출된 문제 텍스트)를 보내면 유사 문제를 정답·단계별 풀이와 함께 만들어 주는 API.
기획은 [PROJECT_BRIEF.txt](PROJECT_BRIEF.txt) 참고.

## 동작 방식

1. **인식 + 생성** — 모델이 이미지/PDF에서 원본 문제를 읽고, 지정한 개수·난이도로 유사 문제와 정답·풀이를 만든다.
2. **검증** — 별도 호출에서 모델이 정답을 보기 전에 각 문제를 직접 풀고, 주어진 정답과 맞는지 판정한다. 결과는 문제마다 `verification.verified`로 돌려준다.

두 호출 모두 구조화 출력(JSON 스키마)을 써서 항상 정해진 JSON 형태로 받는다.

## 모델 제공사 (Claude 또는 Gemini 무료 키)

| | Claude | Gemini |
|---|---|---|
| 키 | `ANTHROPIC_API_KEY` ([console.anthropic.com](https://console.anthropic.com)) | `GEMINI_API_KEY` ([aistudio.google.com/apikey](https://aistudio.google.com/apikey)) |
| 비용 | 유료 | **무료 등급 가능** |
| 기본 모델 | `claude-opus-5-5` | `gemini-3.5-flash-lite` |
| 비고 | 거부 시 서버 측 fallback으로 다른 모델 재시도 | 무료 등급은 분당·하루 요청 한도가 있고, 보낸 데이터를 Google이 서비스 개선에 쓸 수 있음. 서버가 바쁠 때 나는 503은 최대 5번 자동 재시도 |
| 응답 시간 | - | 실측 (문제 3개, 생성+검증): 이미지 업로드 약 40~50초, 텍스트 입력 약 30초 |

- `LLM_PROVIDER`를 비우면 채워진 키로 자동 선택 (둘 다 있으면 Claude).
- `LLM_MODEL`로 모델을 바꿀 수 있다. Gemini에서 수학 정확도가 아쉬우면 `LLM_MODEL=gemini-3.8-flash`.
- 응답의 `provider`, `model` 필드에 어떤 모델이 썼는지 나온다.

## 실행

```bash
py -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env
```

`.env`에 키 하나를 채운 뒤:

```bash
.venv\Scripts\python -m uvicorn app.main:app --reload
```

API 문서: http://localhost:8000/docs

## 사용법

### 1) 이미지·PDF 업로드

`POST /v1/similar-problems` (multipart/form-data)

| 필드 | 필수 | 설명 |
|---|---|---|
| `file` | O | png / jpeg / webp / gif / pdf, 최대 20MB |
| `count` | | 1~10, 기본 3 |
| `difficulty` | | `easier` / `same`(기본) / `harder` |
| `problem_number` | PDF면 O | 처리할 문제 번호 |

```bash
curl -F "file=@problem.png" -F "count=3" -F "difficulty=same" http://localhost:8000/v1/similar-problems
```

### 2) 이미 추출된 문제(텍스트)로 생성

`POST /v1/similar-problems/from-text` (JSON). 이미지를 다시 보내지 않아서 더 빠르고 가볍다.

```json
{
  "problem": {
    "stem": "이차함수 $y=x^2-4$의 그래프가 x축과 만나는 두 점을 A, B라 할 때, 선분 AB의 길이를 구하시오.",
    "choices": [],
    "topic": "이차방정식",
    "figures": [{"kind": "graph", "description": "포물선이 x축과 두 점 A, B에서 만남", "points": [{"label": "A"}, {"label": "B"}]}]
  },
  "count": 3,
  "difficulty": "same"
}
```

| `problem` 필드 | 필수 | 설명 |
|---|---|---|
| `stem` | O | 발문. 수식은 `$...$` |
| `choices` | | 보기. `{"label": "①", "text": "2"}` 또는 `"① 2"` 같은 문자열 |
| `shared_context`, `topic`, `tags` | | 공통 지문, 단원, 개념 태그 |
| `figures` | | 그림 정보: `kind`, `description`, `equations`, `points`, `table_markdown` |
| `answer`, `solution` | | 원본 정답·해설 (참고용) |

**problem-extractor와 같이 쓰기:** problem-extractor의 `GET /problems/{id}` 응답(문제 JSON)을 `problem`에 그대로 넣으면 된다. `id`, `bbox`, `image_url` 같은 나머지 필드는 무시한다. 추출 결과를 사람이 검수·수정한 뒤 생성하면 잘못 읽은 문제로 생성할 일이 없고, 그림 정보(그림 속 식, 이름 붙은 점, 표)도 함께 넘어간다.

응답 형식은 두 방식이 같다. 응답 예시:

```json
{
  "source": {"found": true, "text": "$x^2-5x+6=0$의 두 근의 합은?", "topic": "이차방정식", "grade_level": "중3", "reason_if_not_found": ""},
  "difficulty": "same",
  "provider": "gemini",
  "model": "gemini-3.5-flash-lite",
  "problems": [
    {
      "problem": "$x^2-7x+12=0$의 두 근의 합을 구하시오.",
      "choices": [],
      "answer": "7",
      "solution_steps": ["근과 계수의 관계에 의해 두 근의 합은 $-\\frac{-7}{1}$", "따라서 7"],
      "verification": {"verified": true, "independent_answer": "7", "note": ""}
    }
  ]
}
```

수식은 LaTeX(`$...$`)로 온다. 오류는 `{"detail": "..."}` 형태 (415 형식 오류, 422 문제를 못 찾음/거부, 429 요청 과다 등).

## 테스트

```bash
.venv\Scripts\python -m pytest
```

테스트는 Claude·Gemini 호출을 가짜 클라이언트로 바꿔 돌리므로 API 키·과금이 필요 없다.

## MVP에서 뺀 것

- API 키/로그인, 그래프·도형 그림 생성, 결과 DB 저장
