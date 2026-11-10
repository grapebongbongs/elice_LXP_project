"""튜터 파트 전용 튜닝 값. 튜터 담당만 수정한다."""
import os

TOP_K = int(os.getenv("TUTOR_TOP_K", "5"))               # 검색 섹션 수
SECTION_MAX_CHARS = int(os.getenv("TUTOR_SECTION_CHARS", "2500"))
REASONING_EFFORT = os.getenv("TUTOR_REASONING_EFFORT", "low")  # minimal|low|medium
MAX_TOKENS = int(os.getenv("TUTOR_MAX_TOKENS", "2000"))
