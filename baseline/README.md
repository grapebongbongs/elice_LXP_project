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
│   │   ├── pipeline.py      handle(req, kb) → {answer, action, citations}
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
- 파트 코드는 `async def handle(req: dict, kb) -> dict` 인터페이스만 지키면 내부는 자유. 형식이 조금 틀려도 `schema.py`가 규격대로 고쳐서 내보내므로 **형식 오류로 0점 나는 일은 없다.**
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
| 튜터 | BM25 top5 → LLM 1회로 action·answer·citations 생성, 인용은 KB 실존 ID만 | 공개 라벨로 action 기준 보정, 인용 개수 줄이기(정밀도), 퀵버튼 첫 발화 처리, 임베딩 검색 |
| 가드-입력 | LLM 범주 분류 → `policy.py` 정책표로 allow/block | **`analyze_guard_labels.py` 로 정책표 확정 (현재 미검증 초안)**, 범주 정의를 라벨 기반으로 재작성 |
| 가드-답변 | 근거 검색 + LLM 판정, unsupported 는 후보 답변 원문으로 위치 복원 | grounded 기준 보정, 구간 단위(문장/구) 맞추기 |
| 공통 | 오류·25초 초과 시 규격 fallback, 본문 미로깅 | 임베딩 하이브리드 검색, 지연 시간 측정 |

LLM 이 없을 때의 fallback: 튜터 `clarify`, 가드 `allow/normal` + `grounded/safe=true`.
