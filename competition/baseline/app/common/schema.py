"""요청 정규화 + 응답 규격 강제.

파트 코드가 무엇을 돌려주든, 서버는 여기서 규격에 맞게 고쳐서 내보낸다.
→ 파트 담당자는 형식 오류 걱정 없이 내용만 개선하면 된다.
"""
import re
from typing import Any

TUTOR_ACTIONS = ["answer", "scaffold", "clarify", "abstain", "redirect", "refuse", "escalate"]
GUARD_CATEGORIES = [
    "normal",
    "answer_seeking",
    "academic_dishonesty",
    "off_topic",
    "out_of_material",
    "jailbreak_injection",
    "pii_disclosure",
    "harmful_content",
    "emotional_distress",
    "low_signal_input",
]
ANSWER_MAX = 4000


def normalize_request(body: Any) -> dict:
    if not isinstance(body, dict):
        body = {}
    msgs = body.get("messages")
    if not isinstance(msgs, list):
        msgs = []
    clean = []
    for m in msgs:
        if isinstance(m, dict):
            role = m.get("role") if m.get("role") in ("user", "assistant") else "user"
            content = m.get("content")
            clean.append({"role": role, "content": content if isinstance(content, str) else str(content or "")})
    out = {
        "spec_version": body.get("spec_version", "1.0"),
        "session_id": body.get("session_id", ""),
        "messages": clean,
    }
    if "candidate_answer" in body and body["candidate_answer"] is not None:
        out["candidate_answer"] = str(body["candidate_answer"])
    return out


# ---------------- tutor ----------------
def tutor_fallback() -> dict:
    return {
        "answer": "질문을 조금 더 구체적으로 알려주시겠어요? 어떤 실습이나 예제에 대한 질문인지 알려주시면 자료를 바탕으로 도와드릴게요.",
        "action": "clarify",
        "citations": [],
    }


def enforce_tutor(resp: Any, valid_ids: set[str] | None = None) -> dict:
    if not isinstance(resp, dict):
        return tutor_fallback()
    answer = resp.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        answer = tutor_fallback()["answer"]
    answer = answer[:ANSWER_MAX]
    action = resp.get("action")
    if action not in TUTOR_ACTIONS:
        action = "answer"
    cits = resp.get("citations")
    if not isinstance(cits, list):
        cits = []
    seen, out_cits = set(), []
    for c in cits:
        if isinstance(c, str) and c not in seen and (valid_ids is None or c in valid_ids):
            seen.add(c)
            out_cits.append(c)
    return {"answer": answer, "action": action, "citations": out_cits}


# ---------------- guard ----------------
def guard_fallback(has_candidate: bool) -> dict:
    out: dict = {"input": {"decision": "allow", "category": "normal"}}
    if has_candidate:
        out["output"] = {"grounded": True, "unsupported": [], "safe": True, "category": "normal"}
    return out


_WS = re.compile(r"\s+")


def locate_span(span: str, source: str) -> str | None:
    """LLM 이 준 구간을 후보 답변의 '원문 그대로' 부분 문자열로 되돌린다.
    공백 차이는 허용해서 찾는다."""
    span = span.strip()
    if not span:
        return None
    if span in source:
        return span
    parts = [re.escape(p) for p in _WS.split(span) if p]
    if not parts:
        return None
    m = re.search(r"\s*".join(parts), source)
    return m.group(0) if m else None


def enforce_guard(resp: Any, has_candidate: bool, candidate: str = "") -> dict:
    fb = guard_fallback(has_candidate)
    if not isinstance(resp, dict):
        return fb
    inp = resp.get("input") if isinstance(resp.get("input"), dict) else {}
    decision = inp.get("decision") if inp.get("decision") in ("allow", "block") else "allow"
    category = inp.get("category") if inp.get("category") in GUARD_CATEGORIES else "normal"
    result: dict = {"input": {"decision": decision, "category": category}}
    if has_candidate:
        o = resp.get("output") if isinstance(resp.get("output"), dict) else {}
        grounded = o.get("grounded") if isinstance(o.get("grounded"), bool) else True
        safe = o.get("safe") if isinstance(o.get("safe"), bool) else True
        spans = []
        for s in o.get("unsupported") or []:
            if isinstance(s, str):
                hit = locate_span(s, candidate)
                if hit and hit not in spans:
                    spans.append(hit)
        ocat = o.get("category") if o.get("category") in GUARD_CATEGORIES else "normal"
        result["output"] = {"grounded": grounded, "unsupported": spans, "safe": safe, "category": ocat}
    return result
