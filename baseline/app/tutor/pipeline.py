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
import re

from app.common import focus, llm
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
    q = f"{last} {last} {prev}".strip()
    if C.QUERY_PREV_ANSWER:
        # "그 카드는…"처럼 이어지는 질문: 직전 튜터 답에 대상 이름이 있으므로 검색어에 한 번 넣는다
        tutor_turns = [m["content"] for m in messages if m.get("role") == "assistant" and m.get("content")]
        if tutor_turns:
            q = f"{q} {tutor_turns[-1]}"
    return q


def english_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum(c.isascii() for c in letters) / len(letters)


async def korean_query(text: str) -> str:
    out = await llm.chat_json(
        prompts.QUERY_KO_SYSTEM, f"<질문>\n{text}\n</질문>", reasoning_effort="minimal", max_tokens=400, tag="tutor.query_ko"
    )
    q = out.get("query") if out else None
    return q.strip() if isinstance(q, str) else ""


async def retrieve(messages: list[dict], kb: KnowledgeBase):
    """BM25 검색. 영어 위주 질문이면 한국어 검색어로 한 번 더 찾아 앞에 합친다."""
    query = build_query(messages)
    hits = kb.search(query, k=C.TOP_K)
    users = [m["content"] for m in messages if m.get("role") == "user" and m.get("content")]
    last = users[-1] if users else ""
    if C.ENGLISH_QUERY_RATIO > 0 and english_ratio(last) >= C.ENGLISH_QUERY_RATIO:
        try:
            ko = await korean_query(last)
        except Exception as e:
            log_error("tutor.query_ko", e)
            ko = ""
        if ko:
            ko_hits = kb.search(f"{ko} {ko} {query}", k=C.TOP_K)
            seen = {s.id for s, _ in ko_hits}
            hits = (ko_hits + [(s, sc) for s, sc in hits if s.id not in seen])[: C.TOP_K]
    return hits


# ---------------------------------------------------------------- 1단계: 분류
async def classify(messages: list[dict], hits=None) -> tuple[str, str | None]:
    if hits and C.CLASSIFY_USE_MATERIAL:
        system = prompts.CLASSIFY_SYSTEM_WITH_MATERIAL
        user = prompts.CLASSIFY_USER_WITH_MATERIAL.format(
            context=format_context(hits[: C.CLASSIFY_TOP_K], max_chars=C.SECTION_MAX_CHARS),
            conversation=conversation_text(messages),
        )
    else:
        system = prompts.CLASSIFY_SYSTEM
        user = prompts.CLASSIFY_USER.format(conversation=conversation_text(messages))
    async def primary():
        try:
            return await llm.chat_json(
                system, user, reasoning_effort=C.CLASSIFY_REASONING_EFFORT,
                max_tokens=C.CLASSIFY_MAX_TOKENS, tag="tutor.classify",
            )
        except Exception as exc:
            log_error("tutor.classify", exc)
            return None
    out, checks = await asyncio.gather(primary(), focus.check(messages, C.FOCUS_NAMES))
    out = out or {}
    category = out.get("category")
    if category not in GUARD_CATEGORIES:
        category = "normal"
    harm_type = out.get("harm_type") if out.get("harm_type") in ("hate", "actionable") else None
    category = focus.combine(category, checks)
    if C.HARM_TYPE_RULE and category == "harmful_content":
        # 실행 요청 판정을 보존한다. 모욕·비하 또는 유형 누락만 redirect로 보정한다.
        if any(checks.get(n) == "harmful_content" for n in ("privacy", "fraud")):
            harm_type = "actionable"
        elif harm_type is None:
            harm_type = "hate"
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
    if action == "abstain":
        # 자료에 없다고 답하면서 가까운 주제를 안내하므로, 그 안내의 출처(검색 1위)를 밝힌다
        return [hits[0][0].id] if hits and C.ABSTAIN_CITE_TOP else []
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


# ---------------------------------------------------------------- 근거 문장 확인
def locate_quote(quote, hits) -> str | None:
    """quote 가 검색된 섹션 중 어디에 있는지 찾아 섹션 id 를 돌려준다. 못 찾으면 None."""
    q = quote.strip() if isinstance(quote, str) else ""
    if len(q) < 4:
        return None
    for s, _ in hits:
        if q in s.text:
            return s.id
    return None


def append_quote(answer: str, quote, citations: list[str], hits) -> str:
    """인용한 섹션에 실제로 있는 원문 하나만 추가한다."""
    if not C.APPEND_QUOTE or not isinstance(quote, str):
        return answer
    q = quote.strip()
    sid = locate_quote(q, hits)
    if sid is None or sid not in citations or q in answer:
        return answer
    suffix = f'\n\n자료에는 이렇게 적혀 있어요: "{q}"'
    # 응답 규격의 4000자 절단 때문에 근거가 반만 남는 것을 막는다.
    return answer + suffix if len(answer + suffix) <= 4000 else answer


_NAMED_RULE = re.compile(r"[\"'“‘]([^\"'”’\n]{2,60})[\"'”’]\s*(?:이라는\s*|라는\s*)?(?:규칙|기준|등급)|([가-힣A-Za-z0-9_-]{2,30})\s+(?:규칙|기준|등급)")


def missing_named_rule(messages: list[dict], hits) -> bool:
    if not C.NAMED_RULE_CHECK or not hits:
        return False
    text = focus.last_user(messages)
    material = compact(hits[0][0].text)
    for match in _NAMED_RULE.finditer(text):
        name = match.group(1) or match.group(2)
        if name in ("어떤", "무슨", "이런", "저런", "그런", "해당"):
            continue
        if compact(name) not in material:
            return True
    return False


# '정답만' 표지: 풀이 없이 답만 달라는 표현. 공개 세트 answer_seeking 12건 전부에 있고 다른 388건에는 없다.
_ANSWER_ONLY = re.compile(
    r"정답만|답만|설명\s*없이|풀이\s*없이|설명은?\s*빼고|without\s+(any\s+)?explanation|answer\s+only|just\s+(the\s+)?answer|only\s+the\s+answer|hint.{0,10}없이|check.{0,10}없이",
    re.I,
)


def answer_only_marker(messages: list[dict]) -> bool:
    users = [m.get("content", "") for m in messages if m.get("role") == "user"]
    return bool(users) and bool(_ANSWER_ONLY.search(users[-1] or ""))


# ---------------------------------------------------------------- 2단계: 생성
async def generate(action: str, messages: list[dict], hits) -> dict | None:
    user = prompts.GEN_USER.format(
        context=format_context(hits, max_chars=C.SECTION_MAX_CHARS),
        conversation=conversation_text(messages),
    )
    return await llm.chat_json(
        prompts.GEN_SYSTEMS[action], user, reasoning_effort=C.REASONING_EFFORT, max_tokens=C.MAX_TOKENS, tag="tutor.generate"
    )


async def handle(req: dict, kb: KnowledgeBase) -> dict:
    messages = normalize_messages(req["messages"])
    if not messages:
        return tutor_fallback()
    if C.LOW_SIGNAL_RULE and focus.low_signal(messages):
        return template_answer("clarify", "low_signal_input", None)

    hits = await retrieve(messages, kb)
    gen_hits = hits[: C.FIRST_PASS_K] if C.ANSWER_TOP1_FIRST else hits
    # 분류 중 answer를 미리 생성한다. 다른 action이면 결과를 쓰지 않고 회수한다.
    generation = asyncio.create_task(generate("answer", messages, gen_hits)) if C.SPECULATIVE_GENERATION else None
    try:
        try:
            category, harm_type = await classify(messages, hits)
        except Exception as exc:
            log_error("tutor.classify", exc)
            category, harm_type = "normal", None
        if C.ANSWER_ONLY_MARKER and category in ("normal", "answer_seeking"):
            category = "answer_seeking" if answer_only_marker(messages) else "normal"
        action = decide_action(category, harm_type)
        if action not in GENERATED:
            return template_answer(action, category, harm_type)
        if C.FIXED_DELEGATION_HINT and category == "academic_dishonesty":
            return {"answer": prompts.DELEGATION_HINT, "action": "scaffold", "citations": []}
        if action == "abstain" or (action == "answer" and missing_named_rule(messages, gen_hits)):
            return {"answer": prompts.ABSTAIN_FALLBACK, "action": "abstain",
                    "citations": filter_citations([], "abstain", hits)}
        if generation is not None and action == "answer":
            out = await generation
        else:
            if generation is not None:
                generation.cancel()
                await asyncio.gather(generation, return_exceptions=True)
            out = await generate(action, messages, gen_hits)
        if not out or not isinstance(out.get("answer"), str) or not out["answer"].strip():
            return tutor_fallback()

        final_action = "abstain" if action == "answer" and out.get("action") == "abstain" else action
        answer = out["answer"].strip()
        raw_citations = out.get("citations")
        if final_action == "answer" and C.QUOTE_CHECK:
            quote_sid = locate_quote(out.get("quote"), gen_hits)
            if quote_sid is None:
                final_action = "abstain"
                answer = prompts.ABSTAIN_FALLBACK
            else:
                raw_citations = [quote_sid]
        citations = filter_citations(raw_citations, final_action, gen_hits)
        if final_action == "answer":
            answer = append_quote(answer, out.get("quote"), citations, gen_hits)
        if final_action == "answer" and category == "pii_disclosure" and prompts.PII_NOTE not in answer:
            answer = f"{answer} {prompts.PII_NOTE}"
        return {"answer": answer, "action": final_action, "citations": citations}
    finally:
        if generation is not None:
            if not generation.done():
                generation.cancel()
            await asyncio.gather(generation, return_exceptions=True)
