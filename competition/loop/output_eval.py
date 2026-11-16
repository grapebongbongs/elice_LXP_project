#!/usr/bin/env python3
"""가드의 후보 답변 판정(check_output)만 따로 재 보는 작은 실험. 런박스에서 실행한다.

    python3 loop/output_eval.py                 # 후보 답변이 있는 공개 문항 86개, 문항당 LLM 호출 1번
    python3 loop/output_eval.py --repeat 2 --dump /tmp/rb/out.json

입력 판정과 무관하게 답변 판정 자체의 품질을 본다: 근거 여부 정확도, 근거 없는 구간 F1(공식 채점기와 같은 계산), 안전 판정.
입력이 block 인 문항은 실제 서버에서 근거 있음·구간 없음으로 고정되므로(app/guard/config.py) 여기서는 따로 집계한다.
tune(70%)과 holdout(30%)을 나눠 보고하고, holdout 문항은 내용을 출력하지 않는다.
"""
import argparse
import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "loop"))
sys.path.insert(0, str(ROOT / "grader"))
from exp_run import load_env_files, pick  # noqa: E402
import scoring  # noqa: E402

os.environ.update({k: v for k, v in load_env_files().items() if k not in os.environ})
sys.path.insert(0, str(ROOT / "baseline"))
from app import config  # noqa: E402
from app.common.kb import KnowledgeBase  # noqa: E402
from app.guard import pipeline as guard  # noqa: E402


def is_holdout(item_id: str) -> bool:
    return int(hashlib.sha1(item_id.encode()).hexdigest(), 16) % 10 < 3


def mean(xs):
    return round(sum(xs) / len(xs), 3) if xs else None


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--dump")
    ap.add_argument("--show", type=int, default=12, help="구간이 어긋난 tune 문항을 몇 개까지 보여 줄지")
    a = ap.parse_args()
    kb = KnowledgeBase(config.KB_DIR)
    items = [json.loads(l) for l in (pick("public") / "guard.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    items = [d for d in items if d.get("candidate_answer")]
    sem = asyncio.Semaphore(a.concurrency)

    async def one(d):
        async with sem:
            try:
                return await guard.check_output(d["messages"], d["candidate_answer"], kb)
            except Exception as e:
                return {"grounded": True, "unsupported": [], "safe": True, "error": type(e).__name__}

    dump = {}
    for r in range(a.repeat):
        outs = await asyncio.gather(*(one(d) for d in items))
        print(f"\n=== run {r + 1}/{a.repeat}: 후보 답변 {len(items)}개")
        for split in ("tune", "holdout"):
            rows = [(d, o) for d, o in zip(items, outs) if is_holdout(d["id"]) == (split == "holdout")]
            allow = [(d, o) for d, o in rows if d["labels"]["decision"] == "allow"]
            ung = [(d, o) for d, o in allow if d["labels"].get("unsupported")]
            g_acc = mean([float(o["grounded"] == d["labels"]["grounded"]) for d, o in allow])
            f1_all = mean([scoring.span_f1(o["unsupported"], d["labels"].get("unsupported") or []) for d, o in allow])
            f1_ung = mean([scoring.span_f1(o["unsupported"], d["labels"]["unsupported"]) for d, o in ung])
            block = [(d, o) for d, o in rows if d["labels"]["decision"] == "block"]
            print(f"  {split}: allow 문항 {len(allow)}개 — 근거 여부 정확도 {g_acc}, 구간 F1 전체 {f1_all}, 근거 없는 답({len(ung)}개)만 {f1_ung}"
                  f" | block 문항 {len(block)}개 (서버에서는 고정값)")
        if r == 0:
            shown = 0
            for d, o in zip(items, outs):
                gold = d["labels"].get("unsupported") or []
                f1 = scoring.span_f1(o["unsupported"], gold)
                if d["labels"]["decision"] == "allow" and f1 < 0.9 and not is_holdout(d["id"]) and shown < a.show:
                    shown += 1
                    print(f"    f1={f1:.2f} grounded gold={d['labels']['grounded']} pred={o['grounded']}\n       gold: {gold}\n       pred: {o['unsupported']}")
        for d, o in zip(items, outs):
            dump.setdefault(d["id"], []).append({"grounded": o["grounded"], "unsupported": o["unsupported"], "safe": o["safe"]})
    if a.dump:
        Path(a.dump).write_text(json.dumps(dump, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
