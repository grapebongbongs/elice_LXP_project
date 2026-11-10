"""튜터 파이프라인: 분류 ∥ 검색 → action 결정 → (템플릿 | 조건부 생성) → 인용 필터.

인터페이스(서버가 부르는 함수)만 유지하면 내부는 자유롭게 바꿔도 된다:
    async def handle(req: dict, kb: KnowledgeBase) -> dict
req = {"messages": [...], "session_id": ..., "spec_version": ...}
반환 = {"answer": str, "action": str, "citations": [str]}  (형식은 서버가 한 번 더 강제)

설계 근거: STRATEGY_guard_tutor.md §1-3, §4
- 정책 판단(분류)과 생성을 분리해 'refuse 라벨 + 본문 순응' 불일치를 없앤다.
- refuse/redirect/escalate/clarify 는 고정 템플릿 → LLM 호출 없음, 순응 위험 0.
- 인용은 검색된 섹션 중에서만, 최대 MAX_CITATIONS 개.
"""
import asyncio

from app.common import llm
from app.common.kb import KnowledgeBase, Section, conversation_text, format_context
from app.common.logging_util import log_error
from app.common.normalize import compact, normalize_messages
from app.common.schema import GUARD_CATEGORIES, tutor_fallback
from app.tutor import config as C
from app.tutor import prompts

# 공개 라벨(public/tutor.jsonl)의 범주 → action 대응. harmful_content 는 harm_type 으로 분기.
TUTOR_MAP = {
    "normal": "answer",
    "pii_disclosure": "answer",
    "answer_seeking": "scaffold",
    "academic_dishonesty": "scaffold",
    "jailbreak_injection": "refuse",
    "harmful_content": "refuse",
    "off_topic": "redirect",
    "out_of_material": "abstain",
    "emotional_distress": "escalate",
    "low_signal_input": "clarify",
}
GENERATED = ("answer", "scaffold", "abstain")
CITING = ("answer", "scaffold")

_BUTTON_KEYS = {compact(b) for b in prompts.QUICK_BUTTONS}


def is_quick_button(text: str) -> bool:
    return compact(text) in _BUTTON_KEYS


# ---------------------------------------------------------------- 검색 쿼리
def build_query(messages: list[dict]) -> str:
    """버튼 문구를 뺀 학습자 발화로 쿼리를 만든다. 마지막 발화에 가중(2회)."""
    users = [m["content"] for m in messages if m.get("role") == "user" and m.get("content")]
    real = [u for u in users if not is_quick_button(u)] or users
    if not real:
        return ""
    last = real[-1]
    prev = " ".join(real[-4:-1])
    return f"{last} {last} {prev}".strip()


# ---------------------------------------------------------------- 1단계: 분류
async def classify(messages: list[dict]) -> tuple[str, str | None]:
    out = await llm.chat_json(
        prompts.CLASSIFY_SYSTEM,
        prompts.CLASSIFY_USER.format(conversation=conversation_text(messages)),
        reasoning_effort=C.CLASSIFY_REASONING_EFFORT,
        max_tokens=C.CLASSIFY_MAX_TOKENS,
    )
    if not out:
        return "normal", None  # 분류 실패 시 다수 범주(normal)로 진행 — 생성 프롬프트가 2차 방어
    category = out.get("category")
    if category not in GUARD_CATEGORIES:
        category = "normal"
    harm_type = out.get("harm_type") if out.get("harm_type") in ("hate", "actionable") else None
    return category, harm_type


def decide_action(category: str, harm_type: str | None) -> str:
    action = TUTOR_MAP.get(category, "answer")
    if category == "harmful_content" and harm_type == "hate":
        action = "redirect"
    return action


def template_answer(action: str, category: str, harm_type: str | None) -> dict:
    key = action
    if action == "redirect" and category == "harmful_content" and harm_type == "hate":
        key = "redirect_hate"
    return {"answer": prompts.TEMPLATES[key], "action": action, "citations": []}


# ---------------------------------------------------------------- 인용 필터
def filter_citations(raw, action: str, hits: list[tuple[Section, float]]) -> list[str]:
    if action not in CITING:
        return []
    order = [s.id for s, _ in hits]
    retrieved = set(order)
    cits: list[str] = []
    for c in raw if isinstance(raw, list) else []:
        if isinstance(c, str) and c in retrieved and c not in cits:  # 검색된 섹션만 허용
            cits.append(c)
    if not cits and action == "answer" and order:
        cits = [order[0]]  # answer 라벨은 항상 근거 1개 → 모델이 빠뜨리면 검색 1위
    return cits[: C.MAX_CITATIONS]


# ---------------------------------------------------------------- 2단계: 생성
async def generate(action: str, messages: list[dict], hits) -> dict | None:
    user = prompts.GEN_USER.format(
        context=format_context(hits, max_chars=C.SECTION_MAX_CHARS),
        conversation=conversation_text(messages),
    )
    return await llm.chat_json(
        prompts.GEN_SYSTEMS[action], user, reasoning_effort=C.REASONING_EFFORT, max_tokens=C.MAX_TOKENS
    )


async def handle(req: dict, kb: KnowledgeBase) -> dict:
    messages = normalize_messages(req["messages"])
    if not messages:
        return tutor_fallback()

    # 분류(LLM)를 먼저 띄워 두고 그동안 검색 → 지연 은닉
    cls_task = asyncio.create_task(classify(messages))
    hits = kb.search(build_query(messages), k=C.TOP_K)
    try:
        category, harm_type = await cls_task
    except Exception as e:  # 분류 오류는 normal 로 진행
        log_error("tutor.classify", e)
        category, harm_type = "normal", None

    action = decide_action(category, harm_type)
    if action not in GENERATED:
        return template_answer(action, category, harm_type)

    out = await generate(action, messages, hits)
    if not out or not isinstance(out.get("answer"), str) or not out["answer"].strip():
        return tutor_fallback()

    # answer 경로에서만 모델의 abstain 강등을 허용 (근거 부재)
    final_action = action
    if action == "answer" and out.get("action") == "abstain":
        final_action = "abstain"

    answer = out["answer"].strip()
    if final_action == "answer" and category == "pii_disclosure" and prompts.PII_NOTE not in answer:
        answer = f"{answer} {prompts.PII_NOTE}"

    return {
        "answer": answer,
        "action": final_action,
        "citations": filter_citations(out.get("citations"), final_action, hits),
    }
