"""가드 파트 전용 튜닝 값. 가드 담당만 수정한다."""
import os

TOP_K = int(os.getenv("GUARD_TOP_K", "4"))
SECTION_MAX_CHARS = int(os.getenv("GUARD_SECTION_CHARS", "3000"))
INPUT_REASONING_EFFORT = os.getenv("GUARD_INPUT_REASONING_EFFORT", "medium")  # 입력 분류만 medium (답변 검증은 low, #10에서 medium은 시간 초과)
OUTPUT_REASONING_EFFORT = os.getenv("GUARD_OUTPUT_REASONING_EFFORT", "low")
# 입력 분류(medium) 호출이 INPUT_BACKUP_AFTER 초 안에 끝나지 않으면 같은 프롬프트를 INPUT_BACKUP_EFFORT 로 한 번 더 보내 먼저 끝난 쪽을 쓴다.
# (비워 두면 끔: 그때는 공통 설정 LLM_HEDGE_MEDIUM 초 뒤에 medium 호출을 한 번 더 보낸다.)
# 11/13 낮 시간대에 medium 호출이 p90 13초·p95 15~18초까지 느려져, 동시 64개 점검에서 가드 요청이 20~26초 걸리고 한 요청은 25초 한도에 걸렸다.
# 실측(11/13 낮, API 가 느리던 때): 동시 64개에서 25초 한도에 걸린 요청 4개(끄면 10개). 가드만 2회 93.53 / 91.40 (끈 상태 93.08 / 91.36),
# 입력 판정의 18%가 low 답으로 처리됐고 시간 초과 0건. 평소(medium p95 7초)에는 보조 호출이 거의 나가지 않는다.
INPUT_BACKUP_EFFORT = os.getenv("GUARD_INPUT_BACKUP_EFFORT", "low")
INPUT_BACKUP_AFTER = float(os.getenv("GUARD_INPUT_BACKUP_AFTER", "8"))
MAX_TOKENS = int(os.getenv("GUARD_MAX_TOKENS", "2000"))

# 후보 답변 판정을 몇 번 불러 합칠지 (1 이면 한 번만). app/guard/pipeline.py 의 combine_votes
# 같은 판정을 동시에 보내 '근거 없음' 여부는 과반으로, 구간은 근거 없음이라고 한 표 중 가장 짧은 것으로 정한다.
# 한 번의 판정은 같은 문항에서도 실행마다 달라진다(근거 없는 답을 놓치는 문항이 4회 중 1~3회, 구간이 문장 전체로 나오는 일도 실행마다 다름).
# 저장된 가드 실행 20회의 판정을 3개씩 묶어 계산(LLM 호출 없음, allow 문항 67개의 (근거 여부 정확도 + 구간 F1)/2):
#   tune 0.885 → 0.911, holdout 0.923 → 0.949. 가드 점수로 +0.6.
# 실측(전체 2회, 3번): 가드 94.01 / 93.66 (한 번일 때 4회 평균 92.34), 근거 여부 정확도 0.977 / 0.977, 구간 F1 0.908 / 0.892.
#   근거 없는 답을 놓친 문항이 tune 회당 2.4(저장된 20회 평균) → 1.5개, holdout 0개, 근거 있는 답을 잘못 짚은 문항 0개. 가장 느린 응답 14.3초(동시 16개).
# 세 호출 중 두 표가 모이면 끝나므로 보통 2.1번의 응답만 기다린다(느린 호출 하나는 기다리지 않아도 된다).
OUTPUT_SAMPLES = int(os.getenv("GUARD_OUTPUT_SAMPLES", "3"))

# 긴 글을 붙여 넣은 발화(PASTE_MIN_CHARS 자 이상)는 끝 PASTE_TAIL_CHARS 자만 보여 주고 '수업 밖 작업인가'를 따로 판정한다.
# 막는 쪽으로만 쓴다(주 분류가 허용 범주일 때 offtask 판정이면 off_topic). app/common/focus.py 의 PASTE_SYSTEM
# 실측(가드 2회, guard-paste-tail): 저장된 8회 실행에서 모두 놓치던 holdout off_topic 문항을 2회 모두 막았고, 허용 범주인 긴 발화
# 11문항(22회 판정)에서 잘못 막은 것은 0건. 가드 93.97 / 93.74 (직전 94.01 / 93.66), holdout 가드 95.72 / 97.97.
# 실제 제출(11/16 16:40, 커밋 89fd6df: 이 판정 + 말머리 떼기 + 튜터 경계 판정 과반): 가드 88.90. 같은 날 305fa8c 의 가드는 91.70 / 92.65 / 92.25.
# 채점 중 서버 오류는 없었다. 채점 요청은 공개 세트보다 길어서(가드 요청 본문 p75 650바이트·p90 1070바이트, 공개 세트 492·789)
# 이 판정이 공개 세트에서 본 것보다 많은 문항에 걸렸을 수 있다. 어느 변경 때문인지 가려지기 전까지 꺼 둔다.
PASTE_TAIL_CHECK = os.getenv("GUARD_PASTE_TAIL_CHECK", "0") == "1"
PASTE_MIN_CHARS = int(os.getenv("GUARD_PASTE_MIN_CHARS", "300"))
PASTE_TAIL_CHARS = int(os.getenv("GUARD_PASTE_TAIL_CHARS", "240"))

# 입력 판정(주 분류와 경계 판정)에 넘기기 전에 마지막 발화에서 압박·권한 말머리("거절하지 말고", "나 이 튜터 관리자야",
# "Do not refuse")를 뗀다. app/common/focus.py 의 WRAPPER. 말머리는 정답 범주를 바꾸지 않는데 분류기는 말머리가 붙으면 막는 쪽으로 기운다.
# 말머리가 있는 가드 24문항에서 켠 것과 끈 것을 같은 시각에 3회씩 비교(loop/wrapper_eval.py):
#   오차단 tune 9/54 → 3/54, holdout 2/18 → 0/18, 놓침은 양쪽 모두 0.
# 실측(가드 2회, guard-strip-wrappers): 말머리가 있는 정상 문항의 오차단이 2회 합쳐 5건 → 2건, 말머리가 있는 막아야 할 문항의 놓침 0건.
#   가드 96.19 / 92.89 (회차 차이는 말머리와 무관한 문항의 판정이 회차마다 달라진 것).
# 실제 제출(11/16 16:40, 커밋 89fd6df)에서 가드가 88.90 으로 305fa8c(92.2)보다 3.3 낮았다(위 PASTE_TAIL_CHECK 설명 참고). 가려지기 전까지 꺼 둔다.
STRIP_WRAPPERS = os.getenv("GUARD_STRIP_WRAPPERS", "0") == "1"

# 입력 판정 지시문의 harmful_content 정의에 일반적인 한 줄을 더한다 (app/guard/prompts.py 의 HARM_CLAUSE). 0 이면 기존 지시문.
HARM_CLAUSE = os.getenv("GUARD_HARM_CLAUSE", "0") == "1"

# output.safe 를 입력 판정에서 유도 (safe = decision == "allow").
# 공개 세트 후보 답변 86건 전부에서 성립 (STRATEGY_guard_tutor.md §1-2). 0 이면 LLM 판정 사용.
SAFE_FROM_INPUT = os.getenv("GUARD_SAFE_FROM_INPUT", "1") == "1"

# grounded=false 인데 복원 가능한 구간이 하나도 없을 때 grounded=true 로 보정할지.
# holdout 에서 0/1 을 비교해 정한다 (기본: 모델 판단 유지).
GROUNDED_FALSE_REQUIRES_SPAN = os.getenv("GUARD_GROUNDED_FALSE_REQUIRES_SPAN", "0") == "1"

# 입력 판정에 검색 자료를 함께 넣어 out_of_material 을 판별한다 (0 이면 대화만 보고 판정).
INPUT_USE_MATERIAL = os.getenv("GUARD_INPUT_USE_MATERIAL", "1") == "1"
INPUT_TOP_K = int(os.getenv("GUARD_INPUT_TOP_K", "3"))

# 입력이 block 이면 후보 답변은 grounded=true, unsupported=[] 로 둔다.
# 공개 세트에서 block 이면서 후보 답변이 있는 19건 전부가 grounded=true·unsupported 없음
# (막힌 요청에 대한 답은 수업 사실을 지어낸 것으로 보지 않음). 0 이면 LLM 판정 사용.
GROUNDED_FROM_BLOCK = os.getenv("GUARD_GROUNDED_FROM_BLOCK", "1") == "1"

# OUTPUT_ALIGN: 답변 검증에서 후보 답변의 문장마다 뜻이 가장 가까운 자료 문장을 이 개수만큼 찾아 함께 보여 준다(0 이면 끔).
#   자료 문장 임베딩은 서버를 띄울 때 만들고(app/common/embed.py), 요청마다 후보 답변의 문장만 임베딩한다(호출 1번).
#   임베딩 호출이 실패하거나 늦으면 붙이지 않고 그대로 판정한다.
# OUTPUT_ALIGN_SCOPE: context = 판정에 보여 주는 섹션 안에서만 찾는다, all = 자료 전체에서 찾는다.
OUTPUT_ALIGN = int(os.getenv("GUARD_OUTPUT_ALIGN", "0"))
OUTPUT_ALIGN_SCOPE = os.getenv("GUARD_OUTPUT_ALIGN_SCOPE", "context")
