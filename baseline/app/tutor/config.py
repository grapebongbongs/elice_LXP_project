"""튜터 파트 전용 튜닝 값. 튜터 담당만 수정한다."""
import os

TOP_K = int(os.getenv("TUTOR_TOP_K", "5"))               # 검색 섹션 수
SECTION_MAX_CHARS = int(os.getenv("TUTOR_SECTION_CHARS", "3000"))
REASONING_EFFORT = os.getenv("TUTOR_REASONING_EFFORT", "low")  # minimal|low|medium (생성)
MAX_TOKENS = int(os.getenv("TUTOR_MAX_TOKENS", "2000"))

# 튜터 전용 하이브리드 검색. 기본 1536은 text-embedding-3-small의 기본 차원이다.
# 512 비교는 별도 후보 실험에서만 TUTOR_EMBEDDING_DIMENSIONS=512로 실행한다.
EMBEDDING_ENABLED = os.getenv("TUTOR_EMBEDDING_ENABLED", "1") == "1"
EMBEDDING_DIMENSIONS = int(os.getenv("TUTOR_EMBEDDING_DIMENSIONS", "1536"))
HYBRID_BM25_K = int(os.getenv("TUTOR_HYBRID_BM25_K", "10"))
HYBRID_DENSE_K = int(os.getenv("TUTOR_HYBRID_DENSE_K", "3"))
HYBRID_RRF_K = int(os.getenv("TUTOR_HYBRID_RRF_K", "60"))

# 1단계 분류
CLASSIFY_REASONING_EFFORT = os.getenv("TUTOR_CLASSIFY_REASONING_EFFORT", "low")
CLASSIFY_MAX_TOKENS = int(os.getenv("TUTOR_CLASSIFY_MAX_TOKENS", "1500"))

# 인용 상한: 공개 라벨에서 answer 는 항상 1개, scaffold 는 0~1개
MAX_CITATIONS = int(os.getenv("TUTOR_MAX_CITATIONS", "1"))

# 1단계 분류에 검색 자료를 함께 넣어 out_of_material 을 판별 (0 이면 대화만 보고 분류)
CLASSIFY_USE_MATERIAL = os.getenv("TUTOR_CLASSIFY_USE_MATERIAL", "1") == "1"
CLASSIFY_TOP_K = int(os.getenv("TUTOR_CLASSIFY_TOP_K", "3"))
# abstain 답변의 citations 에 검색 1위 섹션을 넣는다 (가까운 주제 안내의 출처)
ABSTAIN_CITE_TOP = os.getenv("TUTOR_ABSTAIN_CITE_TOP", "1") == "1"

# 마지막 질문에서 영문자 비율이 이 값 이상이면 검색어를 한국어로 바꿔 한 번 더 검색한다 (0 이면 끔)
ENGLISH_QUERY_RATIO = float(os.getenv("TUTOR_ENGLISH_QUERY_RATIO", "0.5"))

# answer 경로에서 모델이 옮긴 근거 문장(quote)이 검색 자료에 실제로 있는지 코드로 확인한다.
# 없으면 지어낸 답으로 보고 abstain 으로 바꾼다. 있으면 그 문장이 든 섹션을 인용한다. (0 이면 끔)
QUOTE_CHECK = os.getenv("TUTOR_QUOTE_CHECK", "1") == "1"
# quote 검증은 유지하되, 조사·어미·표기 차이로 정상 답변이 abstain 되는 경우를 줄이기 위한 후보값이다.
QUOTE_MIN_COVERAGE = float(os.getenv("TUTOR_QUOTE_MIN_COVERAGE", "0.7"))  # quote 글자 bigram 중 섹션에 있는 비율
