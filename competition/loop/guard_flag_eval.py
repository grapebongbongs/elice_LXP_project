#!/usr/bin/env python3
"""가드 입력 판정의 설정 하나를 켠 것과 끈 것을, 고른 문항에서만 같은 시각에 견준다. 런박스에서 실행한다.

    python3 loop/guard_flag_eval.py --flag HARM_CLAUSE --categories harmful_content,normal --repeat 2
    python3 loop/guard_flag_eval.py --flag HARM_CLAUSE --categories harmful_content --repeat 3

- --flag 는 app/guard/config.py 의 참·거짓 설정 이름이다(예: HARM_CLAUSE, STRIP_WRAPPERS, PASTE_TAIL_CHECK).
- 정답 범주가 --categories 에 든 공개 가드 문항에 guard.check_input 을 설정별로 --repeat 번 부른다.
- 차단 결정 일치(오차단·놓침)와 범주 일치를 tune(70%)·holdout(30%)으로 나눠 센다. holdout 문항은 내용을 출력하지 않는다.
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
from app.common.kb import KnowledgeBase  # noqa: E402
from app.guard import config as C  # noqa: E402
from app.guard import pipeline as guard  # noqa: E402


def is_holdout(item_id: str) -> bool:
    return int(hashlib.sha1(item_id.encode()).hexdigest(), 16) % 10 < 3


def short(text: str, n: int = 130) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--flag", required=True)
    ap.add_argument("--categories", default="", help="정답 범주(쉼표 구분). 비우면 전체")
    ap.add_argument("--repeat", type=int, default=2)
    ap.add_argument("--concurrency", type=int, default=8)
    a = ap.parse_args()
    if not isinstance(getattr(C, a.flag, None), bool):
        sys.exit(f"app/guard/config.py 에 참·거짓 설정 {a.flag} 가 없습니다")
    cats = {c for c in a.categories.split(",") if c}
    kb = KnowledgeBase(config.KB_DIR)
    items = [json.loads(l) for l in (pick("public") / "guard.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    items = [d for d in items if not cats or d["labels"]["category"] in cats]
    print(f"설정 {a.flag}: 가드 문항 {len(items)}개 ({dict(Counter(d['labels']['category'] for d in items))}), 설정별 {a.repeat}회")
    sem = asyncio.Semaphore(a.concurrency)

    async def one(d):
        async with sem:
            try:
                r = await asyncio.wait_for(guard.check_input(d["messages"], kb), 40)
                return r.get("decision"), r.get("category")
            except Exception as e:
                return "error", type(e).__name__

    saved = getattr(C, a.flag)
    results: dict[bool, dict[str, list]] = {False: {}, True: {}}
    try:
        for _ in range(a.repeat):
            for flag in (False, True):  # 같은 시각 조건이 되도록 번갈아 부른다
                setattr(C, a.flag, flag)
                outs = await asyncio.gather(*(one(d) for d in items))
                for d, o in zip(items, outs):
                    results[flag].setdefault(d["id"], []).append(o)
    finally:
        setattr(C, a.flag, saved)

    def wrong(d, o):
        lab = d["labels"]
        if o[0] == "error":
            return "error"
        gold_block = lab["decision"] == "block"
        if (o[0] == "block") != gold_block:
            return "놓침" if gold_block else "오차단"
        if lab["category"] != "normal" and o[1] != lab["category"]:
            return "범주만"
        return None

    for split in ("tune", "holdout"):
        sub = [d for d in items if is_holdout(d["id"]) == (split == "holdout")]
        if not sub:
            continue
        for cat in sorted({d["labels"]["category"] for d in sub}):
            part = [d for d in sub if d["labels"]["category"] == cat]
            line = []
            for flag in (False, True):
                kinds = Counter(w for d in part for o in results[flag][d["id"]] if (w := wrong(d, o)))
                line.append(f"{'켬' if flag else '끔'}: {dict(sorted(kinds.items())) or '오류 없음'}")
            better = sum(sum(bool(wrong(d, o)) for o in results[True][d["id"]]) < sum(bool(wrong(d, o)) for o in results[False][d["id"]]) for d in part)
            worse = sum(sum(bool(wrong(d, o)) for o in results[True][d["id"]]) > sum(bool(wrong(d, o)) for o in results[False][d["id"]]) for d in part)
            print(f"[{split}] {cat} {len(part)}문항 × {a.repeat}회 | " + " | ".join(line) + f" | 나아진 문항 {better}, 나빠진 문항 {worse}")
        if split == "tune":
            for d in sub:
                w0 = [wrong(d, o) for o in results[False][d["id"]]]
                w1 = [wrong(d, o) for o in results[True][d["id"]]]
                if w0 != w1:
                    last = [m["content"] for m in d["messages"] if m["role"] == "user"][-1]
                    print(f"   정답={d['labels']['category']} 끔={[w or 'ok' for w in w0]} 켬={[w or 'ok' for w in w1]} :: {short(last)}")


if __name__ == "__main__":
    asyncio.run(main())
