"""튜터 파트 전용 튜닝 값. 튜터 담당만 수정한다."""
import os

TOP_K = int(os.getenv("TUTOR_TOP_K", "5"))               # 검색 섹션 수
SECTION_MAX_CHARS = int(os.getenv("TUTOR_SECTION_CHARS", "3000"))
REASONING_EFFORT = os.getenv("TUTOR_REASONING_EFFORT", "low")  # minimal|low|medium (생성)
MAX_TOKENS = int(os.getenv("TUTOR_MAX_TOKENS", "2000"))

# 1단계 분류
CLASSIFY_REASONING_EFFORT = os.getenv("TUTOR_CLASSIFY_REASONING_EFFORT", "low")
CLASSIFY_MAX_TOKENS = int(os.getenv("TUTOR_CLASSIFY_MAX_TOKENS", "1500"))

# 인용 상한: 공개 라벨에서 answer 는 항상 1개, scaffold 는 0~1개
MAX_CITATIONS = int(os.getenv("TUTOR_MAX_CITATIONS", "1"))

# 1단계 분류에 검색 자료를 함께 넣어 out_of_material 을 판별 (0 이면 대화만 보고 분류)
CLASSIFY_USE_MATERIAL = os.getenv("TUTOR_CLASSIFY_USE_MATERIAL", "0") == "1"
CLASSIFY_TOP_K = int(os.getenv("TUTOR_CLASSIFY_TOP_K", "3"))
# abstain 답변의 citations 에 검색 1위 섹션을 넣는다 (가까운 주제 안내의 출처)
ABSTAIN_CITE_TOP = os.getenv("TUTOR_ABSTAIN_CITE_TOP", "1") == "1"

# 마지막 질문에서 영문자 비율이 이 값 이상이면 검색어를 한국어로 바꿔 한 번 더 검색한다 (0 이면 끔)
ENGLISH_QUERY_RATIO = float(os.getenv("TUTOR_ENGLISH_QUERY_RATIO", "0"))

# answer 경로에서 모델이 옮긴 근거 문장(quote)이 검색 자료에 실제로 있는지 코드로 확인한다.
# 없으면 지어낸 답으로 보고 abstain 으로 바꾼다. 있으면 그 문장이 든 섹션을 인용한다. (0 이면 끔)
QUOTE_CHECK = os.getenv("TUTOR_QUOTE_CHECK", "1") == "1"

# answer는 FIRST_PASS_K개 섹션으로 한 번만 생성. 근거 실패 시 abstain.
ANSWER_TOP1_FIRST = os.getenv("TUTOR_ANSWER_TOP1_FIRST", "1") == "1"
# academic_dishonesty 는 고정 힌트 문장으로 응대(LLM 생성 없음)
FIXED_DELEGATION_HINT = os.getenv("TUTOR_FIXED_DELEGATION_HINT", "1") == "1"
# 검색어에 직전 튜터 답을 넣는다(이어지는 질문의 대상 찾기)
QUERY_PREV_ANSWER = os.getenv("TUTOR_QUERY_PREV_ANSWER", "1") == "1"

# normal/answer_seeking 은 '정답만' 표지 유무로 정한다(공개 세트 400건에서 예외 없음)
ANSWER_ONLY_MARKER = os.getenv("TUTOR_ANSWER_ONLY_MARKER", "1") == "1"

# 2026-11-16 참고 방법론에서 도입. 점수는 이 저장소에서 재검증해야 한다.
FIRST_PASS_K = max(1, int(os.getenv("TUTOR_FIRST_PASS_K", "1")))
APPEND_QUOTE = os.getenv("TUTOR_APPEND_QUOTE", "1") == "1"
LOW_SIGNAL_RULE = os.getenv("LOW_SIGNAL_RULE", "1") == "1"
NAMED_RULE_CHECK = os.getenv("TUTOR_NAMED_RULE_CHECK", "1") == "1"
HARM_TYPE_RULE = os.getenv("HARM_TYPE_RULE", "1") == "1"
FOCUS_NAMES = tuple(n.strip() for n in os.getenv(
    "TUTOR_FOCUS_NAMES", "injection,privacy,fraud,delegation,offtask"
).split(",") if n.strip() in ("injection", "privacy", "fraud", "delegation", "offtask"))
SPECULATIVE_GENERATION = os.getenv("TUTOR_SPECULATIVE_GENERATION", "1") == "1"
