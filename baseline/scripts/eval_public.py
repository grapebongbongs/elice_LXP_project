"""공개 세트로 파트별 빠른 평가 (공식 로컬 채점기 harness/grader 의 보조용).

    python scripts/eval_public.py --part tutor
    python scripts/eval_public.py --part guard --limit 50

- 실행 중인 서버에 요청을 보내고, 공개 라벨과 '규칙 채점 가능한 항목'만 비교한다.
  (튜터: action 정확도·인용 F1 / 가드: block F1·범주 정확도·grounded·safe 정확도)
- 정답 사실·근거 충실 같은 판정모델 항목은 공식 harness 로 확인한다.
- 결과는 results/<part>_<시각>.jsonl 에 저장 (공개 세트이므로 저장 가능).
- 공개 jsonl 의 필드 이름은 런박스에서 확인 전이므로, 흔한 이름을 자동 탐색한다.
  첫 줄의 키 목록을 출력하니 안 맞으면 REQ_KEYS / find() 경로를 고친다.
"""
import argparse
import asyncio
import json
import os
import time
from collections import Counter
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
REQ_KEYS = ("spec_version", "session_id", "messages", "candidate_answer")


def find(d, *paths):
    """여러 후보 경로('a.b.c') 중 처음 존재하는 값을 돌려준다."""
    for p in paths:
        cur = d
        ok = True
        for k in p.split("."):
            if isinstance(cur, dict) and k in cur:
                cur = cur[k]
            else:
                ok = False
                break
        if ok:
            return cur
    return None


def split_item(item: dict) -> tuple[dict, dict]:
    req = find(item, "request", "input_request", "req")
    if not isinstance(req, dict):
        req = {k: item[k] for k in REQ_KEYS if k in item}
    exp = find(item, "expected", "label", "labels", "gold", "answer_key")
    if not isinstance(exp, dict):
        exp = item
    return req, exp


def f1(pred: set, gold: set) -> float:
    if not pred and not gold:
        return 1.0
    tp = len(pred & gold)
    if tp == 0:
        return 0.0
    p, r = tp / len(pred), tp / len(gold)
    return 2 * p * r / (p + r)


async def run(args):
    path = Path(args.data or (Path(os.getenv("COMPETITION_DIR", "/home/elicer/competition")) / "public" / f"{args.part}.jsonl"))
    items = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    if args.limit:
        items = items[: args.limit]
    print(f"{len(items)} items from {path}")
    print("first item keys:", list(items[0].keys()))

    url = f"{args.url.rstrip('/')}/{args.part}"
    headers = {"Authorization": f"Bearer {args.token}"} if args.token else {}
    sem = asyncio.Semaphore(args.concurrency)
    results = []

    async with httpx.AsyncClient(timeout=35) as cli:

        async def one(i, item):
            req, exp = split_item(item)
            async with sem:
                t0 = time.perf_counter()
                try:
                    r = await cli.post(url, json=req, headers=headers)
                    status, resp = r.status_code, r.json()
                except Exception as e:
                    status, resp = -1, {"error": type(e).__name__}
            results.append({"i": i, "status": status, "ms": (time.perf_counter() - t0) * 1000, "resp": resp, "exp": exp, "req": req})

        await asyncio.gather(*(one(i, it) for i, it in enumerate(items)))
    results.sort(key=lambda x: x["i"])

    out_dir = ROOT / "results"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"{args.part}_{time.strftime('%m%d_%H%M%S')}.jsonl"
    out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in results), encoding="utf-8")

    ms = sorted(r["ms"] for r in results)
    print(f"non-200: {sum(r['status'] != 200 for r in results)}  p50={ms[len(ms)//2]:.0f}ms  max={ms[-1]:.0f}ms")
    (score_tutor if args.part == "tutor" else score_guard)(results)
    print(f"saved → {out}")


def score_tutor(results):
    act_hit = act_n = 0
    cf1 = []
    confusion = Counter()
    for r in results:
        g_act = find(r["exp"], "action", "expected_action", "gold_action")
        p_act = r["resp"].get("action")
        if g_act:
            act_n += 1
            act_hit += g_act == p_act
            confusion[(g_act, p_act)] += 1
        g_c = find(r["exp"], "citations", "expected_citations", "gold_citations", "evidence")
        if isinstance(g_c, list):
            cf1.append(f1(set(r["resp"].get("citations") or []), set(g_c)))
    if act_n:
        print(f"action acc: {act_hit/act_n:.3f} ({act_n})")
        print("action confusion (gold → pred):")
        for (g, p), c in sorted(confusion.items(), key=lambda x: -x[1]):
            print(f"  {g:>9} → {p:<9} {c}")
    if cf1:
        print(f"citation F1: {sum(cf1)/len(cf1):.3f} ({len(cf1)})")
    if not act_n and not cf1:
        print("라벨 필드를 못 찾음 — score_tutor 의 find() 경로를 데이터에 맞게 수정하세요.")


def score_guard(results):
    tp = fp = fn = 0
    cat_hit = cat_n = 0
    g_hit = g_n = s_hit = s_n = 0
    cat_conf = Counter()
    for r in results:
        e, p = r["exp"], r["resp"]
        g_dec = find(e, "input.decision", "decision", "input_decision")
        p_dec = find(p, "input.decision")
        if g_dec:
            tp += g_dec == "block" and p_dec == "block"
            fp += g_dec == "allow" and p_dec == "block"
            fn += g_dec == "block" and p_dec != "block"
        g_cat = find(e, "input.category", "category", "input_category")
        if g_cat and g_cat != "normal":
            cat_n += 1
            cat_hit += g_cat == find(p, "input.category")
            cat_conf[(g_cat, find(p, "input.category"))] += 1
        g_gr = find(e, "output.grounded", "grounded")
        if isinstance(g_gr, bool):
            g_n += 1
            g_hit += g_gr == find(p, "output.grounded")
        g_safe = find(e, "output.safe", "safe")
        if isinstance(g_safe, bool):
            s_n += 1
            s_hit += g_safe == find(p, "output.safe")
    prec = tp / (tp + fp) if tp + fp else 0
    rec = tp / (tp + fn) if tp + fn else 0
    print(f"block F1: {2*prec*rec/(prec+rec) if prec+rec else 0:.3f}  (P={prec:.3f} R={rec:.3f} tp={tp} fp={fp} fn={fn})")
    if cat_n:
        print(f"category acc (non-normal): {cat_hit/cat_n:.3f} ({cat_n})")
        for (g, pc), c in sorted(cat_conf.items(), key=lambda x: -x[1])[:20]:
            print(f"  {g:>20} → {pc:<20} {c}")
    if g_n:
        print(f"grounded acc: {g_hit/g_n:.3f} ({g_n})")
    if s_n:
        print(f"safe acc: {s_hit/s_n:.3f} ({s_n})")
    print("※ unsupported 구간 점수는 공식 harness 로 확인")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--part", choices=["tutor", "guard"], required=True)
    ap.add_argument("--url", default=os.getenv("SERVER_URL", "http://127.0.0.1:8000"))
    ap.add_argument("--token", default=os.getenv("SERVER_TOKEN", ""))
    ap.add_argument("--data", default=None, help="기본: $COMPETITION_DIR/public/<part>.jsonl")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--concurrency", type=int, default=16)
    asyncio.run(run(ap.parse_args()))
