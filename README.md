# Elice LXP AI Tutor & Guard

엘리스 LXP 경진대회용 AI 튜터·가드레일 서버입니다. 제공된 KB만 근거로 답변하고, Tutor와 Guard가 각각 학습 지원과 안전성·근거성을 판정합니다.

## 최종 기준

- 구현 기준 커밋: `067d9cb`
- 팀 공유 브랜치: `sangin-93.38`
- 최고 확인 점수: **93.38**
  - Tutor: 92.42
  - Guard: 94.34
  - 600개 요청, 시간 초과·형식 오류·응답 없음·판정 오류 0개

공식 평가는 매번 문항과 모델 출력이 달라질 수 있으므로 같은 코드에서도 점수가 변동할 수 있습니다.

## 핵심 구현

### Tutor

- 제공된 KB를 BM25로 검색하고 상위 근거를 바탕으로 답변
- 분류 결과가 불확실할 때만 추가 판정을 수행하는 적응형 다수결
- 개인정보, 유해 요청, 과제 대행, 자료 외 질문, 저신호 입력 등 유형별 대응
- 인용문 검증 실패 시 재시도·추출형 답변·abstain으로 안전하게 전환
- 답변과 인용을 최종 응답 규격에 맞게 보정

### Guard

- 입력 단계에서 요청의 허용 여부와 위험 범주 판정
- 출력 단계에서 후보 답변을 3회 독립 판정한 뒤 다수결
- 지원되지 않은 구간은 실제 후보 답변의 부분 문자열로 복원
- 입력 판정과 후보 답변 생성을 병렬 처리해 지연 최소화

### Server

- FastAPI 기반 `/tutor`, `/guard`, `/health` 제공
- Bearer 토큰 인증
- 요청당 내부 제한 시간과 규격 준수 fallback 응답
- 64개 동시 요청을 고려한 비동기 처리
- 평가 요청 본문이나 모델 응답 본문을 로그에 남기지 않음

## 저장소 구조

```text
.
├── baseline/          # 제출 서버와 실행 스크립트
│   ├── app/           # Tutor, Guard, 공통 KB·LLM 코드
│   ├── scripts/       # 부하 테스트 등 보조 도구
│   ├── requirements.txt
│   └── run.sh
├── harness/           # 로컬 채점 도구
├── grader/            # 채점 관련 코드
├── kb/                # 제공된 학습 자료
├── public/            # 공개 평가 데이터
├── SPEC.md            # 대회 API·채점 규격
└── submission.json    # 제출 URL, 토큰, 커밋 정보
```

자세한 구현과 설정은 [`baseline/README.md`](baseline/README.md)를 참고하세요.

## 런박스에서 실행

저장소 루트가 `/home/elicer/competition`인 경우:

```bash
cd /home/elicer/competition
python3 -m pip install -r baseline/requirements.txt
cp baseline/.env.example .env
```

`.env`에 최소한 다음 값을 설정합니다.

```ini
OPENAI_API_KEY=발급받은_API_키
SERVER_TOKEN=제출용_서버_토큰
```

`OPENAI_BASE_URL`과 `OPENAI_MODEL`이 런박스에 미리 설정되어 있다면 `.env`에 다시 적지 않아도 됩니다. `.env`는 Git에 올리지 않습니다.

서버를 시작합니다.

```bash
cd /home/elicer/competition/baseline
bash run.sh
```

다음 로그가 보이면 정상적으로 실행된 상태입니다.

```text
[kb] loaded 48 sections from /home/elicer/competition/kb
Uvicorn running on http://0.0.0.0:8000
```

## 빠른 확인

### 상태 확인

```bash
curl -s http://127.0.0.1:8000/health
```

정상 응답 예시:

```json
{"ok":true,"sections":48}
```

### Mock 부하 테스트

실제 API 사용량 없이 형식과 동시 요청 처리를 확인합니다. 첫 번째 터미널에서 mock 서버를 실행합니다.

```bash
cd /home/elicer/competition/baseline
LLM_MOCK=1 PORT=8001 bash run.sh
```

두 번째 터미널에서 64개 동시 요청 테스트를 실행합니다.

```bash
cd /home/elicer/competition/baseline
python3 scripts/load_test.py \
  --url http://127.0.0.1:8001 \
  --token "$SERVER_TOKEN" \
  --n 64
```

### 로컬 채점

실제 서버가 8000번 포트에서 실행 중일 때 저장소 루트의 다른 터미널에서 실행합니다.

```bash
cd /home/elicer/competition
python3 harness/score_local.py \
  --tutor-url http://127.0.0.1:8000/tutor \
  --guard-url http://127.0.0.1:8000/guard \
  --token "$SERVER_TOKEN" \
  --public public \
  --kb kb
```

## 제출 설정

외부 터널 주소를 확인한 뒤 루트의 `submission.json`을 설정합니다.

```json
{
  "tutor_url": "https://<런박스-터널>/proxy/8000/tutor",
  "guard_url": "https://<런박스-터널>/proxy/8000/guard",
  "token": "<SERVER_TOKEN과 같은 값>",
  "commit": "067d9cb"
}
```

- URL에 `.tunnel.elice.io`가 두 번 붙지 않았는지 확인합니다.
- `token`은 서버의 `SERVER_TOKEN`과 정확히 같아야 합니다.
- 이미 채점 중이면 `active_grade_exists` 오류가 날 수 있으므로 기존 채점 완료를 기다립니다.
- 서버를 재시작했다면 터널 주소가 유지되는지도 다시 확인합니다.

## 보안 및 대회 규정

- `.env`, API 키, 서버 토큰을 커밋하거나 외부로 전송하지 않습니다.
- 제공된 KB 외의 외부 자료를 답변 근거로 사용하지 않습니다.
- 평가 요청·응답 본문을 저장하거나 분석하지 않습니다.
- 공개 정답이나 특정 평가 문항을 하드코딩하지 않습니다.
- 최종 제출 전 `SPEC.md`와 운영진 공지를 다시 확인합니다.
