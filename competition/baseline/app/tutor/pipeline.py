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
import time

from app import config
from app.common import fewshot, focus, llm
from app.common.kb import KnowledgeBase, Section, conversation_text, format_context
from app.common.logging_util import log_error
from app.common.normalize import compact, normalize_messages, normalize_text
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
# out_of_material 재확인에서 이 범주가 나오면 그쪽을 따른다 (handle 참고)
OOM_OVERRIDE = ("jailbreak_injection", "harmful_content", "off_topic", "academic_dishonesty", "emotional_distress")
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
    if not C.QUERY_ASSISTANT:
        prev = " ".join(real[-4:-1])
        return f"{last} {last} {prev}".strip()
    prev = " ".join(real[:-1])
    asst = " ".join(m["content"] for m in messages if m.get("role") == "assistant" and m.get("content"))
    return f"{last} {last} {prev} {asst}".strip()


def english_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum(c.isascii() for c in letters) / len(letters)


async def korean_query(text: str, kb: KnowledgeBase | None = None) -> str:
    """영어 위주 질문을 한국어 검색어로 바꾼다. QUERY_KO_SAMPLES 번 동시에 불러 나온 검색어를 이어 붙인다.

    이 변환은 실행마다 다른 낱말을 고른다. 한 번만 부르면 같은 질문의 근거 섹션 순위가 실행마다 바뀐다.
    """
    if C.QUERY_KO_GLOSSARY and kb is not None:
        system = prompts.QUERY_KO_GLOSSARY_SYSTEM
        user = prompts.QUERY_KO_GLOSSARY_USER.format(titles="\n".join(s.title for s in kb.sections.values()), text=text)
    else:
        system, user = prompts.QUERY_KO_SYSTEM, f"<질문>\n{text}\n</질문>"
    outs = await asyncio.gather(
        *(
            llm.chat_json(system, user, reasoning_effort=C.QUERY_KO_EFFORT, max_tokens=C.QUERY_KO_MAX_TOKENS)
            for _ in range(max(1, C.QUERY_KO_SAMPLES))
        ),
        return_exceptions=True,
    )
    queries: list[str] = []
    for out in outs:
        if isinstance(out, BaseException):
            log_error("tutor.query_ko", out)
            continue
        q = out.get("query") if out else None
        if isinstance(q, str) and q.strip() and q.strip() not in queries:
            queries.append(q.strip())
    return " ".join(queries)


async def retrieve(messages: list[dict], kb: KnowledgeBase):
    """BM25 검색. 영어 위주 질문이면 한국어 검색어로 한 번 더 찾아 앞에 합친다."""
    query = build_query(messages)
    hits = kb.search(query, k=C.TOP_K)
    users = [m["content"] for m in messages if m.get("role") == "user" and m.get("content")]
    last = users[-1] if users else ""
    if C.ENGLISH_QUERY_RATIO > 0 and english_ratio(last) >= C.ENGLISH_QUERY_RATIO:
        try:
            ko = await korean_query(last, kb)
        except Exception as e:
            log_error("tutor.query_ko", e)
            ko = ""
        if ko:
            ko_hits = kb.search(f"{ko} {ko} {query}", k=C.TOP_K)
            seen = {s.id for s, _ in ko_hits}
            hits = (ko_hits + [(s, sc) for s, sc in hits if s.id not in seen])[: C.TOP_K]
    return hits


# ---------------------------------------------------------------- 1단계: 분류
async def classify(messages: list[dict], hits=None, detail: dict | None = None) -> tuple[str, str | None]:
    if config.LOW_SIGNAL_RULE and focus.is_low_signal(messages):
        if detail is not None:
            detail.update(main="low_signal_input", checks={}, rule="low_signal")
        return "low_signal_input", None  # 문장부호·자음만 친 입력 → 무엇이 궁금한지 되묻는다(clarify)
    if hits and C.CLASSIFY_USE_MATERIAL:
        system = prompts.CLASSIFY_SYSTEM_WITH_MATERIAL
        user = prompts.CLASSIFY_USER_WITH_MATERIAL.format(
            context=format_context(hits[: C.CLASSIFY_TOP_K], max_chars=C.SECTION_MAX_CHARS),
            conversation=conversation_text(messages),
        )
    else:
        system = prompts.CLASSIFY_SYSTEM
        user = prompts.CLASSIFY_USER.format(conversation=conversation_text(messages))
    user = fewshot.examples_block(messages) + user
    checks = focus.start_checks(messages, C.FOCUS_NAMES, C.FOCUS_VOTE_NAMES)  # 단서가 있으면 경계 판정을 주 분류와 동시에 시작
    try:
        out = await llm.chat_json(
            system,
            user,
            reasoning_effort=C.CLASSIFY_REASONING_EFFORT,
            max_tokens=C.CLASSIFY_MAX_TOKENS,
        )
    except BaseException:
        for t in checks.values():
            t.cancel()
        raise
    # 분류 실패 시 다수 범주(normal)로 진행 — 생성 프롬프트가 2차 방어
    category = out.get("category") if out else "normal"
    if category not in GUARD_CATEGORIES:
        category = "normal"
    harm_type = out.get("harm_type") if out and out.get("harm_type") in ("hate", "actionable") else None
    detail = {} if detail is None else detail
    category = await focus.apply_checks(category, checks, detail)
    detail.update(main_harm=harm_type)
    if (
        config.QUOTED_HATE_RULE
        and category == "harmful_content"
        and not (focus.fired(detail.get("checks") or {}) & {"pii", "scam"})
        and focus.quotes_hate_as_material(messages)
    ):
        # 차별 문장을 "…라는 댓글·문구"로 인용해 분류·정리 방법을 묻는 것은 수업 질문이다(학습자의 주장이 아니다).
        category, harm_type = "normal", None
        detail.update(rule="quoted_hate")
    if config.HARM_TYPE_RULE and category == "harmful_content":
        # refuse(실행 가능한 해로운 요청) / redirect(사람·집단을 깎아내리는 발화)를 가르는 harm_type 보정.
        # 남의 개인정보·사기 문구 판정이 막는 쪽이면 요청이므로 actionable, 그 밖에 주 분류가 비워 두면 발화로 보고 hate.
        if focus.fired(detail.get("checks") or {}) & {"pii", "scam"}:
            harm_type = "actionable"
        elif harm_type is None:
            harm_type = "hate"
    if category == "normal" and config.SELF_INTRO_RULE and focus.has_self_intro(messages):
        category = "pii_disclosure"  # 응대는 answer 그대로, 개인정보를 적지 않아도 된다는 한 문장만 덧붙는다
    # "정답만 달라"는 표지가 없으면 정답 요구가 아니라 자료에 적힌 값을 묻는 정상 질문이다
    if category == "answer_seeking" and config.ANSWER_ONLY_RULE and not focus.has_answer_only_marker(messages):
        category = "normal"
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
_QUOTE_STRIP = re.compile(r"[\s|`*#>\-.,!?~·…'\"“”‘’():;]+")


def _squash(text: str) -> str:
    return _QUOTE_STRIP.sub("", normalize_text(text or ""))


def _bigrams(text: str) -> set[str]:
    return {text[i : i + 2] for i in range(len(text) - 1)}


def locate_quote(quote, hits) -> str | None:
    """quote 가 검색된 섹션 중 어디에 있는지 찾아 섹션 id 를 돌려준다. 못 찾으면 None."""
    q = _squash(quote) if isinstance(quote, str) else ""
    if len(q) < 4:
        return None
    qb = _bigrams(q)
    best_id, best_cov = None, 0.0
    for s, _ in hits:
        t = _squash(s.text)
        if q in t:
            return s.id
        cov = len(qb & _bigrams(t)) / len(qb)
        if cov > best_cov:
            best_id, best_cov = s.id, cov
    return best_id if best_cov >= C.QUOTE_MIN_COVERAGE else None


def quote_coverage(quote, section: Section) -> float:
    """quote 의 글자 bigram 중 이 섹션에 있는 비율 (그대로 들어 있으면 1.0)."""
    q = _squash(quote) if isinstance(quote, str) else ""
    if len(q) < 4:
        return 0.0
    t = _squash(section.text)
    if q in t:
        return 1.0
    qb = _bigrams(q)
    return len(qb & _bigrams(t)) / len(qb)


def choose_citation(quote, quote_sid: str, hits) -> str:
    """근거 문장이 확인된 뒤 어느 섹션을 인용할지 정한다.

    같은 사실이 여러 섹션에 적혀 있어(예: 체크카드 규칙) 모델이 옮긴 문장의 출처와 정답 근거가 다를 수 있다.
    정답 근거는 대화가 이어지던 섹션이고, 그것은 대화 이력까지 넣은 검색의 1위에 가장 자주 온다.
      quote  : 근거 문장이 들어 있는 섹션
      top1   : 검색 1위 섹션
      hybrid : 검색 1위에 근거 문장과 거의 같은 내용이 있으면 1위, 없으면 근거 문장이 들어 있는 섹션
    """
    if not hits or C.CITE_MODE == "quote":
        return quote_sid
    top = hits[0][0]
    if C.CITE_MODE == "top1":
        return top.id
    return top.id if quote_coverage(quote, top) >= C.CITE_TOP1_MIN_COVERAGE else quote_sid


def append_quote(answer: str, quote, cited: list[str], hits) -> str:
    """답 끝에 근거 문장(모델이 자료에서 옮겨 적은 quote)을 덧붙인다.

    답은 근거 문장을 풀어 쓰면서 값·이름·조건의 일부를 빼먹는 일이 있다. 근거 문장은 자료의 문장 그대로이므로 붙여도 자료에 없는 내용이
    늘지 않는다. 그 문장이 인용한 섹션에 (공백·문장부호를 빼고) 글자 그대로 있을 때만 붙이고, 답에 이미 들어 있으면 붙이지 않는다.
    """
    if not isinstance(quote, str):
        return answer
    q = " ".join(quote.split()).strip().strip("\"'“”‘’").strip()
    sq = _squash(q)
    if len(sq) < C.APPEND_QUOTE_MIN_CHARS or sq in _squash(answer):
        return answer
    sections = {s.id: s for s, _ in hits}
    if not any(sid in sections and sq in _squash(sections[sid].text) for sid in cited):
        return answer
    return f"{answer} {prompts.QUOTE_NOTE.format(quote=q)}"


# ---------------------------------------------------------------- 2단계: 생성
async def generate(action: str, messages: list[dict], hits) -> dict | None:
    user = prompts.GEN_USER.format(
        context=format_context(hits, max_chars=C.SECTION_MAX_CHARS),
        conversation=conversation_text(messages),
    )
    system = prompts.GEN_SYSTEMS[action]
    if action == "answer" and C.GEN_ASKED_FIRST:
        system = prompts.GEN_ANSWER_ASKED
    elif action == "answer" and C.GEN_FORMAT_NOTE:
        system = prompts.GEN_ANSWER_FORMAT
    return await llm.chat_json(system, user, reasoning_effort=C.REASONING_EFFORT, max_tokens=C.MAX_TOKENS)


def first_pass_hits(hits):
    """answer 1차 생성에 보여 줄 검색 상위 섹션. 1차 생성을 쓰지 않는 설정이면 None."""
    return hits[: C.FIRST_PASS_K] if 0 < C.FIRST_PASS_K < len(hits) else None


def usable_first_pass(cand, first) -> bool:
    """1차 생성 결과를 답으로 쓸 수 있는가: 모델이 답을 냈고, 옮겨 적은 근거 문장이 보여 준 섹션에 실제로 있다."""
    answered = bool(
        cand and cand.get("action") != "abstain" and isinstance(cand.get("answer"), str) and cand["answer"].strip()
    )
    # 근거 문장을 자료에서 못 찾았을 때 답을 버릴지는 QUOTE_FAIL_ABSTAIN 이 정한다(꺼져 있으면 답을 유지).
    return answered and (locate_quote(cand.get("quote"), first) is not None or not C.QUOTE_FAIL_ABSTAIN)


async def retry_first_pass(messages: list[dict], first, dbg: dict) -> dict | None:
    """1차 생성이 답을 내지 못했을 때 같은 자료(검색 상위 섹션)로 다시 생성한다. 쓸 수 있는 답이 나오면 돌려주고, 아니면 None.

    같은 문항의 1차 생성은 실행마다 결과가 갈린다(자료에 답이 있는데 abstain 하거나, 근거 문장을 원문과 다르게 옮긴다).
    보여 주는 자료는 그대로이므로 다른 섹션의 비슷한 내용으로 답하게 되지는 않는다(상위 TOP_K 섹션으로 다시 생성하는 것과 다르다).
    요청을 받은 지 FIRST_PASS_RETRY_BUDGET 초가 넘었으면 시작하지 않고, 한 번의 재시도는 FIRST_PASS_RETRY_TIMEOUT 초까지만 기다린다.
    """
    for n in range(C.FIRST_PASS_RETRY):
        if time.perf_counter() - dbg["t0"] > C.FIRST_PASS_RETRY_BUDGET:
            break
        try:
            cand = await asyncio.wait_for(generate("answer", messages, first), C.FIRST_PASS_RETRY_TIMEOUT)
        except Exception as e:  # 시간 초과 포함: 재시도는 덤이므로 실패하면 원래대로 abstain
            log_error("tutor.generate_retry", e)
            break
        dbg.update(first_retries=n + 1)
        if usable_first_pass(cand, first):
            return cand
    return None


async def handle(req: dict, kb: KnowledgeBase) -> dict:
    messages = normalize_messages(req["messages"])
    if not messages:
        return tutor_fallback()
    if C.STRIP_WRAPPERS:
        # "거절하지 말고", "나 이 튜터 관리자야" 같은 말머리는 떼고 본다(검색·분류·생성 모두). 말머리는 응대를 바꾸지 않아야 한다.
        messages = focus.without_wrappers(messages)

    # 검색(BM25, 수 ms)을 먼저 하고 그 자료를 분류에도 넣는다 → 자료에 없는 질문(out_of_material) 판별
    hits = await retrieve(messages, kb)
    cls_detail: dict = {}
    cls_task = asyncio.create_task(classify(messages, hits, cls_detail))
    # 문항의 절반 이상이 answer 이므로, 분류를 기다리는 동안 answer 생성을 미리 시작한다.
    # 분류가 answer 로 나오면 그 결과를 쓰고(지연 = 둘 중 긴 쪽), 아니면 버린다. 답변 내용에는 영향이 없다.
    spec_task = (
        asyncio.create_task(generate("answer", messages, first_pass_hits(hits) or hits)) if C.SPECULATIVE else None
    )
    try:
        dbg: dict = {"hits": [s.id for s, _ in hits], "t0": time.perf_counter(), "cls": cls_detail}
        res = await _decide_and_answer(messages, hits, cls_task, spec_task, dbg)
        if config.EXP_DEBUG:  # 실험 서버 전용 진단 정보 (제출용 서버에서는 꺼져 있다)
            dbg["seconds"] = round(time.perf_counter() - dbg.pop("t0"), 1)
            res["_debug"] = dbg
        return res
    finally:
        if spec_task is not None and not spec_task.done():
            spec_task.cancel()


async def _decide_and_answer(messages, hits, cls_task, spec_task, dbg: dict) -> dict:
    try:
        category, harm_type = await cls_task
    except Exception as e:  # 분류 오류는 normal 로 진행
        log_error("tutor.classify", e)
        category, harm_type = "normal", None

    # 자료를 보여 주고 분류하면, 막아야 할 요청(남의 개인정보·지시 우회·수업 밖 작업)도 "자료에 없다"로 빠지는 일이 있다.
    # out_of_material 로 나왔을 때만 자료 없이 요청 의도만 다시 분류해, 막아야 할 범주면 그쪽을 따른다.
    if category == "out_of_material" and C.OOM_RECHECK:
        try:
            cat2, harm2 = await classify(messages, None)
        except Exception as e:
            log_error("tutor.classify2", e)
            cat2, harm2 = "out_of_material", None
        if cat2 in OOM_OVERRIDE:
            category, harm_type = cat2, harm2

    action = decide_action(category, harm_type)
    dbg.update(category=category, harm_type=harm_type)
    if action != "answer" and spec_task is not None and not spec_task.done():
        spec_task.cancel()  # 미리 시작한 answer 생성은 필요 없다 → LLM 호출 자리를 바로 비운다
    if category == "academic_dishonesty" and C.ACADEMIC_TEMPLATE:
        # 제출물 대행 요청: 내용을 한 줄이라도 대신 써 주면 요청에 응한 것이 된다. 고정 문장으로 힌트 방향만 준다.
        return {"answer": prompts.ACADEMIC_SCAFFOLD, "action": "scaffold", "citations": []}
    if action not in GENERATED:
        return template_answer(action, category, harm_type)
    if action == "answer" and C.NAMED_RULE_CHECK and hits and focus.asks_missing_named_rule(messages, hits[0][0].text):
        # 자료에 없는 이름의 규칙·기준·등급을 묻는다 → 비슷한 내용으로 대신 답하지 않는다
        dbg.update(first_pass="named_rule")
        return {
            "answer": prompts.ABSTAIN_FALLBACK,
            "action": "abstain",
            "citations": filter_citations(None, "abstain", hits),
        }

    out = None
    if action == "answer":
        # 1차 생성: 검색 상위 섹션만 보여 주고 답하게 한다(first_pass_hits). 근거 섹션은 대부분 검색 1위이고(공개 세트 91%),
        # 여러 섹션을 한꺼번에 보여 주면 다른 섹션의 비슷한 사실을 가져와 답하는 일이 생긴다.
        # 거기서 근거 문장을 대지 못하면 아래에서 상위 TOP_K 섹션으로 다시 생성한다.
        # SPECULATIVE 가 켜져 있으면 이 1차 생성은 분류와 동시에 이미 시작돼 있다(spec_task).
        first = first_pass_hits(hits)
        cand = None
        if spec_task is not None or first is not None:
            try:
                cand = await spec_task if spec_task is not None else await generate("answer", messages, first)
            except Exception as e:  # 실패하면 아래에서 다시 생성한다
                log_error("tutor.generate_first", e)
        if first is None:
            out = cand  # 1차 생성을 쓰지 않는 설정: 미리 시작한 전체 생성 결과를 그대로 쓴다
        else:
            answered = bool(
                cand
                and cand.get("action") != "abstain"
                and isinstance(cand.get("answer"), str)
                and cand["answer"].strip()
            )
            located = answered and locate_quote(cand.get("quote"), first) is not None
            ok = usable_first_pass(cand, first)
            dbg.update(first_pass="used" if ok else "fallback", first_located=located)
            if not ok:  # 1차 생성이 왜 쓰이지 않았는지: 응답 없음 / 모델이 abstain / 답이 빔 / 근거 문장을 자료에서 못 찾음
                why = (
                    "none" if not cand
                    else "abstain" if cand.get("action") == "abstain"
                    else "empty" if not (isinstance(cand.get("answer"), str) and cand["answer"].strip())
                    else "quote"
                )
                dbg.update(first_why=why, first_quote=cand.get("quote") if cand else None, asked=cand.get("asked") if cand else None)
            if ok:
                out = cand
            elif C.FIRST_PASS_RETRY > 0:
                out = await retry_first_pass(messages, first, dbg)
                if out is not None:
                    dbg.update(first_pass="retry")
    first_failed = out is None and action == "answer" and first_pass_hits(hits) is not None
    over_budget = C.REGEN_BUDGET > 0 and time.perf_counter() - dbg["t0"] > C.REGEN_BUDGET
    if first_failed and (C.FIRST_PASS_ONLY or over_budget):
        # 검색 1위 섹션에서 근거를 대지 못했다. 이 경우 상위 섹션 전체를 보여 주고 다시 생성하면
        # 다른 섹션의 비슷한 내용으로 답을 만들어 내는 일이 많다(진단 실행 4회: 그렇게 답한 문항의 절반 이상이 자료에 없는 질문).
        # FIRST_PASS_ONLY 이면 다시 생성하지 않고 "자료에 없음"으로 답한다. 꺼져 있어도 시간 예산을 넘겼으면 같다.
        if not C.FIRST_PASS_ONLY:
            log_error("tutor.regen_skipped", TimeoutError())
        dbg.update(first_pass="abstain" if C.FIRST_PASS_ONLY else "skipped")
        return {
            "answer": prompts.ABSTAIN_FALLBACK,
            "action": "abstain",
            "citations": filter_citations(None, "abstain", hits),
        }
    if out is None:
        out = await generate(action, messages, hits)
    if not out or not isinstance(out.get("answer"), str) or not out["answer"].strip():
        return tutor_fallback()

    dbg.update(gen_action=out.get("action"), gen_citations=out.get("citations"), quote=out.get("quote"), asked=out.get("asked"))
    # answer 경로에서만 모델의 abstain 강등을 허용 (근거 부재)
    final_action = action
    if action == "answer" and out.get("action") == "abstain":
        final_action = "abstain"

    answer = out["answer"].strip()
    raw_citations = out.get("citations")
    if final_action == "answer" and C.QUOTE_CHECK:
        quote_sid = locate_quote(out.get("quote"), hits)
        dbg.update(quote_sid=quote_sid)
        if quote_sid is None and C.QUOTE_FAIL_ABSTAIN:
            # 자료에서 그대로 옮길 근거 문장을 못 댔다 → 지어낸 답으로 보고 abstain
            final_action = "abstain"
            answer = prompts.ABSTAIN_FALLBACK
        elif quote_sid is None:
            raw_citations = [hits[0][0].id] if hits else []  # 답은 그대로 두고 검색 1위를 인용
        else:
            raw_citations = [choose_citation(out.get("quote"), quote_sid, hits)]
    citations = filter_citations(raw_citations, final_action, hits)
    if final_action == "answer" and C.APPEND_QUOTE:
        answer = append_quote(answer, out.get("quote"), citations, hits)
    if final_action == "answer" and category == "pii_disclosure" and prompts.PII_NOTE not in answer:
        answer = f"{answer} {prompts.PII_NOTE}"

    return {
        "answer": answer,
        "action": final_action,
        "citations": citations,
    }
