"""범주 → allow/block 정책표. 가드 담당만 수정한다.

public/guard.jsonl(200건) 범주×결정 교차표로 확정 (STRATEGY_guard_tutor.md §1-1):
  block : academic_dishonesty 7, harmful_content 26, jailbreak_injection 16, off_topic 7
  allow : normal 114, out_of_material 12, pii_disclosure 7, answer_seeking 4,
          emotional_distress 4, low_signal_input 3
같은 범주 안에서 결정이 갈리는 경우가 없어 USE_LLM_DECISION 은 비워 둔다.
  - off_topic      : 수업과 무관한 요청(광고 문구·그림·게임 공략) → block
  - pii_disclosure : 학습자가 '자기' 이름·학교를 밝힌 경우 → allow
                     (남의 개인정보를 캐는 요청은 harmful_content 로 분류되어 block)
"""

BLOCK_CATEGORIES = {
    "academic_dishonesty",
    "jailbreak_injection",
    "harmful_content",
    "off_topic",
}

# 정책표 대신 LLM 이 낸 decision 을 그대로 쓸 범주 (범주 안에서 결정이 갈리는 경우)
USE_LLM_DECISION: set[str] = set()


def decide(category: str, llm_decision: str | None) -> str:
    if category in USE_LLM_DECISION and llm_decision in ("allow", "block"):
        return llm_decision
    return "block" if category in BLOCK_CATEGORIES else "allow"
