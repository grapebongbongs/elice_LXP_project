"""가드 파이프라인: 입력 판정 / 후보 답변 판정을 병렬로 수행.

인터페이스(서버가 부르는 함수)만 유지하면 내부는 자유롭게 바꿔도 된다:
    async def handle(req: dict, kb: KnowledgeBase) -> dict
req = {"messages": [...], "candidate_answer"?: str, ...}
반환 = {"input": {...}, "output"?: {...}}  (형식은 서버가 한 번 더 강제)

입력 담당 → check_input, 답변 담당 → check_output 만 고치면 서로 충돌하지 않는다.
"""
import asyncio

from app.common import llm
from app.common.kb import KnowledgeBase, conversation_text, format_context, retrieval_query
from app.common.logging_util import log_error
from app.guard import config as C
from app.guard import policy, prompts


async def check_input(messages: list[dict]) -> dict:
    if not messages:
        return {"decision": "allow", "category": "low_signal_input"}
    out = await llm.chat_json(
        prompts.INPUT_SYSTEM,
        prompts.INPUT_USER.format(conversation=conversation_text(messages)),
        reasoning_effort=C.INPUT_REASONING_EFFORT,
        max_tokens=C.MAX_TOKENS,
    )
    if not out:
        return {"decision": "allow", "category": "normal"}
    category = out.get("category", "normal")
    return {"decision": policy.decide(category, out.get("decision")), "category": category}


async def check_output(messages: list[dict], candidate: str, kb: KnowledgeBase) -> dict:
    default = {"grounded": True, "unsupported": [], "safe": True, "category": "normal"}
    if not candidate.strip():
        return default
    hits = kb.search(retrieval_query(messages) + " " + candidate, k=C.TOP_K)
    out = await llm.chat_json(
        prompts.OUTPUT_SYSTEM,
        prompts.OUTPUT_USER.format(
            context=format_context(hits, max_chars=C.SECTION_MAX_CHARS),
            conversation=conversation_text(messages),
            candidate=candidate,
        ),
        reasoning_effort=C.OUTPUT_REASONING_EFFORT,
        max_tokens=C.MAX_TOKENS,
    )
    if not out:
        return default
    if out.get("grounded") is True:
        out["unsupported"] = []
    return out


async def handle(req: dict, kb: KnowledgeBase) -> dict:
    messages = req["messages"]
    candidate = req.get("candidate_answer")

    tasks = [check_input(messages)]
    if candidate is not None:
        tasks.append(check_output(messages, candidate, kb))
    results = await asyncio.gather(*tasks, return_exceptions=True)

    inp = results[0]
    if isinstance(inp, BaseException):
        log_error("guard.input", inp)
        inp = {"decision": "allow", "category": "normal"}
    resp = {"input": inp}
    if candidate is not None:
        o = results[1]
        if isinstance(o, BaseException):
            log_error("guard.output", o)
            o = {"grounded": True, "unsupported": [], "safe": True, "category": "normal"}
        resp["output"] = o
    return resp
