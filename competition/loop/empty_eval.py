#!/usr/bin/env python3
"""LLM 응답이 비거나(JSON 없음) 토큰 한도에서 끊기는 비율을 호출 종류별로 잰다. 런박스에서 실행한다.

    python3 loop/empty_eval.py [--part guard|tutor|all] [--repeat 1] [--concurrency 12]

chat_json 은 응답 글이 비었거나 JSON 이 아니면 None 을 돌려주고, 쓰는 쪽은 기본값으로 넘어간다
(가드 입력 판정은 normal·allow, 튜터 생성은 abstain, 경계 판정은 판정 없음). 이 경로는 로그에 남지 않는다.
추론 토큰도 max_completion_tokens 에 들어가므로, 추론이 길어지면 답을 쓰기 전에 한도에 걸려 응답이 빈다.

공개 세트 문항을 서버와 같은 함수(app.guard.pipeline.handle, app.tutor.pipeline.handle)로 한 번씩 처리하면서
호출마다 finish_reason, 응답이 비었는지, 완성 토큰 수(추론 포함)를 모은다. 출력은 개수와 분위수뿐이다(본문 없음).
문항별로 '빈 응답이 있었는가'와 정답 여부를 짝지어, 빈 응답이 오류로 이어지는지도 센다.
"""
import argparse
import asyncio
import contextvars
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "loop"))
from exp_run import is_holdout, load_env_files, pick  # noqa: E402

os.environ.update({k: v for k, v in load_env_files().items() if k not in os.environ})
sys.path.insert(0, str(ROOT / "baseline"))
from app import config  # noqa: E402
from app.common import focus, llm  # noqa: E402
from app.common.kb import KnowledgeBase  # noqa: E402
from app.common.schema import enforce_guard, enforce_tutor, normalize_request  # noqa: E402
from app.guard import pipeline as guard  # noqa: E402
from app.guard import prompts as guard_prompts  # noqa: E402
from app.tutor import pipeline as tutor  # noqa: E402
from app.tutor import prompts as tutor_prompts  # noqa: E402

ITEM = contextvars.ContextVar("item", default=None)
CALLS: list[dict] = []
PREFIX: dict[str, str] = {}
for mod, short in ((guard_prompts, "guard"), (tutor_prompts, "tutor"), (focus, "focus")):
    for name, val in vars(mod).items():
        if isinstance(val, str) and len(val) > 120 and name.isupper():
            PREFIX.setdefault(val[:48], f"{short}.{name}")

_orig = llm._call_once


async def _call_once(kwargs, effort, started=None):
    resp = await _orig(kwargs, effort, started)
    try:
        ch = resp.choices[0]
        usage = getattr(resp, "usage", None)
        details = getattr(usage, "completion_tokens_details", None) if usage else None
        system = kwargs["messages"][0]["content"]
        CALLS.append({
            "item": ITEM.get(),
            "kind": PREFIX.get(system[:48], "other"),
            "effort": effort,
            "cap": kwargs.get("max_completion_tokens"),
            "finish": ch.finish_reason,
            "empty": llm._parse_json(ch.message.content or "") is None,
            "blank": not (ch.message.content or "").strip(),
            "completion": getattr(usage, "completion_tokens", None) if usage else None,
            "reasoning": getattr(details, "reasoning_tokens", None) if details else None,
            "prompt": getattr(usage, "prompt_tokens", None) if usage else None,
        })
    except Exception:
        pass
    return resp


llm._call_once = _call_once


def pct(xs, q):
    xs = sorted(x for x in xs if x is not None)
    return xs[min(len(xs) - 1, int(len(xs) * q))] if xs else None


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--part", default="all")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--concurrency", type=int, default=12)
    a = ap.parse_args()
    kb = KnowledgeBase(config.KB_DIR)
    sem = asyncio.Semaphore(a.concurrency)
    items = []
    for part in ("guard", "tutor"):
        if a.part in (part, "all"):
            for line in (pick("public") / f"{part}.jsonl").read_text(encoding="utf-8").splitlines():
                if line.strip():
                    items.append((part, json.loads(line)))
    results = {}

    async def one(part, d, rep):
        key = f"{d['id']}#{rep}"
        ITEM.set(key)
        async with sem:
            req = normalize_request({k: d[k] for k in ("messages", "candidate_answer") if k in d})
            try:
                if part == "guard":
                    raw = await guard.handle(req, kb)
                    cand = req.get("candidate_answer")
                    resp = enforce_guard(raw, cand is not None, cand or "")
                    ok = resp["input"]["decision"] == d["labels"]["decision"]
                else:
                    raw = await tutor.handle(req, kb)
                    resp = enforce_tutor(raw, valid_ids=set(kb.sections) or None)
                    ok = resp["action"] == d["labels"]["action"]
                results[key] = (part, d, ok, resp)
            except Exception as e:
                results[key] = (part, d, None, type(e).__name__)

    for rep in range(a.repeat):
        await asyncio.gather(*(asyncio.create_task(one(p, d, rep)) for p, d in items))

    print(f"items {len(items)} x{a.repeat}, llm calls {len(CALLS)}")
    by = defaultdict(list)
    for c in CALLS:
        by[(c["kind"], c["effort"], c["cap"])].append(c)
    print("== 호출 종류별: 호출 수, 빈 응답(JSON 없음), 글자 없음, finish_reason, 완성 토큰(추론 포함) 분위수, 추론 토큰 p50/p90")
    for k in sorted(by, key=lambda k: -len(by[k])):
        cs = by[k]
        fin = Counter(c["finish"] for c in cs)
        comp = [c["completion"] for c in cs]
        reas = [c["reasoning"] for c in cs]
        print(f"  {k[0]:34s} effort={k[1]:10s} cap={k[2]}: n={len(cs):4d} empty={sum(c['empty'] for c in cs):3d} blank={sum(c['blank'] for c in cs):3d} "
              f"finish={dict(fin)} completion p50/p90/p99/max={pct(comp, .5)}/{pct(comp, .9)}/{pct(comp, .99)}/{pct(comp, 1)} "
              f"reasoning p50/p90={pct(reas, .5)}/{pct(reas, .9)} prompt p50/p90={pct([c['prompt'] for c in cs], .5)}/{pct([c['prompt'] for c in cs], .9)}")
    empties = defaultdict(list)
    for c in CALLS:
        if c["empty"]:
            empties[c["item"]].append(c["kind"])
    print("== 문항 단위: 빈 응답이 있었던 문항과 정답 여부(가드는 차단 결정, 튜터는 action)")
    for part in ("guard", "tutor"):
        rows = [(k, v) for k, v in results.items() if v[0] == part]
        if not rows:
            continue
        for split in ("tune", "holdout"):
            sel = [(k, v) for k, v in rows if is_holdout(v[1]["id"]) == (split == "holdout")]
            with_e = [(k, v) for k, v in sel if k in empties]
            wo = [(k, v) for k, v in sel if k not in empties]
            f = lambda xs: f"{sum(1 for _, v in xs if v[2])}/{len(xs)}"
            kinds = Counter(kind for k, _ in with_e for kind in empties[k])
            print(f"  {part} {split}: 빈 응답 있는 문항 정답 {f(with_e)}, 없는 문항 정답 {f(wo)}, 처리 오류 {sum(1 for _, v in sel if v[2] is None)} | 빈 응답 종류 {dict(kinds)}")
        if part == "guard":
            miss = [(k, v) for k, v in rows if v[2] is False and v[1]["labels"]["decision"] == "block"]
            print(f"  guard 놓친 차단 {len(miss)}건 중 빈 응답이 있었던 것 {sum(1 for k, _ in miss if k in empties)}건")
        else:
            wrong_abstain = [(k, v) for k, v in rows if isinstance(v[3], dict) and v[1]["labels"]["action"] == "answer" and v[3]["action"] == "abstain"]
            print(f"  tutor 정답 answer 를 abstain 한 {len(wrong_abstain)}건 중 빈 응답이 있었던 것 {sum(1 for k, _ in wrong_abstain if k in empties)}건")


if __name__ == "__main__":
    asyncio.run(main())
