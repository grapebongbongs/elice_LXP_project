# 제출 서버 기술 가이드

이 디렉터리는 `POST /tutor`와 `POST /guard`를 제공하는 최종 FastAPI 서버입니다. 현재 구현 기준은 커밋 `067d9cb`이며, 팀 공유 브랜치는 `sangin-93.38`입니다.

## 처리 구조

```text
요청
 ├─ Bearer 토큰 확인
 ├─ 요청 정규화 및 크기 확인
 ├─ Tutor 또는 Guard 파이프라인
 ├─ 응답 스키마 강제 및 fallback
 └─ HTTP 200 JSON 응답
```

- 서버 전체 처리 제한은 기본 25초입니다.
- LLM 호출은 최대 64개까지 동시에 처리합니다.
- 느린 호출은 설정된 시점에 보조 호출을 보내 먼저 완료된 결과를 사용합니다.
- 오류가 발생해도 채점 규격에 맞는 fallback JSON을 반환합니다.
- 로그에는 경로, 본문 길이, 상태, 처리 시간, 오류 종류만 남기며 요청·응답 본문은 남기지 않습니다.

## 디렉터리 구조

```text
baseline/
├── app/
│   ├── main.py              # FastAPI 엔드포인트, 인증, 제한 시간, fallback
│   ├── config.py            # 공통 경로·LLM·서버 설정
│   ├── common/
│   │   ├── kb.py            # KB 로드와 BM25 검색
│   │   ├── llm.py           # JSON LLM 호출, 동시성·보조 호출 제어
│   │   ├── schema.py        # 요청 정규화와 최종 응답 규격 보정
│   │   ├── focus.py         # 경계 사례 판정과 규칙
│   │   ├── normalize.py     # 텍스트 정규화
│   │   ├── fewshot.py       # 선택적 공개 예시 검색
│   │   ├── embed.py         # Guard 출력 정렬 실험용 임베딩
│   │   └── logging_util.py  # 본문을 제외한 운영 로그
│   ├── tutor/
│   │   ├── pipeline.py      # 검색, 분류, 답변, 인용 처리
│   │   ├── prompts.py       # Tutor 프롬프트
│   │   └── config.py        # TUTOR_* 설정
│   └── guard/
│       ├── pipeline.py      # 입력·후보 답변 판정
│       ├── prompts.py       # Guard 프롬프트
│       ├── policy.py        # 범주별 allow/block 정책
│       └── config.py        # GUARD_* 설정
├── scripts/
│   ├── load_test.py         # 인증·형식·동시성 검사
│   ├── eval_public.py       # 공개 세트 파트별 평가
│   └── analyze_guard_labels.py
├── requirements.txt
├── .env.example
└── run.sh
```

`embed.py`는 현재 기본 경로에서는 사용하지 않습니다. `GUARD_OUTPUT_ALIGN`의 기본값이 `0`이므로 최종 공유본은 BM25 검색만 사용합니다.

## API

모든 POST 요청에 다음 헤더가 필요합니다.

```http
Authorization: Bearer <SERVER_TOKEN>
Content-Type: application/json
```

### `GET /health`

```json
{"ok": true, "sections": 48}
```

`sections`가 0이면 KB 경로를 확인해야 합니다.

### `POST /tutor`

요청:

```json
{
  "spec_version": "1.0",
  "session_id": "s_example",
  "messages": [
    {"role": "user", "content": "체크카드의 하루 사용 한도는 얼마야?"}
  ]
}
```

응답:

```json
{
  "answer": "자료를 바탕으로 한 답변",
  "action": "answer",
  "citations": ["FIN-02-01"]
}
```

`action`은 `answer`, `scaffold`, `clarify`, `abstain`, `redirect`, `refuse`, `escalate` 중 하나입니다. `answer`는 4,000자 이하이며, `citations`에는 실제 KB 섹션 ID만 남습니다.

### `POST /guard`

요청:

```json
{
  "spec_version": "1.0",
  "session_id": "s_example",
  "messages": [
    {"role": "user", "content": "질문 내용"}
  ],
  "candidate_answer": "검사할 튜터 답변"
}
```

응답:

```json
{
  "input": {
    "decision": "allow",
    "category": "normal"
  },
  "output": {
    "grounded": true,
    "unsupported": [],
    "safe": true,
    "category": "normal"
  }
}
```

`candidate_answer`가 없는 요청은 `input`만 반환합니다. `unsupported`의 각 항목은 반드시 후보 답변에 실제로 존재하는 부분 문자열로 보정됩니다.

끝 슬래시 유무와 런박스 프록시 경로 모두 리다이렉트 없이 처리합니다.

## Tutor 파이프라인

1. 대화 이력과 마지막 발화를 정규화합니다.
2. 저신호 입력과 개인정보·유해 요청·과제 대행 등 명확한 경계 사례를 확인합니다.
3. 이전 Tutor 답변을 포함한 검색 질의로 KB의 BM25 상위 5개 섹션을 찾습니다.
4. 저추론 분류를 2회 동시에 실행합니다. 두 결과가 다르거나 실패하면 세 번째 분류를 실행해 다수결합니다.
5. 분류와 1차 답변 생성을 가능한 범위에서 병렬 실행합니다.
6. 검색 1위 섹션에서 추출형 답변을 만들고 근거 문장이 실제 자료에 있는지 확인합니다.
7. 근거 확인이 실패하면 제한 시간 안에서 재시도하거나, 검색 문장을 직접 추출하거나, 자료 밖 질문으로 abstain합니다.
8. 인용을 최대 1개로 정리하고 최종 응답 스키마를 강제합니다.

주요 기본값:

| 환경변수 | 기본값 | 의미 |
|---|---:|---|
| `TUTOR_TOP_K` | `5` | 검색할 KB 섹션 수 |
| `TUTOR_REASONING_EFFORT` | `low` | 답변 생성 추론 수준 |
| `TUTOR_MAX_TOKENS` | `2000` | Tutor LLM 최대 출력 토큰 |
| `TUTOR_CLASSIFY_ADAPTIVE_VOTE` | `1` | 분류 적응형 다수결 사용 |
| `TUTOR_SPECULATIVE` | `1` | 분류와 답변 생성을 병렬 시작 |
| `TUTOR_EXTRACTIVE` | `1` | 근거 문장 선택형 답변 사용 |
| `TUTOR_MAX_CITATIONS` | `1` | 인용 개수 상한 |
| `TUTOR_APPEND_QUOTE` | `1` | 확인된 근거 문장을 답변에 보강 |
| `TUTOR_APPEND_CONTEXT` | `0` | 추가 문맥 문장 덧붙이기 비활성 |
| `TUTOR_STRIP_WRAPPERS` | `0` | 발화 말머리 제거 비활성 |

## Guard 파이프라인

1. 입력 판정과 후보 답변 판정을 동시에 시작합니다.
2. 입력 판정은 검색 자료 상위 3개를 함께 보고 `decision`과 `category`를 정합니다.
3. 기본 `medium` 판정이 8초 안에 끝나지 않으면 같은 프롬프트를 `low`로 보내 먼저 끝난 결과를 사용합니다.
4. 차단 입력의 후보 답변은 `grounded=true`, `unsupported=[]`로 처리합니다.
5. 허용 입력의 후보 답변은 3회 독립 판정합니다.
6. `grounded`는 다수결로 정하고, 근거 없음 표 중 가장 짧고 유효한 구간을 선택합니다.
7. `safe`는 입력 허용 여부에서 유도하고 최종 스키마를 강제합니다.

주요 기본값:

| 환경변수 | 기본값 | 의미 |
|---|---:|---|
| `GUARD_TOP_K` | `4` | 출력 판정용 검색 섹션 수 |
| `GUARD_INPUT_TOP_K` | `3` | 입력 판정용 검색 섹션 수 |
| `GUARD_INPUT_REASONING_EFFORT` | `medium` | 입력 분류 추론 수준 |
| `GUARD_OUTPUT_REASONING_EFFORT` | `low` | 후보 답변 판정 추론 수준 |
| `GUARD_MAX_TOKENS` | `2000` | Guard LLM 최대 출력 토큰 |
| `GUARD_INPUT_BACKUP_AFTER` | `8` | low 보조 판정을 시작할 시간(초) |
| `GUARD_OUTPUT_SAMPLES` | `3` | 후보 답변 독립 판정 횟수 |
| `GUARD_SAFE_FROM_INPUT` | `1` | 입력 판정에서 safe 유도 |
| `GUARD_STRIP_WRAPPERS` | `0` | 발화 말머리 제거 비활성 |
| `GUARD_HARM_CLAUSE` | `0` | 추가 harmful 문구 비활성 |
| `GUARD_OUTPUT_ALIGN` | `0` | 임베딩 기반 문장 정렬 비활성 |

비활성 옵션은 공식 점수에서 채택되지 않은 실험값입니다. 특별한 비교 실험이 아니라면 최종 공유본의 기본값을 유지합니다.

## 환경 설정

저장소 루트의 `.env`를 사용합니다. `run.sh`는 `baseline/.env`보다 루트의 `.env`를 먼저 읽습니다.

필수 값:

```ini
OPENAI_API_KEY=발급받은_API_키
SERVER_TOKEN=제출용_서버_토큰
```

런박스에서 미리 제공되는 값:

```ini
OPENAI_BASE_URL=운영진_제공_URL
OPENAI_MODEL=gpt-5-nano
```

선택 값:

```ini
LLM_MOCK=1
PORT=8001
KB_DIR=/다른/KB/경로
LLM_MAX_CONCURRENCY=64
REQUEST_DEADLINE=25
```

- `SERVER_TOKEN`은 `submission.json`의 `token`과 같아야 하며 API 키와는 다른 값을 씁니다.
- `LLM_MOCK=1`은 형식·부하 검사 전용입니다. 실제 채점 서버에서는 사용하지 않습니다.
- `.env`, 임베딩 캐시, 비밀값은 Git에 커밋하지 않습니다.

## 실행

```bash
cd /home/elicer/competition/baseline
python3 -m pip install -r requirements.txt
bash run.sh
```

기본 포트는 8000입니다. 8080은 런박스 VSCode가 사용하므로 선택하지 않습니다.

## 제출 전 검사

서버와 검사는 서로 다른 터미널에서 실행합니다.

### 1. 상태 확인

```bash
curl -s http://127.0.0.1:8000/health
```

### 2. Mock 부하 테스트

첫 번째 터미널:

```bash
cd /home/elicer/competition/baseline
LLM_MOCK=1 PORT=8001 bash run.sh
```

두 번째 터미널:

```bash
cd /home/elicer/competition/baseline
python3 scripts/load_test.py \
  --url http://127.0.0.1:8001 \
  --token "$SERVER_TOKEN" \
  --n 64
```

### 3. 로컬 채점

```bash
cd /home/elicer/competition
python3 harness/score_local.py \
  --tutor-url http://127.0.0.1:8000/tutor \
  --guard-url http://127.0.0.1:8000/guard \
  --token "$SERVER_TOKEN" \
  --public public \
  --kb kb
```

로컬 채점과 공식 채점은 표본과 판정 모델이 달라 점수가 같지 않을 수 있습니다.

## 문제 해결

### `address already in use`

8000번 포트에 기존 서버가 이미 실행 중입니다. 기존 서버를 그대로 쓰거나 해당 프로세스를 정상 종료한 뒤 다시 시작합니다. 같은 포트에 서버 두 개를 띄우지 않습니다.

### `[kb] loaded 0 sections`

저장소 구조가 `/home/elicer/competition/baseline`과 `/home/elicer/competition/kb`인지 확인합니다. 다른 위치라면 `KB_DIR`을 지정합니다.

### HTTP 401

요청의 Bearer 토큰, `.env`의 `SERVER_TOKEN`, `submission.json`의 `token`이 모두 같은지 확인합니다.

### 제출은 되지만 채점 요청이 오지 않음

- `submission.json`이 `/home/elicer/competition/submission.json`에 있는지 확인합니다.
- 터널 주소에 `.tunnel.elice.io`가 중복되지 않았는지 확인합니다.
- URL 끝에 `/tutor`, `/guard`가 정확히 있는지 확인합니다.
- 외부 주소의 `/health` 또는 각 엔드포인트가 실제 서버로 연결되는지 확인합니다.

### `active_grade_exists`

이미 실행 중인 채점이 있다는 뜻입니다. 해시나 토큰을 바꿀 문제가 아니므로 기존 채점이 끝날 때까지 기다립니다.

### 판정 오류가 갑자기 많이 발생함

시간 초과·형식 오류·응답 없음이 0인데 판정 오류만 비정상적으로 많다면 채점기 측 일시 오류일 수 있습니다. 결과의 seed와 오류 수를 보관하고 운영진 안내에 따라 재제출합니다.

## 운영 및 규정

- 서버는 런박스 안에서 직접 검색·판정·응답해야 합니다.
- 운영진이 제공한 LLM API와 제공된 KB만 사용합니다.
- 요청 사이에 상태를 공유하거나 평가 요청을 저장·분석하지 않습니다.
- 요청·응답 본문, API 키, 토큰을 로그에 남기지 않습니다.
- 공개 정답이나 비공개 평가 패턴을 하드코딩하지 않습니다.
- 최종 제출 전 `../SPEC.md`와 최신 운영진 공지를 다시 확인합니다.
