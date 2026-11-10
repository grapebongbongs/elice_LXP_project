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
CLASSIFY_USE_MATERIAL = os.getenv("TUTOR_CLASSIFY_USE_MATERIAL", "1") == "1"
CLASSIFY_TOP_K = int(os.getenv("TUTOR_CLASSIFY_TOP_K", "3"))
# abstain 답변의 citations 에 검색 1위 섹션을 넣는다 (가까운 주제 안내의 출처)
ABSTAIN_CITE_TOP = os.getenv("TUTOR_ABSTAIN_CITE_TOP", "1") == "1"
