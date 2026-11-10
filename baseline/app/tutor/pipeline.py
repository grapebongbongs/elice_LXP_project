"""튜터 파이프라인: 검색 → LLM 1회 호출 → 후처리.

인터페이스(서버가 부르는 함수)만 유지하면 내부는 자유롭게 바꿔도 된다:
    async def handle(req: dict, kb: KnowledgeBase) -> dict
req = {"messages": [...], "session_id": ..., "spec_version": ...}
반환 = {"answer": str, "action": str, "citations": [str]}  (형식은 서버가 한 번 더 강제)
"""
from app.common import llm
from app.common.kb import KnowledgeBase, conversation_text, format_context, retrieval_query
from app.common.schema import tutor_fallback
from app.tutor import config as C
from app.tutor import prompts


async def handle(req: dict, kb: KnowledgeBase) -> dict:
    messages = req["messages"]
    if not messages:
        return tutor_fallback()

    hits = kb.search(retrieval_query(messages), k=C.TOP_K)
    context = format_context(hits, max_chars=C.SECTION_MAX_CHARS)
    user = prompts.USER_TEMPLATE.format(context=context, conversation=conversation_text(messages))

    out = await llm.chat_json(
        prompts.SYSTEM, user, reasoning_effort=C.REASONING_EFFORT, max_tokens=C.MAX_TOKENS
    )
    if not out:
        return tutor_fallback()

    # 인용은 검색된 섹션 + KB 에 존재하는 ID 로만 제한
    retrieved = {s.id for s, _ in hits}
    cits = [c for c in (out.get("citations") or []) if isinstance(c, str) and (c in retrieved or kb.has(c))]
    if out.get("action") not in ("answer", "scaffold"):
        cits = []
    out["citations"] = cits
    return out
