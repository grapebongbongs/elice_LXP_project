#!/usr/bin/env python3
"""저장된 공개 세트 실행 결과(baseline/results/*.jsonl)에서 틀린 문항을 오류 유형별로 모아 본다.

    python3 loop/errors.py selfintro-full                 # 이름이 이 글자로 시작하는 실행 전부
    python3 loop/errors.py selfintro-full --only block    # block | category | action | cite | output

- 여러 번 돌린 실행을 함께 주면 문항마다 "몇 번 중 몇 번 틀렸는지"를 센다. 반복해서 틀리는 문항이 고칠 대상이다.
- holdout 문항(문항 id 해시 30%)은 **개수와 유형만** 보여 주고 내용은 출력하지 않는다(LOOP.md §2:
  프롬프트의 예시·규칙은 holdout 이 아닌 문항만 보고 만든다).
- 공개 세트만 다룬다. 채점 요청은 어디에도 저장되지 않으므로 이 도구로 볼 수 없다.
"""
import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMP = Path("/home/elicer/competition")


def pick(name: str) -> Path:
    for base in (ROOT, COMP):
        if (base / name).exists():
            return base / name
    sys.exit(f"{name} 폴더를 찾지 못했습니다")


def short(text: str, n: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="+", help="실행 이름(앞부분) 또는 결과 파일 경로")
    ap.add_argument("--only", choices=["block", "category", "action", "cite", "output"])
    ap.add_argument("--chars", type=int, default=220, help="발화를 몇 글자까지 보여 줄지")
    ap.add_argument("--min", type=int, default=1, help="이 횟수 이상 틀린 문항만")
    a = ap.parse_args()

    files = []
    for n in a.names:
        p = Path(n)
        files += [p] if p.exists() else sorted((ROOT / "baseline" / "results").glob(n + "*_run*.jsonl"))
    if not files:
        sys.exit("결과 파일이 없습니다")
    public = {}
    for e in ("tutor", "guard"):
        for line in (pick("public") / f"{e}.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                p = json.loads(line)
                public[p["id"]] = p

    # kind -> id -> [예측 요약, ...]
    wrong: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    rows_by_id = {}
    for f in files:
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            x = json.loads(line)
            r, resp, lab = x["r"], x.get("resp") or {}, x["labels"]
            rows_by_id[x["id"]] = x
            dbg = resp.get("_debug") or {}
            if x["endpoint"] == "guard":
                inp = resp.get("input") or {}
                if r["false_block"]:
                    wrong["block:오차단"][x["id"]].append(str(inp.get("category")))
                if r["missed_block"]:
                    wrong["block:놓침"][x["id"]].append(str(inp.get("category")))
                if r["category_scored"] and not r["category_correct"] and r["input_correct"]:
                    wrong["category:범주만 틀림"][x["id"]].append(str(inp.get("category")))
                if r["has_output"] and r["valid"]:
                    out = resp.get("output") or {}
                    if not r["output_correct"]:
                        wrong["output:근거 여부"][x["id"]].append(f"grounded={out.get('grounded')}")
                    elif r["span_f1"] is not None and r["span_f1"] < 0.6:
                        wrong["output:구간"][x["id"]].append(f"f1={r['span_f1']:.2f} {out.get('unsupported')}")
            else:
                if not r["action_correct"]:
                    how = f"{resp.get('action')}" + (f"({dbg.get('category')}/{dbg.get('first_pass')})" if dbg else "")
                    wrong[f"action:{lab['action']}"][x["id"]].append(how)
                elif r["citation_p"] is not None and r["citation_p"] + r["citation_r"] < 2:
                    wrong["cite:인용"][x["id"]].append(f"{resp.get('citations')} 검색={dbg.get('hits', [])[:3]}")

    n_runs = len(files)
    print(f"실행 {n_runs}개: {', '.join(f.name for f in files)}\n")
    for kind in sorted(wrong):
        if a.only and not kind.startswith(a.only + ":"):
            continue
        items = {i: v for i, v in wrong[kind].items() if len(v) >= a.min}
        tune = {i: v for i, v in items.items() if not rows_by_id[i]["holdout"]}
        hold = {i: v for i, v in items.items() if rows_by_id[i]["holdout"]}
        per_run = sum(len(v) for v in items.values()) / n_runs
        print(f"== {kind}: 문항 {len(items)}개 (회당 {per_run:.1f}개) — tune {len(tune)}, holdout {len(hold)}"
              f" (holdout 은 횟수만: {sorted(Counter(len(v) for v in hold.values()).items())})")
        for i, v in sorted(tune.items(), key=lambda kv: -len(kv[1])):
            p = public[i]
            lab = p["labels"]
            users = [m["content"] for m in p["messages"] if m["role"] == "user"]
            gold = lab.get("category") if p["endpoint"] == "guard" else f"{lab.get('action')}/{lab.get('category')}"
            print(f"  [{len(v)}/{n_runs}] {i} 정답={gold} 예측={dict(Counter(v))} 턴={len(users)}")
            print(f"      마지막 발화: {short(users[-1], a.chars)}")
            if p["endpoint"] == "guard" and kind.startswith("output") and p.get("candidate_answer"):
                print(f"      후보 답변: {short(p['candidate_answer'], a.chars)}")
                print(f"      정답 구간: {lab.get('unsupported')}")
        print()


if __name__ == "__main__":
    main()
