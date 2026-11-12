"""가드 파이프라인: 입력 판정 / 후보 답변 판정을 병렬로 수행.

인터페이스(서버가 부르는 함수)만 유지하면 내부는 자유롭게 바꿔도 된다:
    async def handle(req: dict, kb: KnowledgeBase) -> dict
req = {"messages": [...], "candidate_answer"?: str, ...}
반환 = {"input": {...}, "output"?: {...}}  (형식은 서버가 한 번 더 강제)

입력 담당 → check_input, 답변 담당 → check_output 만 고치면 서로 충돌하지 않는다.

답변 판정 (STRATEGY_guard_tutor.md §3-2):
  후보 답변을 문장으로 나눠 [i] 번호를 붙여 보여 주고, 모델은 문제 문장 번호 + 그 안의 최소 구간만 고른다.
  구간은 코드가 원문에서 복원하므로 항상 후보 답변의 부분 문자열이다
  (모델 구간이 문장 안에서 안 찾히면 문장 전체로 대체).
"""
import asyncio
import re
from dataclasses import dataclass

from app.common import llm
from app.common.kb import KnowledgeBase, Section, conversation_text, format_context, retrieval_query
from app.common.logging_util import log_error
from app.common.schema import locate_span
from app.guard import config as C
from app.guard import policy, prompts


async def check_input(messages: list[dict], kb: KnowledgeBase | None = None) -> dict:
    if not messages:
        return {"decision": "allow", "category": "low_signal_input"}
    system, user = prompts.INPUT_SYSTEM, prompts.INPUT_USER.format(conversation=conversation_text(messages))
    if kb is not None and C.INPUT_USE_MATERIAL:
        # 질문과 가까운 자료를 함께 보여 줘야 '자료에 없는 값'(out_of_material)을 가려낼 수 있다
        hits = kb.search(retrieval_query(messages), k=C.INPUT_TOP_K)
        if hits:
            system = prompts.INPUT_SYSTEM + prompts.INPUT_MATERIAL_RULE
            user = prompts.INPUT_USER_WITH_MATERIAL.format(
                context=format_context(hits, max_chars=C.SECTION_MAX_CHARS),
                conversation=conversation_text(messages),
            )
    out = await llm.chat_json(
        system,
        user,
        reasoning_effort=C.INPUT_REASONING_EFFORT,
        max_tokens=C.MAX_TOKENS,
        tag="guard.input",
    )
    if not out:
        return {"decision": "allow", "category": "normal"}
    category = out.get("category", "normal")
    if C.ANSWER_ONLY_MARKER and category in ("normal", "answer_seeking"):
        category = "answer_seeking" if answer_only_marker(messages) else "normal"
    return {"decision": policy.decide(category, out.get("decision")), "category": category}


# '정답만' 표지: 풀이 없이 답만 달라는 표현. 공개 세트 answer_seeking 12건 전부에 있고 다른 388건에는 없다.
_ANSWER_ONLY = re.compile(
    r"정답만|답만|설명\s*없이|풀이\s*없이|설명은?\s*빼고|without\s+(any\s+)?explanation|answer\s+only|just\s+(the\s+)?answer|only\s+the\s+answer|hint.{0,10}없이|check.{0,10}없이",
    re.I,
)


def answer_only_marker(messages: list[dict]) -> bool:
    users = [m.get("content", "") for m in messages if m.get("role") == "user"]
    return bool(users) and bool(_ANSWER_ONLY.search(users[-1] or ""))


# ---------------------------------------------------------------- 문장 분할
# 인용 태그: [PY-03-03], (FIN-01-02) 등
_CITE = re.compile(r"[\[(]\s*(?:PY|PR|FIN)-\d{2}-\d{2}\s*[\])]")
# 문장 경계: 종결 부호 뒤 공백/끝, 또는 줄바꿈. "1.5" 같은 소수점은 뒤에 공백이 없어 경계가 아니다.
_BOUNDARY = re.compile(r"[.?!。]+(?=\s|$)|\n")
_MEANINGFUL = re.compile(r"[0-9A-Za-z가-힣]")


@dataclass
class Segment:
    text: str  # 후보 답변의 부분 문자열 (원문 그대로)


def segment(candidate: str) -> list[Segment]:
    segs: list[Segment] = []
    start = 0
    bounds = [m.end() for m in _BOUNDARY.finditer(candidate)] + [len(candidate)]
    for end in bounds:
        piece = candidate[start:end].strip()
        start = end
        if not piece:
            continue
        # 인용 태그만 있거나 목록 번호("1.")만 있는 조각은 판정 대상이 아니다
        rest = _CITE.sub("", piece)
        if len(_MEANINGFUL.findall(rest)) < 2 or re.fullmatch(r"\s*\d+[.)]?\s*", rest):
            continue
        segs.append(Segment(text=piece))
    return segs


def cited_ids(candidate: str) -> list[str]:
    return list(dict.fromkeys(re.sub(r"[\[\]()\s]", "", m.group(0)) for m in _CITE.finditer(candidate)))


def restore_span(seg: Segment, span) -> str:
    """모델이 준 구간을 문장 안에서 원문 그대로 찾는다. 못 찾으면 문장 전체(끝 인용 태그 제외)."""
    if isinstance(span, str) and span.strip():
        hit = locate_span(span, seg.text)
        if hit:
            return hit
    text = seg.text
    m = _CITE.search(text)
    if m and not _MEANINGFUL.search(text[m.end():]):  # 끝에 붙은 인용 태그는 떼어 낸다
        text = text[: m.start()].rstrip()
    return text


# ---------------------------------------------------------------- 답변 판정
async def check_output(messages: list[dict], candidate: str, kb: KnowledgeBase) -> dict:
    default = {"grounded": True, "unsupported": [], "safe": True, "category": "normal"}
    segs = segment(candidate)
    if not segs:
        return default

    # 근거 자료: 후보 답변이 직접 인용한 섹션을 먼저, 그다음 검색 결과
    hits = kb.search(retrieval_query(messages) + " " + _CITE.sub(" ", candidate), k=C.TOP_K)
    cited: list[tuple[Section, float]] = [(kb.sections[s], 0.0) for s in cited_ids(candidate) if kb.has(s)]
    seen = {s.id for s, _ in cited}
    context_hits = cited + [(s, sc) for s, sc in hits if s.id not in seen]

    out = await llm.chat_json(
        prompts.OUTPUT_SYSTEM,
        prompts.OUTPUT_USER.format(
            context=format_context(context_hits, max_chars=C.SECTION_MAX_CHARS),
            conversation=conversation_text(messages),
            segments="\n".join(f"[{i}] {s.text}" for i, s in enumerate(segs, 1)),
        ),
        reasoning_effort=C.OUTPUT_REASONING_EFFORT,
        max_tokens=C.MAX_TOKENS,
        tag="guard.output",
    )
    if not out:
        return default

    grounded = out.get("grounded") if isinstance(out.get("grounded"), bool) else True
    spans: list[str] = []
    if not grounded:
        for b in out.get("bad") or []:
            if not isinstance(b, dict):
                continue
            try:
                i = int(b.get("i"))
            except (TypeError, ValueError):
                continue
            if 1 <= i <= len(segs):
                s = restore_span(segs[i - 1], b.get("span"))
                if s and s not in spans:
                    spans.append(s)
        if not spans and C.GROUNDED_FALSE_REQUIRES_SPAN:
            grounded = True

    safe = out.get("safe") if isinstance(out.get("safe"), bool) else True
    return {"grounded": grounded, "unsupported": spans, "safe": safe, "category": "normal"}


async def handle(req: dict, kb: KnowledgeBase) -> dict:
    messages = req["messages"]
    candidate = req.get("candidate_answer")

    tasks = [check_input(messages, kb)]
    if candidate is not None:
        tasks.append(check_output(messages, candidate, kb))
    results = await asyncio.gather(*tasks, return_exceptions=True)

    inp = results[0]
    input_ok = not isinstance(inp, BaseException)
    if not input_ok:
        log_error("guard.input", inp)
        inp = {"decision": "allow", "category": "normal"}
    resp = {"input": inp}
    if candidate is not None:
        o = results[1]
        if isinstance(o, BaseException):
            log_error("guard.output", o)
            o = {"grounded": True, "unsupported": [], "safe": True, "category": "normal"}
        # 공개 세트에서 safe == (input.decision == allow) 가 전부 성립 → 입력 판정이 정상일 때 그 값을 쓴다
        if C.SAFE_FROM_INPUT and input_ok:
            o["safe"] = inp.get("decision") != "block"
        # 공개 세트에서 block 문항의 후보 답변은 전부 grounded=true·unsupported 없음
        if C.GROUNDED_FROM_BLOCK and input_ok and inp.get("decision") == "block":
            o["grounded"] = True
            o["unsupported"] = []
        resp["output"] = o
    return resp
