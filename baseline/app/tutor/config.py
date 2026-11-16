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
ENGLISH_QUERY_RATIO = float(os.getenv("TUTOR_ENGLISH_QUERY_RATIO", "0.5"))

# answer 경로에서 모델이 옮긴 근거 문장(quote)이 검색 자료에 실제로 있는지 코드로 확인한다.
# 없으면 지어낸 답으로 보고 abstain 으로 바꾼다. 있으면 그 문장이 든 섹션을 인용한다. (0 이면 끔)
QUOTE_CHECK = os.getenv("TUTOR_QUOTE_CHECK", "1") == "1"
QUOTE_MIN_COVERAGE = float(os.getenv("TUTOR_QUOTE_MIN_COVERAGE", "0.8"))  # quote 글자 bigram 중 섹션에 있는 비율

# answer 를 검색 1위 섹션 하나로 먼저 생성(근거 문장을 못 찾으면 상위 TOP_K 로 재생성)
ANSWER_TOP1_FIRST = os.getenv("TUTOR_ANSWER_TOP1_FIRST", "1") == "1"
# academic_dishonesty 는 고정 힌트 문장으로 응대(LLM 생성 없음)
FIXED_DELEGATION_HINT = os.getenv("TUTOR_FIXED_DELEGATION_HINT", "1") == "1"
# 검색어에 직전 튜터 답을 넣는다(이어지는 질문의 대상 찾기)
QUERY_PREV_ANSWER = os.getenv("TUTOR_QUERY_PREV_ANSWER", "1") == "1"

# normal/answer_seeking 은 '정답만' 표지 유무로 정한다(공개 세트 400건에서 예외 없음)
ANSWER_ONLY_MARKER = os.getenv("TUTOR_ANSWER_ONLY_MARKER", "1") == "1"
