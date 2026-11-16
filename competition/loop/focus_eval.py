#!/usr/bin/env python3
"""경계 판정(app/common/focus.py)만 따로 재 보는 작은 실험. 런박스에서 실행한다.

    python3 loop/focus_eval.py            # 단서가 있는 공개 문항(약 70개)만 호출
    python3 loop/focus_eval.py --repeat 2

공개 세트에서 단서(override / submit)가 있는 문항만 골라 경계 판정을 부르고, 라벨과 비교한다.
각 판정(app/common/focus.py 의 CHECKS)마다, 정답 범주가 그 판정의 범주이면 pos, 아니면 neg 가 맞다.
예) override: jailbreak_injection 이면 direct, 아니면 study / submit: academic_dishonesty 이면 make, 아니면 ask
tune(70%)과 holdout(30%)을 따로 보고한다. 프롬프트의 예시는 tune 문항에서만 가져왔으므로 holdout 수치가 기준이다.
holdout 문항은 틀려도 내용을 출력하지 않는다(id 만).
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
from app.common import focus  # noqa: E402
from app.common.normalize import normalize_messages  # noqa: E402

CHECKS = {c.name: c for c in focus.CHECKS}


def is_holdout(item_id: str) -> bool:
    return int(hashlib.sha1(item_id.encode()).hexdigest(), 16) % 10 < 3


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--only", default="", help="쉼표로 구분한 판정 이름 (기본: 전부)")
    ap.add_argument("--dump", help="문항별 판정을 이 파일에 저장 (공개 세트 id 와 판정만)")
    ap.add_argument("--votes", type=int, default=1, help="판정을 이만큼 불러 과반을 쓴다 (1 이면 한 번)")
    ap.add_argument("--endpoint", choices=["tutor", "guard"], help="한쪽 문항만")
    a = ap.parse_args()
    pub = pick("public")
    items = [json.loads(l) for f in ("tutor", "guard") for l in (pub / f"{f}.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    if a.endpoint:
        items = [d for d in items if d["endpoint"] == a.endpoint]
    sem = asyncio.Semaphore(a.concurrency)
    dump: dict = {}

    only = {n for n in a.only.split(",") if n}
    for name, chk in CHECKS.items():
        if only and name not in only:
            continue
        cue, pos, neg, pos_cat = chk.cue, chk.pos, chk.neg, chk.category
        subset = [d for d in items if cue.search(focus.last_user_text(d["messages"]))]
        missed = [d for d in items if d["labels"]["category"] == pos_cat and d not in subset]

        async def one(d):
            async with sem:
                msgs = normalize_messages(d["messages"])
                return await (focus.ask_voted(chk, msgs, a.votes) if a.votes > 1 else focus.ask(chk, msgs))

        for r in range(a.repeat):
            preds = await asyncio.gather(*(one(d) for d in subset))
            for d, p in zip(subset, preds):
                dump.setdefault(name, {}).setdefault(d["id"], []).append(p)
            print(f"\n=== {name} (run {r + 1}/{a.repeat}): 단서 있는 문항 {len(subset)}개, 단서 없는 {pos_cat} {len(missed)}개")
            for split in ("tune", "holdout"):
                rows = [(d, p) for d, p in zip(subset, preds) if is_holdout(d["id"]) == (split == "holdout")]
                conf = Counter()
                for d, p in rows:
                    want = pos if d["labels"]["category"] == pos_cat else neg
                    conf[(want, p)] += 1
                ok = sum(v for (w, p), v in conf.items() if w == p)
                print(f"  {split}: 정확도 {ok}/{len(rows)}  (정답→판정) {dict(sorted((f'{w}→{p}', v) for (w, p), v in conf.items()))}")
                for d, p in rows:
                    want = pos if d["labels"]["category"] == pos_cat else neg
                    if p != want:
                        text = "(holdout: 내용 생략)" if split == "holdout" else focus.last_user_text(d["messages"]).replace("\n", " / ")[:160]
                        print(f"     틀림 [{d['labels']['category']}] 판정={p} {d['id']} {text}")
        for d in missed:
            if not is_holdout(d["id"]):
                print(f"  단서 없음(tune) [{pos_cat}] {focus.last_user_text(d['messages']).replace(chr(10), ' / ')[:160]}")
        print(f"  단서 없음(holdout) {sum(is_holdout(d['id']) for d in missed)}개")
    if a.dump:
        Path(a.dump).write_text(json.dumps(dump, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
