"""가드 파트 전용 튜닝 값. 가드 담당만 수정한다."""
import os

TOP_K = int(os.getenv("GUARD_TOP_K", "4"))
SECTION_MAX_CHARS = int(os.getenv("GUARD_SECTION_CHARS", "3000"))
INPUT_REASONING_EFFORT = os.getenv("GUARD_INPUT_REASONING_EFFORT", "medium")  # 입력 분류만 medium (답변 검증은 low, #10에서 medium은 시간 초과)
OUTPUT_REASONING_EFFORT = os.getenv("GUARD_OUTPUT_REASONING_EFFORT", "low")
MAX_TOKENS = int(os.getenv("GUARD_MAX_TOKENS", "2000"))

# output.safe 를 입력 판정에서 유도 (safe = decision == "allow").
# 공개 세트 후보 답변 86건 전부에서 성립 (STRATEGY_guard_tutor.md §1-2). 0 이면 LLM 판정 사용.
SAFE_FROM_INPUT = os.getenv("GUARD_SAFE_FROM_INPUT", "1") == "1"

# grounded=false 인데 복원 가능한 구간이 하나도 없을 때 grounded=true 로 보정할지.
# holdout 에서 0/1 을 비교해 정한다 (기본: 모델 판단 유지).
GROUNDED_FALSE_REQUIRES_SPAN = os.getenv("GUARD_GROUNDED_FALSE_REQUIRES_SPAN", "0") == "1"

# 입력 판정에 검색 자료를 함께 넣어 out_of_material 을 판별한다 (0 이면 대화만 보고 판정).
INPUT_USE_MATERIAL = os.getenv("GUARD_INPUT_USE_MATERIAL", "1") == "1"
INPUT_TOP_K = int(os.getenv("GUARD_INPUT_TOP_K", "1"))

# 입력이 block 이면 후보 답변은 grounded=true, unsupported=[] 로 둔다.
# 공개 세트에서 block 이면서 후보 답변이 있는 19건 전부가 grounded=true·unsupported 없음
# (막힌 요청에 대한 답은 수업 사실을 지어낸 것으로 보지 않음). 0 이면 LLM 판정 사용.
GROUNDED_FROM_BLOCK = os.getenv("GUARD_GROUNDED_FROM_BLOCK", "1") == "1"

# normal/answer_seeking 은 '정답만' 표지 유무로 정한다(공개 세트 400건에서 예외 없음)
ANSWER_ONLY_MARKER = os.getenv("GUARD_ANSWER_ONLY_MARKER", "1") == "1"
