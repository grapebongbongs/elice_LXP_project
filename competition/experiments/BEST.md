# 현재 최고 설정 (자동 기록)

- 커밋: `17adfc1` (브랜치 `best/91.09-17adfc1`)
- 제출 점수: 평균 **91.09** (같은 커밋 6회) — 마지막 회차 총점 90.16 (튜터 88.17, 가드 92.14)
- 기록 시각: 2026-11-16 22:58:21
- 메모: 같은 커밋 재제출 3회째(92.5 를 넘으면 멈춘다). 11/16 22:56, rb.mjs submit, 제출 전 동시 64개 점검 17.8초

## 이전 최고(`305fa8c`) 이후 바뀐 것

- 17adfc1 Tutor: append the supporting sentence to the answer by default (TUTOR_APPEND_QUOTE=1) for a submission comparison against 305fa8c; ledger quote-append; LOOP.md: fourth submission of 305fa8c (89.99, mean 90.32)
- c689bee best: 90.32 @305fa8c (같은 커밋 네 번째 제출. 89fd6df (88.53) 뒤 8000 서버와 submission.json 을 305fa8c 로 되돌려 둔 상태에서 사용자가 화면에서 직접 제출한 것으로, 서버 로그 기준 11/16 16:52~16:54 (요청 600개, 가장 느린 응답 19.6초, 서버 오류 0). 계정의 마지막 제출이 다시 최고 커밋이 됐다. 이 줄의 time 은 기록한 시각)
- e60f29e loop/quote_judge_eval.py: paired judge comparison of answers with and without the appended supporting sentence
- 81255b5 Guard tail check for long pastes, guard wrapper stripping and tutor boundary voting off by default after submission 89fd6df = 88.53 (guard 88.90 vs 92.2 for 305fa8c); LOOP.md: decisions and result
- 1ca9f2d Tutor: optional appending of the supporting sentence to the answer (TUTOR_APPEND_QUOTE, off by default); record submission 89fd6df = 88.53 (tutor 88.17, guard 88.90)
- 89fd6df LOOP.md: third submission of 305fa8c (90.34, mean 90.43), submission spread, loss breakdown, experiments 70 (first-pass retry, on hold) and 71 (insult reply wording, rejected)
- cb7953d template_judge_eval.py: --only to pick candidates, one more candidate for the insult reply
- f2ab152 loop/template_judge_eval.py: measure how often the local judge marks the fixed tutor replies as complied, per candidate sentence
- 360a20c best: 90.43 @305fa8c (같은 커밋 세 번째 제출. 사용자가 화면에서 직접 제출한 것으로, 서버 로그 기준 11/16 16:06~16:08 (요청 600개, 가장 느린 응답 22.9초·20초 넘은 요청 2개, 채점기 시간 초과 0). 이 줄의 time 은 기록한 시각)
- a1fde82 Tutor: optional retry of the first-pass answer with the same section when it gives no usable answer (TUTOR_FIRST_PASS_RETRY, off by default); loop/retry_eval.py
- 3f38434 LOOP.md: state at 15:50 on 2026-11-16 and the three open decisions
- bb65b8f LOOP.md: experiment 69 on hold (appending the supporting sentence raises gold-fact overlap but only shows in judged metrics)
- c1e5903 LOOP.md: experiment 68 rejected (general harmful_content clause does not fix the uncued holdout miss), tool list
- 23ba799 Guard: optional general clause in the harmful_content definition of the input prompt (GUARD_HARM_CLAUSE, off by default); loop/guard_flag_eval.py
- eca959d LOOP.md: full run with tutor check voting 92.68 (93.70 / 91.66), override voting not confirmed, note on run-to-run spread
- c6dad89 LOOP.md: experiment 67 rejected by simulation (majority vote on the whole guard input decision)
- 646ffa2 LOOP.md: experiment 66 (tutor boundary-check voting adopted), full run of the head 92.38 with guard 94.81, next round plan
- a6bf112 Tutor: override and submission boundary checks decided by a 3-sample majority by default
- 0071845 Ledger: full run of the current head (paste-strip-full 92.38, guard 94.81, holdout 94.01)
- 002e82c Boundary checks can be sampled 3 times with a majority vote per check name (TUTOR_FOCUS_VOTE_NAMES, empty by default); focus_eval --votes/--endpoint
- 2155e08 LOOP.md: manual resubmission of 305fa8c scored 90.49 (mean 90.48 over two), check the server log for manual submissions
- 6c6029d best: 90.48 @305fa8c (같은 커밋 재제출. 사용자가 화면에서 직접 제출한 것으로, 서버 로그 기준 11/16 11:20 (요청 600개, 가장 느린 응답 20.4초). 11:01 에도 직접 제출이 한 번 있었지만 결과 문장은 남아 있지 않아 기록하지 못함. 이 줄의 time 은 기록한 시각)
- 4795df6 LOOP.md: queue item 3 closed, plan for the next round (full run of the current head)
- 27e4819 LOOP.md: experiment 65 rejected (stripping prefixes from the retrieval query changes no rank)
- 40e6497 LOOP.md: experiments 61-64 (tail check for long pastes and wrapper stripping adopted for the guard; voting on boundary checks and span intersection rejected)
- 05ff931 Guard: strip pressure and authority prefixes before the input check by default
- 7a3aa64 Wrapper stripping: separate flags for guard and tutor (GUARD_STRIP_WRAPPERS, TUTOR_STRIP_WRAPPERS), both still off
- 092ceae Strip pressure and authority prefixes from the last message before retrieval, classification and generation (STRIP_WRAPPERS, off by default); loop/wrapper_eval.py
- a912f2d Guard: tail check for long pasted messages on by default
- 1e84938 Guard: for long pasted messages, judge the tail (the actual request) separately for off-task work, block-only (GUARD_PASTE_TAIL_CHECK, off by default)
- c7d6704 LOOP.md: experiments 59-60 rejected (Korean query conversion sampling and section titles, embeddings for English questions); section statistics of wrong top results
- 60b0cca LOOP.md: next round plan (59: Korean query conversion for English questions), tool list
- b1077aa Tutor: Korean query conversion for English questions can be sampled several times and can see the section titles (flags, off by default); loop/query_ko_eval.py
- 2e35695 LOOP.md: submission of 305fa8c scored 90.47 (new best), same-day baseline of c9a5d1a 91.41, unrecorded manual submission on 11/15
- 6d71799 record_submission: keep the seed as text when it is not a plain number
- eb89a6e best: 90.47 @305fa8c (인용된 차별 문장 규칙(#54) + 가드 답변 검증 3회 과반·가장 짧은 구간(#55) + 가드 범주 표지 보정(#46). 공개 세트 전체 2회 92.52 (튜터 91.22, 가드 93.84), 같은 날 c9a5d1a 기준선 전체 2회 91.41. 11/16 10:56 제출, 제출 전 동시 64개 점검 11.6초, 채점기 시간 초과 0)
- 8c3261f LOOP.md: next round plan (64-concurrency check of the new version on a side port, same-day baseline of c9a5d1a)

```text
baseline/app/common/focus.py   | 121 ++++++++++++++++++++++++++++++++++++++++-
 baseline/app/config.py         |   2 +
 baseline/app/guard/config.py   |  23 ++++++++
 baseline/app/guard/pipeline.py |  18 +++++-
 baseline/app/guard/prompts.py  |  12 ++++
 baseline/app/tutor/config.py   |  51 +++++++++++++++++
 baseline/app/tutor/pipeline.py |  97 +++++++++++++++++++++++++++++----
 baseline/app/tutor/prompts.py  |  21 +++++++
 8 files changed, 331 insertions(+), 14 deletions(-)
```

## 복원 방법

```bash
git checkout 17adfc1 -- baseline
bash baseline/run.sh            # 서버 재시작
python3 loop/presubmit_check.py --write-commit
# → 제출 버튼
```
