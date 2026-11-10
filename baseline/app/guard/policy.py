"""범주 → allow/block 정책표. 가드 담당만 수정한다.

!! 아래 기본값은 범주 이름만 보고 정한 '미검증 초안'이다.
   scripts/analyze_guard_labels.py 로 public/guard.jsonl 의 범주×결정 교차표를 보고 고친다.
   (같은 범주가 항상 같은 결정이 아닐 수 있음 → 그 경우 LLM 판정 decision 을 쓰도록
    USE_LLM_DECISION 에 범주를 넣는다.)
"""

BLOCK_CATEGORIES = {
    "academic_dishonesty",
    "jailbreak_injection",
    "pii_disclosure",
    "harmful_content",
}

# 정책표 대신 LLM 이 낸 decision 을 그대로 쓸 범주 (범주 안에서 결정이 갈리는 경우)
USE_LLM_DECISION: set[str] = set()


def decide(category: str, llm_decision: str | None) -> str:
    if category in USE_LLM_DECISION and llm_decision in ("allow", "block"):
        return llm_decision
    return "block" if category in BLOCK_CATEGORIES else "allow"
