# 제출용 Baseline (Tutor / Guard 분리 구조)

`POST /tutor`, `POST /guard` 를 제공하는 FastAPI 서버. **튜터와 가드는 서로 다른 폴더에서 독립적으로 개발**하고,
서버·형식·인증·로깅은 공통 레이어가 보장한다.

## 폴더 구조와 담당

```text
baseline/
├── app/
│   ├── main.py              [공통] 서버·인증·리다이렉트 방지·타임아웃·fallback·로깅
│   ├── config.py            [공통] 경로·토큰·LLM 설정 (환경변수)
│   ├── common/
│   │   ├── llm.py           [공통] LLM 호출 (JSON 강제, 동시성 제한)
│   │   ├── kb.py            [공통] KB 로드 + BM25 검색 (서버 시작 시 색인)
│   │   ├── schema.py        [공통] 요청 정규화 · 응답 규격 강제 · unsupported 원문 위치 복원
│   │   └── logging_util.py  [공통] 시간·길이·상태·오류명만 로깅
│   ├── tutor/               ★ 튜터 담당만 수정
│   │   ├── pipeline.py      handle(req, kb, retriever=None) → {answer, action, citations}
│   │   ├── retrieval.py     튜터 전용 임베딩 + BM25 RRF 검색
│   │   ├── prompts.py       시스템 프롬프트·action 기준
│   │   └── config.py        TOP_K, reasoning effort 등
│   └── guard/               ★ 가드 담당만 수정
│       ├── pipeline.py      check_input (입력 담당) / check_output (답변 담당) 병렬 실행
│       ├── prompts.py       입력 범주 정의 / 답변 판정 프롬프트
│       ├── policy.py        범주 → allow/block 정책표 (공개 라벨로 확정)
│       └── config.py
├── scripts/
│   ├── eval_public.py         --part tutor|guard  공개 세트 빠른 평가
│   ├── analyze_guard_labels.py  가드 범주×결정 교차표 → policy.py 근거
│   └── load_test.py           64 동시 요청·형식·인증·리다이렉트 점검
├── run.sh  requirements.txt  .env.example
```

**규칙**
- 파트 코드는 `async def handle(req: dict, kb, ...) -> dict` 인터페이스만 지키면 내부는 자유. 형식이 조금 틀려도 `schema.py`가 규격대로 고쳐서 내보내므로 **형식 오류로 0점 나는 일은 없다.**
- 가드 2명은 `check_input` / `check_output` 함수를 나눠 맡으면 충돌이 없다 (프롬프트도 `INPUT_*` / `OUTPUT_*` 로 분리).
- `app/common/`, `app/main.py` 수정은 팀에 공유 후 진행 (양쪽 점수에 영향). 검색 개선(임베딩 등)은 `kb.py`의 `search()` 인터페이스를 유지한 채 바꾼다.
- 파트별 튜닝 값은 각자 `config.py` 또는 환경변수(`TUTOR_*`, `GUARD_*`)로 바꾼다.

## 런박스 실행

```bash
cd /home/elicer/competition
# baseline 폴더를 여기로 복사 (→ /home/elicer/competition/baseline, kb/ 는 ../kb 로 자동 인식)
cd baseline
pip install -r requirements.txt
cp .env.example ../.env      # 키·SERVER_TOKEN 입력 (이미 ../.env 가 있으면 SERVER_TOKEN 만 추가)
bash run.sh                  # 0.0.0.0:8000, PORT=8001 bash run.sh 로 변경 가능 (8080 금지)
```

시작 로그에 `[kb] loaded N sections` 가 0보다 커야 한다 (경로가 다르면 `KB_DIR=/path/to/kb`).

## 검증 (제출 전)

```bash
# 1) 형식·동시성·인증 (크레딧 0원: LLM_MOCK=1 bash run.sh 로 띄운 뒤)
python scripts/load_test.py --token "$SERVER_TOKEN" --n 128
# 2) 외부 주소로도 동일 확인
python scripts/load_test.py --url https://<런박스>.tunnel.elice.io/proxy/8000 --token "$SERVER_TOKEN" --n 64
# 3) 파트별 공개 세트 평가 (실제 LLM)
python scripts/eval_public.py --part tutor --token "$SERVER_TOKEN"
python scripts/eval_public.py --part guard --token "$SERVER_TOKEN"
# 4) 공식 로컬 채점기 harness/, grader/ 로 최종 확인
```

`eval_public.py` 는 공개 jsonl 의 필드 이름을 자동 탐색한다. 첫 실행 때 출력되는 `first item keys` 를 보고 라벨을 못 찾으면 `find()` 경로를 고친다.

## 제출

`/home/elicer/competition/submission.json` (위치·이름 변경 금지):

```json
{
  "tutor_url": "https://<런박스 주소>.tunnel.elice.io/proxy/8000/tutor",
  "guard_url": "https://<런박스 주소>.tunnel.elice.io/proxy/8000/guard",
  "token": "<.env 의 SERVER_TOKEN 과 동일>",
  "commit": "<git rev-parse HEAD>"
}
```

채점이 끝날 때까지 서버와 런박스 탭을 유지한다.

## 지금 baseline 이 하는 것 / 할 일

| 파트 | 현재 | 우선 개선 (TODO) |
|---|---|---|
| 튜터 | BM25+임베딩 RRF top5 → LLM으로 action·answer·citations 생성, 인용은 KB 실존 ID만 | 공개 라벨로 action 기준 보정, 인용 개수 줄이기(정밀도), 퀵버튼 첫 발화 처리 |
| 가드-입력 | LLM 범주 분류 → `policy.py` 정책표로 allow/block | **`analyze_guard_labels.py` 로 정책표 확정 (현재 미검증 초안)**, 범주 정의를 라벨 기반으로 재작성 |
| 가드-답변 | 근거 검색 + LLM 판정, unsupported 는 후보 답변 원문으로 위치 복원 | grounded 기준 보정, 구간 단위(문장/구) 맞추기 |
| 공통 | 오류·25초 초과 시 규격 fallback, 본문 미로깅 | 임베딩 하이브리드 검색, 지연 시간 측정 |

LLM 이 없을 때의 fallback: 튜터 `clarify`, 가드 `allow/normal` + `grounded/safe=true`.

## Elice 실험 매크로

`scripts/run_experiment.sh`는 **깨끗하게 커밋된 후보 브랜치**에서만 실행합니다. 실제 LLM으로 64 동시성 검사를 하고 공개 세트 평가를 10회 실행합니다. 후보 중위값이 기존 champion보다 3점 이내이면 champion도 같은 시점에 10회 재평가합니다.

```bash
git switch codex/experiment/<id>
bash scripts/run_experiment.sh \
  --id <YYYYMMDD-sequence> \
  --scope tutor \
  --hypothesis "가설을 한 문장으로 적습니다"
```

- `experiment-reports/champion.json`이 없으면 최초 기준을 만드는 `bootstrap_needs_review` 결과만 기록합니다.
- 64 동시성, 형식, 인증, 리다이렉트, 각 평가의 형식·판정 오류가 한 번이라도 발생하면 즉시 실패합니다.
- 총점 중위값 승격 폭은 `Δ=3`입니다. total은 올랐지만 guard가 떨어지거나, tutor가 오르지 않은 상태에서 guard만 4.2점 이상 오르면 `review_required`로 멈춥니다.
- 요청·응답 본문은 저장하지 않습니다. `/tmp`에는 숫자 집계와 서버 메타 로그만 잠시 두고 종료 시 삭제합니다.
- `scripts/publish_experiment_result.sh`가 `exp-result-<id>/`의 수치 결과와 `report.json`을 현재 후보 브랜치에 커밋·push합니다. 같은 폴더의 `logs/`는 런박스에 남지만 Git에는 올라가지 않습니다. `main`/`champion`에는 직접 push하지 않습니다.

상세 운영 지침은 로컬 전용 `.docs/experiment-loop.md`를 확인하세요.

실험 매크로는 시작 시 `fastapi`, `uvicorn`, `openai`, `httpx`를 확인합니다. 하나라도
없으면 `requirements.txt`에 선언된 의존성만 설치하고, 이미 설치되어 있으면 설치를
건너뜁니다.

후보가 커밋된 뒤 `scripts/evaluate_current_candidate.sh`를 실행하면 KST 시각+커밋 해시의
실험 ID, 최신 커밋 제목의 가설, 변경 경로 기반 범위를 자동으로 생성해 실험 매크로를 호출합니다.

### 후보 브랜치·커밋·push 만들기

작업 트리의 변경을 새 후보 브랜치 하나에 묶어 원격으로 올릴 때는 아래 스크립트를 쓴다.
현재의 **모든 비무시 변경**은 기본으로 한 커밋에 포함된다. 기존 브랜치,
`main`, `champion`을 덮어쓰거나 force-push하지 않는다.

```bash
# .git_info를 현재 후보 정보로 갱신한 뒤, 인자 없이 실행할 수 있다.
bash scripts/create_candidate_branch.sh

# 최초 실행: feature·번호를 기록한다.
bash scripts/create_candidate_branch.sh \
  --feature tutor-hybrid-retrieval \
  --number 01 \
  --update "add 1536d BM25 and embedding RRF retrieval"

# 다음 변경: .git_info를 읽어 같은 feature의 번호를 02로 자동 증가시킨다.
bash scripts/create_candidate_branch.sh \
  --update "tune dense retrieval candidate count"
```

브랜치는 `TraderPark/<변경특징>-<변경번호>` 형식으로 생성되고 upstream을 설정한다. 예:
`TraderPark/tutor-hybrid-retrieval-01`. `.git_info`에는 마지막 `feature`, `number`, `message`를
로컬로만 저장한다. 실행 전 변경 목록·커밋 메시지를 보여 주며 `y` 또는 `yes`를 입력해야만
브랜치 생성·커밋·push를 수행한다. 모든 비무시 변경은 기본으로 포함된다. `.env`, `.docs/`,
`.venv/`, `.git_info`, `exp-result-*/logs/`는 `.gitignore`에 의해 포함되지 않는다.

### 실험 진행 대시보드

실험을 실행하기 전 별도 터미널에서 다음을 실행하면 64 동시성 검사 상태와 10회
채점 표(회차별 정답·부분 점수·오답·형식 오류·총점·tutor·guard)를 실시간으로 볼 수 있다.

```bash
python3 scripts/experiment_dashboard.py
```

`c`/`h`로 candidate/champion 표를 전환하고 `q`로 화면만 닫는다. 상태 파일에는 수치와
단계만 기록하며 요청·응답 본문이나 비밀값은 표시·저장하지 않는다. 상세 내용은 로컬 전용
`.docs/experiment-loop.md`를 참고한다.

### 튜터 하이브리드 검색 실험

튜터는 BM25와 `text-embedding-3-small`의 코사인 유사도 순위를 RRF로 합친다. 기본값은
1,536차원이며 KB 임베딩은 서버 시작 때 한 번만 만든다. 요청별 질문 벡터는 메모리에서만
사용하며 저장하거나 로그로 남기지 않는다. 가드는 이 실험의 영향을 받지 않도록 기존 BM25
검색을 유지한다. 임베딩 API 초기화·호출이 실패하면 서버를 멈추지 않고 튜터도 기존 BM25
경로로 자동 복귀한다.

512차원은 성능·안정성 비교를 위한 별도 후보에서만 다음처럼 설정한다. 같은 후보에서 다른
검색 변경과 함께 비교하지 않는다.

```bash
export TUTOR_EMBEDDING_DIMENSIONS=512
```
