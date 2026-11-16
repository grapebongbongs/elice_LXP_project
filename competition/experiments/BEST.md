# 현재 최고 설정 (자동 기록)

- 커밋: `305fa8c` (브랜치 `best/90.32-305fa8c`)
- 제출 점수: 평균 **90.32** (같은 커밋 4회) — 마지막 회차 총점 89.99 (튜터 87.42, 가드 92.56)
- 기록 시각: 2026-11-16 16:58:13
- 메모: 같은 커밋 네 번째 제출. 89fd6df (88.53) 뒤 8000 서버와 submission.json 을 305fa8c 로 되돌려 둔 상태에서 사용자가 화면에서 직접 제출한 것으로, 서버 로그 기준 11/16 16:52~16:54 (요청 600개, 가장 느린 응답 19.6초, 서버 오류 0). 계정의 마지막 제출이 다시 최고 커밋이 됐다. 이 줄의 time 은 기록한 시각

## 이전 최고(`c9a5d1a`) 이후 바뀐 것

- 305fa8c LOOP.md: experiments 54-58 (quoted-hate rule and 3-sample answer check adopted; quote keep, guard off-task block-only, last-utterance override check rejected)
- 520cafc Guard: sample the answer check 3 times by default; keep abstaining when the first-pass quote is not found
- 00951f4 Tutor: TUTOR_QUOTE_FAIL_ABSTAIN now also governs the first-pass answer (keep the answer when its quote is not found, flag still on by default)
- d56c73c Guard: answer check can be sampled several times and combined by majority, shortest span (GUARD_OUTPUT_SAMPLES, off by default)
- d9991f3 Tutor: treat a discriminatory sentence quoted as exercise material as a normal question (QUOTED_HATE_RULE)
- 3567f29 LOOP.md: correct the time of the status note
- 79bf440 LOOP.md: runbox restarted on 2026-11-15, server back on c9a5d1a, error review 53 (no new hypothesis)
- 7a9bd02 LOOP.md: state on 2026-11-15 (runbox session ended, loop paused) and what to do on resume
- 60a4cb5 LOOP.md: experiments 51-52 rejected (submission marker gate, answer-check safety as a second opinion)
- f5433a5 LOOP.md: experiments 49-50 rejected (span from material diff, copula stripping, dialog-context retrieval)
- 5a31b89 LOOP.md: experiment 48 rejected (query weights and BM25 parameters are already at the plateau)
- cfb761b LOOP.md: experiment 47 rejected (phrase markers for the override boundary do not carry over to holdout); next candidates
- dcad0c0 Guard: apply the answer-only marker rule to categories (allow categories only); format note for the answer prompt rejected
- a762944 Tutor: optional sentence in the answer prompt about format requests and pasted code (TUTOR_GEN_FORMAT_NOTE, off by default)
- 87c7dd9 LOOP.md: experiment 40 rejected (answer check at medium effort is worse), note 45 on format-request abstains
- c94010f LOOP.md: submission c9a5d1a = 89.33 (tutor 88.5, guard 90.15), new best; experiments 43 (adopted) and 44 (rejected)
- 5a6cafe best: 89.33 @c9a5d1a (harm_type 보정 + 이름 붙은 규칙 abstain + 무의미 입력 규칙 + 튜터 수업 밖 작업 판정 + 자기소개 표지 + 가드 입력 보조 호출(low). 공개 세트 전체 2회 91.43(eeb824a), 튜터 2회 91.69, 가드 2회 92.47. 11/13 14:03 제출, 서버 로그상 가장 느린 응답 25.0초(튜터 1건이 서버 한도에 걸려 기본 답), 채점기 시간 초과 0)
- f210d27 record_submission.py: optional trailer line for the best-score commit

```text
baseline/app/common/focus.py   | 18 +++++++++
 baseline/app/config.py         |  5 +++
 baseline/app/guard/config.py   | 10 +++++
 baseline/app/guard/pipeline.py | 86 +++++++++++++++++++++++++++++++++++++-----
 baseline/app/tutor/config.py   | 13 ++++++-
 baseline/app/tutor/pipeline.py | 23 +++++++++--
 baseline/app/tutor/prompts.py  |  7 ++++
 7 files changed, 147 insertions(+), 15 deletions(-)
```

## 복원 방법

```bash
git checkout 305fa8c -- baseline
bash baseline/run.sh            # 서버 재시작
python3 loop/presubmit_check.py --write-commit
# → 제출 버튼
```
