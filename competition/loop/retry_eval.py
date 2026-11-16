#!/usr/bin/env python3
"""튜터 1차 생성 재시도(TUTOR_FIRST_PASS_RETRY)를, 1차 생성이 답을 못 낸 적이 있는 공개 문항에서만 잰다. 런박스에서 실행한다.

    python3 loop/retry_eval.py --repeat 6
    python3 loop/retry_eval.py --repeat 6 --runs quote-vote3-full paste-strip-full vote-paste-strip-full

- 대상: 저장된 실행(baseline/results/<이름>_run*.jsonl, EXP_DEBUG=1 로 돌린 것)에서 1차 생성이 한 번이라도 답을 못 낸 튜터 문항.
- 재시도를 켜고 tutor.handle 전체(검색·분류·생성)를 문항마다 --repeat 번 부른다.
  재시도는 1차 생성이 답을 못 냈을 때만 일어나므로, 같은 요청 안에서 '재시도가 없었다면'(= abstain)과 '재시도한 결과'를 함께 얻는다.
- 규칙으로 채점되는 것만 센다: action 일치, 인용 F1. 판정 모델은 부르지 않는다.
- tune(70%)과 holdout(30%)을 따로 보고하고 holdout 은 개수만 낸다.
"""
import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "loop"))
from exp_run import load_env_files, pick  # noqa: E402

os.environ.update({k: v for k, v in load_env_files().items() if k not in os.environ})
sys.path.insert(0, str(ROOT / "baseline"))
from app import config  # noqa: E402
from app.common.kb import KnowledgeBase  # noqa: E402
from app.tutor import pipeline as tutor  # noqa: E402

DEFAULT_RUNS = ["quote-vote3-full", "paste-strip-full", "vote-paste-strip-full", "tutor-quote-keep", "tutor-offtask", "rules3-full"]


def is_holdout(item_id: str) -> bool:
    return int(hashlib.sha1(item_id.encode()).hexdigest(), 16) % 10 < 3


def short(text: str, n: int = 110) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def rule_points(gold_action: str, evidence: list, action, citations) -> float:
    """판정 모델과 무관한 문항 점수: action 일치와 인용 F1(정답 근거가 있는 문항만)의 평균."""
    parts = [float(action == gold_action)]
    if evidence:
        cited = list(dict.fromkeys(citations or []))
        hit = len(set(evidence) & set(cited))
        p, r = (hit / len(cited) if cited else 0.0), hit / len(evidence)
        parts.append(2 * p * r / (p + r) if p + r else 0.0)
    return sum(parts) / len(parts)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeat", type=int, default=6)
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--retries", type=int, default=1)
    ap.add_argument("--runs", nargs="*", default=DEFAULT_RUNS, help="대상 문항을 고를 저장된 실행 이름(앞부분)")
    a = ap.parse_args()

    failed = Counter()
    files = [f for n in a.runs for f in sorted((ROOT / "baseline" / "results").glob(n + "_run*.jsonl"))]
    for f in files:
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            x = json.loads(line)
            dbg = (x.get("resp") or {}).get("_debug") or {}
            if x["endpoint"] == "tutor" and dbg.get("first_pass") == "abstain":
                failed[x["id"]] += 1
    public = {}
    for line in (pick("public") / "tutor.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            d = json.loads(line)
            public[d["id"]] = d
    items = [public[i] for i in failed if i in public]
    print(f"저장된 실행 {len(files)}개에서 1차 생성이 답을 못 낸 적이 있는 튜터 문항 {len(items)}개"
          f" (정답 answer {sum(d['labels']['action'] == 'answer' for d in items)}, abstain {sum(d['labels']['action'] == 'abstain' for d in items)},"
          f" 그 밖 {sum(d['labels']['action'] not in ('answer', 'abstain') for d in items)}), 문항마다 {a.repeat}회, 재시도 {a.retries}회")

    kb = KnowledgeBase(config.KB_DIR)
    config.EXP_DEBUG = True
    tutor.C.FIRST_PASS_RETRY = a.retries
    sem = asyncio.Semaphore(a.concurrency)

    async def one(d):
        async with sem:
            t0 = time.perf_counter()
            try:
                r = await asyncio.wait_for(tutor.handle({"messages": d["messages"]}, kb), 40)
            except Exception as e:
                return {"error": type(e).__name__, "sec": time.perf_counter() - t0}
            dbg = r.get("_debug") or {}
            return {"action": r.get("action"), "citations": r.get("citations"), "first_pass": dbg.get("first_pass"),
                    "first_why": dbg.get("first_why"), "retries": dbg.get("first_retries", 0), "sec": time.perf_counter() - t0}

    results: dict[str, list[dict]] = {d["id"]: [] for d in items}
    for _ in range(a.repeat):
        outs = await asyncio.gather(*(one(d) for d in items))
        for d, o in zip(items, outs):
            results[d["id"]].append(o)

    secs_retry = sorted(o["sec"] for os_ in results.values() for o in os_ if o.get("retries"))
    secs_all = sorted(o["sec"] for os_ in results.values() for o in os_)
    print(f"\n처리 시간(초): 전체 p50 {secs_all[len(secs_all) // 2]:.1f} 최대 {secs_all[-1]:.1f}"
          + (f" | 재시도한 요청 {len(secs_retry)}개 p50 {secs_retry[len(secs_retry) // 2]:.1f} 최대 {secs_retry[-1]:.1f}" if secs_retry else " | 재시도한 요청 0개"))
    print("오류:", dict(Counter(o["error"] for os_ in results.values() for o in os_ if "error" in o)) or "없음")

    for split in ("tune", "holdout"):
        sub = [d for d in items if is_holdout(d["id"]) == (split == "holdout")]
        total = Counter()
        before = after = 0.0
        lines = []
        for d in sub:
            lab = d["labels"]
            gold, ev = lab["action"], lab.get("evidence") or []
            kinds = Counter()
            for o in results[d["id"]]:
                if "error" in o:
                    kinds["error"] += 1
                    continue
                # 재시도가 없었다면: 1차 생성이 답을 못 냈으므로 abstain(인용 없음)
                tried = o["first_why"] is not None and o["first_pass"] in ("retry", "abstain")
                pts_after = rule_points(gold, ev, o["action"], o["citations"])
                pts_before = rule_points(gold, ev, "abstain", []) if tried else pts_after
                before += pts_before
                after += pts_after
                if not tried:
                    kinds["1차에서 끝남"] += 1
                elif o["first_pass"] == "retry":
                    kinds[f"재시도로 답함({'+' if pts_after > pts_before else '-' if pts_after < pts_before else '='})"] += 1
                else:
                    kinds["재시도해도 abstain" if o["retries"] else "재시도 안 함(시간)"] += 1
            for k, v in kinds.items():
                total[(gold, k)] += v
            lines.append((d, kinds))
        n = len(sub) * a.repeat
        print(f"\n[{split}] 문항 {len(sub)}개 × {a.repeat}회 = {n}건 | 규칙 기반 점수 합: 재시도 없음 {before:.2f} → 재시도 {after:.2f}"
              f" (차이 {after - before:+.2f}, 전체 1회 실행으로 환산 {(after - before) / a.repeat:+.2f}문항)")
        for (gold, k), v in sorted(total.items()):
            print(f"   정답 {gold}: {k} {v}")
        if split == "tune":
            for d, kinds in lines:
                users = [m["content"] for m in d["messages"] if m["role"] == "user"]
                print(f"   - 정답={d['labels']['action']} 저장된 실행에서 답 못 냄 {failed[d['id']]}/{len(files)} {dict(kinds)} :: {short(users[-1])}")


if __name__ == "__main__":
    asyncio.run(main())
