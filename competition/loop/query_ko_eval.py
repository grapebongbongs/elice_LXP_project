#!/usr/bin/env python3
"""영어 위주 질문의 한국어 검색어 변환만 따로 재 보는 작은 실험. 런박스에서 실행한다.

    python3 loop/query_ko_eval.py --repeat 4
    python3 loop/query_ko_eval.py --repeat 4 --configs base,gloss,base3

공개 세트 튜터 문항 중 마지막 발화의 영문자 비율이 TUTOR_ENGLISH_QUERY_RATIO 이상인 문항(약 20개)만 쓴다.
- 정답 근거가 있는 문항: 변환 설정별로 근거 섹션의 검색 순위(1위 비율)를 잰다. 변환 호출은 실행마다 달라지므로 --repeat 로 여러 번 잰다.
- 모든 문항: 변환한 한국어 문장에 '이름 붙은 규칙' 꼴(app/common/focus.py 의 NAMED_RULE)을 적용했을 때 걸리는 문항의 정답 action 분포.
LLM 호출은 변환 호출뿐이다(문항 수 × 설정별 호출 수 × 반복). tune(70%)과 holdout(30%)을 따로 보고하고 holdout 문항은 내용을 출력하지 않는다.
"""
import argparse
import asyncio
import hashlib
import json
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "loop"))
from exp_run import load_env_files, pick  # noqa: E402

os.environ.update({k: v for k, v in load_env_files().items() if k not in os.environ})
sys.path.insert(0, str(ROOT / "baseline"))
from app import config  # noqa: E402
from app.common import focus  # noqa: E402
from app.common.kb import KnowledgeBase  # noqa: E402
from app.common.normalize import normalize_messages  # noqa: E402
from app.tutor import config as C  # noqa: E402
from app.tutor import pipeline as tutor  # noqa: E402

# 이름: (목차 보여 주기, 호출 횟수, 추론 수준, 최대 토큰)
CONFIGS = {
    "base": (False, 1, "minimal", 400),
    "base3": (False, 3, "minimal", 400),
    "gloss": (True, 1, "minimal", 400),
    "gloss3": (True, 3, "minimal", 400),
    "gloss-low": (True, 1, "low", 1000),
}


def is_holdout(item_id: str) -> bool:
    return int(hashlib.sha1(item_id.encode()).hexdigest(), 16) % 10 < 3


def short(text: str, n: int = 150) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def missing_named_rule(text: str, material: str) -> bool:
    """focus.asks_missing_named_rule 과 같은 판단을 주어진 문장(text)에 적용한다."""
    m = focus.NAMED_RULE.search(text)
    if not m:
        return False
    body = "".join(material.lower().split())
    mod, head = m.group(1).lower(), m.group(2)
    return (mod + head) not in body and (mod not in body or head not in body)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeat", type=int, default=4)
    ap.add_argument("--configs", default="base,base3,gloss,gloss3")
    ap.add_argument("--concurrency", type=int, default=8)
    a = ap.parse_args()
    kb = KnowledgeBase(config.KB_DIR)
    items = [json.loads(l) for l in (pick("public") / "tutor.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    rows = []
    for d in items:
        msgs = normalize_messages(d["messages"])
        last = [m["content"] for m in msgs if m["role"] == "user"][-1]
        if tutor.english_ratio(last) >= C.ENGLISH_QUERY_RATIO:
            rows.append((d, msgs, last))
    print(f"영어 위주 튜터 문항 {len(rows)}개 (holdout {sum(is_holdout(d['id']) for d, _, _ in rows)}개), 반복 {a.repeat}회")
    sem = asyncio.Semaphore(a.concurrency)

    async def one(msgs, last):
        # tutor.retrieve 와 같은 순서로 합친다(변환한 검색어를 함께 받아 보려고 여기서 다시 적었다)
        query = tutor.build_query(msgs)
        hits = kb.search(query, k=C.TOP_K)
        async with sem:
            try:
                ko = await tutor.korean_query(last, kb)
            except Exception:
                ko = ""
        if ko:
            ko_hits = kb.search(f"{ko} {ko} {query}", k=C.TOP_K)
            seen = {s.id for s, _ in ko_hits}
            hits = (ko_hits + [(s, sc) for s, sc in hits if s.id not in seen])[: C.TOP_K]
        return ko, hits

    for name in [n for n in a.configs.split(",") if n]:
        C.QUERY_KO_GLOSSARY, C.QUERY_KO_SAMPLES, C.QUERY_KO_EFFORT, C.QUERY_KO_MAX_TOKENS = CONFIGS[name]
        ranks: dict[str, list[int]] = {}
        named: Counter = Counter()
        empty = 0
        for _ in range(a.repeat):
            outs = await asyncio.gather(*(one(msgs, last) for _, msgs, last in rows))
            for (d, _, _), (ko, hits) in zip(rows, outs):
                empty += not ko
                order = [s.id for s, _ in hits]
                ev = d["labels"].get("evidence") or []
                if ev:
                    ranks.setdefault(d["id"], []).append(min((order.index(e) + 1 if e in order else 9) for e in ev))
                if ko and hits and missing_named_rule(ko, hits[0][0].text):
                    named[("holdout" if is_holdout(d["id"]) else "tune", d["labels"]["action"])] += 1
        print(f"\n=== {name}: 목차={CONFIGS[name][0]} 호출={CONFIGS[name][1]} 추론={CONFIGS[name][2]} | 빈 변환 {empty}건")
        for split in ("tune", "holdout"):
            ids = [i for i in ranks if is_holdout(i) == (split == "holdout")]
            top1 = sum(r == 1 for i in ids for r in ranks[i])
            total = sum(len(ranks[i]) for i in ids)
            per_item = sorted(Counter(f"{sum(r == 1 for r in ranks[i])}/{len(ranks[i])}" for i in ids).items())
            print(f"  {split}: 근거 있는 문항 {len(ids)}개, 1위 {top1}/{total} | 문항별 1위 횟수 {per_item}")
        print(f"  변환문에 이름 붙은 규칙 꼴이 걸린 횟수(정답 action 별): {sorted(named.items())}")
        if name == a.configs.split(",")[0]:
            by_id = {d["id"]: (d, last) for d, _, last in rows}
            for i, rs in ranks.items():
                if not is_holdout(i) and any(r != 1 for r in rs):
                    print(f"    tune 1위 아님 {rs}: {short(by_id[i][1])}")


if __name__ == "__main__":
    asyncio.run(main())
