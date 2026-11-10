"""가드 파트 전용 튜닝 값. 가드 담당만 수정한다."""
import os

TOP_K = int(os.getenv("GUARD_TOP_K", "4"))
SECTION_MAX_CHARS = int(os.getenv("GUARD_SECTION_CHARS", "2500"))
INPUT_REASONING_EFFORT = os.getenv("GUARD_INPUT_REASONING_EFFORT", "low")
OUTPUT_REASONING_EFFORT = os.getenv("GUARD_OUTPUT_REASONING_EFFORT", "low")
MAX_TOKENS = int(os.getenv("GUARD_MAX_TOKENS", "3000"))
