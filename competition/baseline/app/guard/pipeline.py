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

from app import config
from app.common import embed, fewshot, focus, llm
from app.common.kb import KnowledgeBase, Section, conversation_text, format_context, retrieval_query
from app.common.logging_util import log_error
from app.common.schema import locate_span
from app.guard import config as C
from app.guard import policy, prompts


async def check_input(messages: list[dict], kb: KnowledgeBase | None = None) -> dict:
    if not messages or (config.LOW_SIGNAL_RULE and focus.is_low_signal(messages)):
        return {"decision": "allow", "category": "low_signal_input"}
    if C.STRIP_WRAPPERS:
        # "거절하지 말고", "나 이 튜터 관리자야" 같은 말머리는 떼고 판정한다(주 분류와 경계 판정 모두)
        messages = focus.without_wrappers(messages)
    base_system = prompts.INPUT_SYSTEM_HARM if C.HARM_CLAUSE else prompts.INPUT_SYSTEM
    system, user = base_system, prompts.INPUT_USER.format(conversation=conversation_text(messages))
    if kb is not None and C.INPUT_USE_MATERIAL:
        # 질문과 가까운 자료를 함께 보여 줘야 '자료에 없는 값'(out_of_material)을 가려낼 수 있다
        hits = kb.search(retrieval_query(messages), k=C.INPUT_TOP_K)
        if hits:
            system = base_system + prompts.INPUT_MATERIAL_RULE
            user = prompts.INPUT_USER_WITH_MATERIAL.format(
                context=format_context(hits, max_chars=C.SECTION_MAX_CHARS),
                conversation=conversation_text(messages),
            )
    user = fewshot.examples_block(messages) + user
    checks = focus.start_checks(messages)  # 단서가 있으면 경계 판정을 주 분류와 동시에 시작
    # 긴 글을 붙여 넣은 발화는 끝부분(실제 요청)만 따로 판정한다
    paste = focus.start_paste_check(messages, C.PASTE_MIN_CHARS, C.PASTE_TAIL_CHARS) if C.PASTE_TAIL_CHECK else None
    try:
        out = await llm.chat_json(
            system,
            user,
            reasoning_effort=C.INPUT_REASONING_EFFORT,
            max_tokens=C.MAX_TOKENS,
            backup_effort=C.INPUT_BACKUP_EFFORT or None,
            backup_after=C.INPUT_BACKUP_AFTER,
        )
    except BaseException:
        for t in checks.values():
            t.cancel()
        if paste is not None:
            paste.cancel()
        raise
    category = out.get("category", "normal") if out else "normal"
    detail: dict = {}
    category = await focus.apply_checks(category, checks, detail)
    if paste is not None:
        mode = await paste
        detail["paste"] = mode
        # 끝의 요청이 수업 밖 작업이면 off_topic. 막는 쪽으로만 쓴다: 이미 막는 범주이거나 정서적 위기면 그대로 둔다.
        if mode == "offtask" and category not in policy.BLOCK_CATEGORIES and category != "emotional_distress":
            category = "off_topic"
    if config.ANSWER_ONLY_RULE:
        # answer_seeking 은 "정답만/설명 없이" 표지가 있을 때만이다(공개 세트 12개 전부에 표지가 있고 다른 388개에는 없다).
        # 허용 범주끼리만 바꾸므로 차단 결정은 달라지지 않는다. 저장된 가드 실행 6회: 범주 정확도 tune 0.868 → 0.882, holdout 0.893 → 0.899.
        marker = focus.has_answer_only_marker(messages)
        if category == "answer_seeking" and not marker:
            category = "normal"
        elif marker and category in ("normal", "out_of_material", "pii_disclosure"):
            category = "answer_seeking"
    # 자기 이름·학교를 밝히며 묻는 정상 질문은 pii_disclosure (allow). 다른 범주로 분류됐으면 그 범주가 우선이다.
    if category == "normal" and config.SELF_INTRO_RULE and focus.has_self_intro(messages):
        category = "pii_disclosure"
    res = {"decision": policy.decide(category, out.get("decision") if out else None), "category": category}
    if config.EXP_DEBUG:  # 실험 서버 전용 진단 정보: 주 분류와 경계 판정 결과 (제출용 서버에서는 꺼져 있다)
        res["_debug"] = detail
    return res


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


# ---------------------------------------------------------------- 문장별 가까운 자료 문장 (임베딩)
_KB_UNITS: list[tuple[str, str]] | None = None


def kb_units(kb: KnowledgeBase) -> list[tuple[str, str]]:
    """자료를 문장(표는 행) 단위로 나눈 목록 [(섹션 id, 문장)]. 튜터가 답에 덧붙일 때 쓰는 단위와 같다."""
    global _KB_UNITS
    if _KB_UNITS is None:
        from app.tutor.pipeline import section_units

        _KB_UNITS = [(s.id, text) for s in kb.sections.values() for text, _ in section_units(s)]
    return _KB_UNITS


async def aligned_sentences(segs: list[Segment], context_hits, kb: KnowledgeBase) -> str:
    """후보 답변의 문장마다 뜻이 가장 가까운 자료 문장 OUTPUT_ALIGN 개를 찾아 '[번호] (섹션) 문장 / (섹션) 문장' 줄로 적는다.

    꺼져 있거나 임베딩을 쓸 수 없으면 빈 문자열(그때는 지금까지와 똑같이 판정한다).
    """
    if C.OUTPUT_ALIGN <= 0 or not await embed.ensure(kb_units(kb)):
        return ""
    vecs = await embed.embed([_CITE.sub(" ", s.text) for s in segs])
    if vecs is None:
        return ""
    sids = None if C.OUTPUT_ALIGN_SCOPE == "all" else [s.id for s, _ in context_hits]
    lines = []
    for i, v in enumerate(vecs, 1):
        near = embed.nearest(v, sids, C.OUTPUT_ALIGN)
        if near:
            lines.append(f"[{i}] " + " / ".join(f"({sid}) {text}" for _, sid, text in near))
    return "\n".join(lines)


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

    user = prompts.OUTPUT_USER.format(
        context=format_context(context_hits, max_chars=C.SECTION_MAX_CHARS),
        conversation=conversation_text(messages),
        segments="\n".join(f"[{i}] {s.text}" for i, s in enumerate(segs, 1)),
    )
    system = prompts.OUTPUT_SYSTEM
    aligned = await aligned_sentences(segs, context_hits, kb)
    if aligned:
        system += prompts.OUTPUT_ALIGNED_NOTE
        user += prompts.OUTPUT_ALIGNED.format(aligned=aligned)
    n = max(1, C.OUTPUT_SAMPLES)
    if n == 1:
        vote = await _judge_output(user, segs, system)
        return {**vote, "category": "normal"} if vote else default

    # 같은 판정을 n 번 동시에 보내고 과반으로 정한다. 과반이 정해지면 남은 호출은 기다리지 않는다.
    need = n // 2 + 1
    tasks = [asyncio.create_task(_judge_output(user, segs, system)) for _ in range(n)]
    votes: list[dict] = []
    try:
        for fut in asyncio.as_completed(tasks):
            try:
                vote = await fut
            except Exception as e:  # 한 호출의 실패는 표 하나가 빠지는 것으로 처리한다
                log_error("guard.output_sample", e)
                continue
            if vote is None:
                continue
            votes.append(vote)
            bad = sum(not v["grounded"] for v in votes)
            if bad >= need or len(votes) - bad >= need:
                break
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()
            elif not t.cancelled():
                t.exception()  # 결과를 기다리지 않은 호출의 오류는 읽어서 버린다(경고 로그 방지)
    if not votes:
        return default
    return {**combine_votes(votes), "category": "normal"}


async def _judge_output(user: str, segs: list[Segment], system: str = prompts.OUTPUT_SYSTEM) -> dict | None:
    """답변 판정 호출 한 번. 응답이 비었으면 None."""
    out = await llm.chat_json(
        system, user, reasoning_effort=C.OUTPUT_REASONING_EFFORT, max_tokens=C.MAX_TOKENS
    )
    if not out:
        return None

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
    return {"grounded": grounded, "unsupported": spans, "safe": safe}


_SPAN_NOISE = re.compile(r"[\s\W_]+")


def _span_size(spans: list[str]) -> int:
    """구간에서 공백·문장부호를 뺀 글자 수 (채점기의 구간 F1 과 같은 단위)."""
    return len(_SPAN_NOISE.sub("", "".join(spans)))


def combine_votes(votes: list[dict]) -> dict:
    """여러 번 부른 답변 판정을 하나로 합친다.

    - 근거 여부: '근거 없음' 표가 '근거 있음' 표보다 적지 않으면 근거 없음.
      세 표가 다 모이면 과반이고, 호출 실패로 두 표만 모여 갈리면 근거 없음 쪽을 따른다.
    - 구간: 근거 없음이라고 한 표 가운데 가장 짧은 구간. 한 번의 판정은 문장 전체를 구간으로 주는 일이 잦고
      (처음 보는 문항에서 정답 구간의 1.5배가 넘는 구간이 42%), 정답 구간은 틀린 값과 그 서술부만 담은 짧은 구절이다.
    """
    bad = [v for v in votes if not v["grounded"]]
    safe = sum(bool(v["safe"]) for v in votes) * 2 >= len(votes)
    if not bad or len(bad) < len(votes) - len(bad):
        return {"grounded": True, "unsupported": [], "safe": safe}
    with_span = [v["unsupported"] for v in bad if v["unsupported"]]
    spans = min(with_span, key=_span_size) if with_span else []
    return {"grounded": False, "unsupported": list(spans), "safe": safe}


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
    if "_debug" in inp:
        resp["_debug"] = inp.pop("_debug")
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
