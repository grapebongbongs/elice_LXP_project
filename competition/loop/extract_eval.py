#!/usr/bin/env python3
"""저장된 공개 세트 실행으로, LLM 없이 두 가지를 센다. 런박스에서 실행한다.

    python3 loop/extract_eval.py

1. 1차 생성의 근거 문장 확인이 실패한 경우(_debug.first_why == "quote")에 자료 문장으로 답했다면(TUTOR_QUOTE_FAIL_EXTRACT):
   그런 경우의 수와 정답 응대, 검색 1위가 정답 근거인지, 고른 문장이 정답 사실을 담는지(글자 bigram 0.6 이상),
   그리고 채점식의 규칙 부분(action·인용)이 얼마나 달라지는지. 비교용으로 모델이 스스로 abstain 한 경우(first_why == "abstain")도 센다.
   참고로 정답 answer 문항 전체에서, 검색 1위 섹션에서 고른 문장 n 개가 정답 사실을 담는 비율도 낸다(이 답의 일반적인 품질).
2. answer 로 나간 답의 글이 스스로 "자료에 나와 있지 않다"고 말하는 경우(TUTOR_ADMIT_ABSTAIN 후보)의 수와 정답 응대.

출력은 개수와 비율뿐이다. tune 과 holdout 을 따로 보고하고 holdout 문항의 내용은 찍지 않는다.
"""
import glob
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "loop"))
from exp_run import is_holdout, load_env_files, pick  # noqa: E402

os.environ.update({k: v for k, v in load_env_files().items() if k not in os.environ})
os.environ["TUTOR_QUOTE_FAIL_EXTRACT"] = os.environ.get("TUTOR_QUOTE_FAIL_EXTRACT", "2")
sys.path.insert(0, str(ROOT / "baseline"))
from app import config  # noqa: E402
from app.common.kb import KnowledgeBase  # noqa: E402
from app.common.normalize import normalize_messages  # noqa: E402
from app.tutor import config as C  # noqa: E402
from app.tutor import prompts  # noqa: E402
from app.tutor.pipeline import build_query, extractive_answer, pick_units  # noqa: E402

ADMITS = re.compile(r"자료[^.!?\n]{0,25}(나와 있지 않|나오지 않|명시되어 있지 않|명시돼 있지 않|명시되지 않|적혀 있지 않|설명되어 있지 않|없어요|없습니다)")
QUOTE_LEAD = prompts.QUOTE_NOTE.split("{")[0].strip()


def bigrams(text: str) -> set:
    s = "".join(ch for ch in text.lower() if ch.isalnum())
    return {s[i : i + 2] for i in range(len(s) - 1)}


def cover(gold_facts, text) -> float:
    g = bigrams(" ".join(gold_facts or []))
    return len(g & bigrams(text)) / len(g) if g else 0.0


def main():
    kb = KnowledgeBase(config.KB_DIR)
    public = {}
    for line in (pick("public") / "tutor.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            d = json.loads(line)
            public[d["id"]] = d

    def hits_for(d):
        ms = normalize_messages(d["messages"])
        return ms, kb.search(build_query(ms), k=5)

    # ---- 참고: 정답 answer 문항 전체에서 자료 문장 답의 품질 (LLM 없음)
    print(f"== 참고: 정답 answer 문항 전체에서, 검색 1위 섹션의 문장 n 개로 답했을 때 (n = {C.QUOTE_FAIL_EXTRACT})")
    q = Counter()
    for d in public.values():
        L = d["labels"]
        if L["action"] != "answer":
            continue
        sp = "holdout" if is_holdout(d["id"]) else "tune"
        ms, hits = hits_for(d)
        ext = extractive_answer(hits, ms, None)
        q[(sp, "n")] += 1
        q[(sp, "답을 만들 수 있음")] += ext is not None
        top1 = bool(hits) and hits[0][0].id in (L.get("evidence") or [])
        q[(sp, "검색 1위가 정답 근거")] += top1
        if ext is not None:
            c = cover(L.get("gold_facts"), ext["answer"])
            q[(sp, "정답 사실 담김(0.6 이상)")] += c >= 0.6
            q[(sp, "검색 1위가 정답 근거이고 정답 사실 담김")] += top1 and c >= 0.6
            q[(sp, "글자 수 합")] += len(ext["answer"])
    for sp in ("tune", "holdout"):
        n = q[(sp, "n")]
        print(f"  {sp}: 문항 {n}, " + ", ".join(f"{k} {q[(sp, k)]}" for k in ("답을 만들 수 있음", "검색 1위가 정답 근거", "정답 사실 담김(0.6 이상)", "검색 1위가 정답 근거이고 정답 사실 담김"))
              + f", 답 길이 평균 {q[(sp, '글자 수 합')] / max(1, q[(sp, '답을 만들 수 있음')]):.0f}자")

    # ---- 저장된 실행
    ev = Counter()
    rule = Counter()
    adm = Counter()
    runs = 0
    files = sorted(glob.glob(str(ROOT / "baseline" / "results" / "*.jsonl")), key=os.path.getmtime)
    for f in files:
        rows = [json.loads(l) for l in open(f, encoding="utf-8") if l.strip()]
        rows = [x for x in rows if x.get("endpoint") == "tutor" and isinstance(x.get("resp"), dict) and x["id"] in public]
        if not rows or not any("first_why" in (x["resp"].get("_debug") or {}) for x in rows):
            continue  # 진단 정보가 없는 예전 실행
        runs += 1
        for x in rows:
            d = public[x["id"]]
            L = d["labels"]
            sp = "holdout" if x.get("holdout") else "tune"
            dbg = x["resp"].get("_debug") or {}
            why = dbg.get("first_why")
            ev[(sp, "tutor rows")] += 1
            if why in ("quote", "abstain") and x["resp"].get("action") == "abstain":
                ev[(sp, why, "건수")] += 1
                ev[(sp, why, "정답 " + L["action"])] += 1
            if why == "quote" and x["resp"].get("action") == "abstain":
                ms, hits = hits_for(d)
                ext = extractive_answer(hits, ms, dbg.get("category"))
                top1 = bool(hits) and hits[0][0].id in (L.get("evidence") or [])
                ev[(sp, "quote", "검색 1위가 정답 근거")] += top1
                ev[(sp, "quote", "답을 만들 수 있음")] += ext is not None
                if ext is not None and L.get("gold_facts"):
                    ev[(sp, "quote", "정답 사실 담김(0.6 이상)")] += cover(L["gold_facts"], ext["answer"]) >= 0.6
                # 채점식의 규칙 부분: action 일치 + 인용 F1 (정답 근거가 있을 때)
                gold_ev = set(L.get("evidence") or [])
                before = [float("abstain" == L["action"])] + ([0.0] if gold_ev else [])
                if ext is not None:
                    after = [float("answer" == L["action"])] + ([float(ext["citations"][0] in gold_ev)] if gold_ev else [])
                else:
                    after = before
                rule[(sp, "before")] += sum(before)
                rule[(sp, "after")] += sum(after)
                rule[(sp, "parts")] += len(before)
            # 2. 답이 스스로 자료에 없다고 말하는 경우
            if x["resp"].get("action") == "answer":
                text = x["resp"].get("answer") or ""
                cut = text.find(QUOTE_LEAD)
                body = text[:cut] if cut > 0 else text
                adm[(sp, "answer 로 나간 답")] += 1
                if ADMITS.search(body):
                    adm[(sp, "스스로 자료에 없다고 함")] += 1
                    adm[(sp, "그중 정답 " + L["action"])] += 1
    print(f"\n== 1. 저장된 실행 {runs}개 (진단 정보가 있는 것)")
    for sp in ("tune", "holdout"):
        print(f"  {sp}: 튜터 문항·회 {ev[(sp, 'tutor rows')]}")
        for why in ("quote", "abstain"):
            keys = sorted(k[2] for k in ev if k[0] == sp and k[1] == why)
            print(f"    first_why={why}: " + ", ".join(f"{k} {ev[(sp, why, k)]}" for k in keys))
        if rule[(sp, "parts")]:
            print(f"    quote 인 경우의 규칙 점수 합(action + 인용): {rule[(sp, 'before')]:.0f} → {rule[(sp, 'after')]:.0f} (항목 {rule[(sp, 'parts')]}개)")
    print("\n== 2. answer 로 나간 답이 스스로 '자료에 나와 있지 않다'고 하는 경우")
    for sp in ("tune", "holdout"):
        keys = sorted(k[1] for k in adm if k[0] == sp)
        print(f"  {sp}: " + ", ".join(f"{k} {adm[(sp, k)]}" for k in keys))


if __name__ == "__main__":
    main()
