#!/usr/bin/env python3
"""말머리 떼기(GUARD_STRIP_WRAPPERS / TUTOR_STRIP_WRAPPERS)를 켠 것과 끈 것을, 말머리가 있는 공개 문항에서만 같은 시각에 견준다. 런박스에서 실행한다.

    python3 loop/wrapper_eval.py --repeat 3

- 대상: 마지막 발화가 app/common/focus.py 의 strip_wrappers 로 달라지는 문항(튜터·가드).
- 튜터 문항은 tutor.handle 전체(검색·분류·생성)를, 가드 문항은 guard.check_input(입력 판정)을 설정별로 --repeat 번 부른다.
- 튜터는 action 일치, 가드는 차단 결정 일치와 범주 일치를 센다. tune(70%)과 holdout(30%)을 따로 보고하고 holdout 은 개수만 낸다.
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
from app.common.normalize import normalize_text  # noqa: E402
from app.guard import pipeline as guard  # noqa: E402
from app.guard import policy  # noqa: E402
from app.tutor import pipeline as tutor  # noqa: E402


def is_holdout(item_id: str) -> bool:
    return int(hashlib.sha1(item_id.encode()).hexdigest(), 16) % 10 < 3


def short(text: str, n: int = 130) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--only", choices=["tutor", "guard"])
    a = ap.parse_args()
    kb = KnowledgeBase(config.KB_DIR)
    items = []
    for e in ("tutor", "guard"):
        if a.only and a.only != e:
            continue
        for line in (pick("public") / f"{e}.jsonl").read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            last = normalize_text([m["content"] for m in d["messages"] if m["role"] == "user"][-1])
            if focus.strip_wrappers(last) != last:
                items.append((d, last))
    print(f"말머리가 있는 문항 {len(items)}개 (튜터 {sum(d['endpoint'] == 'tutor' for d, _ in items)}, 가드 {sum(d['endpoint'] == 'guard' for d, _ in items)}), 설정별 {a.repeat}회")
    sem = asyncio.Semaphore(a.concurrency)

    async def one(d):
        async with sem:
            try:
                if d["endpoint"] == "tutor":
                    r = await asyncio.wait_for(tutor.handle({"messages": d["messages"]}, kb), 40)
                    return {"action": r.get("action")}
                r = await asyncio.wait_for(guard.check_input(d["messages"], kb), 40)
                return {"decision": r.get("decision"), "category": r.get("category")}
            except Exception as e:
                return {"error": type(e).__name__}

    results: dict[bool, dict[str, list[dict]]] = {False: {}, True: {}}
    saved = (guard.C.STRIP_WRAPPERS, tutor.C.STRIP_WRAPPERS)
    for r in range(a.repeat):
        for flag in (False, True):  # 같은 시각 조건이 되도록 번갈아 부른다
            guard.C.STRIP_WRAPPERS = tutor.C.STRIP_WRAPPERS = flag
            outs = await asyncio.gather(*(one(d) for d, _ in items))
            for (d, _), o in zip(items, outs):
                results[flag].setdefault(d["id"], []).append(o)
    guard.C.STRIP_WRAPPERS, tutor.C.STRIP_WRAPPERS = saved

    def wrong(d, o):
        lab = d["labels"]
        if "error" in o:
            return "error"
        if d["endpoint"] == "tutor":
            if o["action"] == lab["action"]:
                return None
            return f"{lab['action']}→{o['action']}"
        gold_block = lab["decision"] == "block"
        if (o["decision"] == "block") != gold_block:
            return "오차단" if not gold_block else "놓침"
        if lab["category"] != "normal" and o["category"] != lab["category"]:
            return "범주만"
        return None

    for e in ("tutor", "guard"):
        for split in ("tune", "holdout"):
            sub = [(d, last) for d, last in items if d["endpoint"] == e and is_holdout(d["id"]) == (split == "holdout")]
            if not sub:
                continue
            line = []
            for flag in (False, True):
                kinds = Counter()
                for d, _ in sub:
                    for o in results[flag][d["id"]]:
                        w = wrong(d, o)
                        if w:
                            kinds[w] += 1
                total = len(sub) * a.repeat
                line.append(f"{'떼기' if flag else '그대로'}: 오류 {sum(kinds.values())}/{total} {dict(sorted(kinds.items()))}")
            better = sum(
                sum(bool(wrong(d, o)) for o in results[True][d["id"]]) < sum(bool(wrong(d, o)) for o in results[False][d["id"]])
                for d, _ in sub
            )
            worse = sum(
                sum(bool(wrong(d, o)) for o in results[True][d["id"]]) > sum(bool(wrong(d, o)) for o in results[False][d["id"]])
                for d, _ in sub
            )
            print(f"\n[{e} {split}] 문항 {len(sub)}개 | " + " | ".join(line) + f" | 나아진 문항 {better}, 나빠진 문항 {worse}")
            if split == "tune":
                for d, last in sub:
                    w0 = [wrong(d, o) for o in results[False][d["id"]]]
                    w1 = [wrong(d, o) for o in results[True][d["id"]]]
                    if any(w0) or any(w1):
                        gold = d["labels"].get("action") or f"{d['labels']['decision']}/{d['labels']['category']}"
                        print(f"   정답={gold} 그대로={[w or 'ok' for w in w0]} 떼기={[w or 'ok' for w in w1]} :: {short(last)}")


if __name__ == "__main__":
    asyncio.run(main())
