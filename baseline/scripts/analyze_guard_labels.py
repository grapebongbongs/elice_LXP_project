"""public/guard.jsonl 의 범주 분포와 범주×결정 교차표 → app/guard/policy.py 정책표 근거.

    python scripts/analyze_guard_labels.py [--data path] [--examples 3]
"""
import argparse
import json
import os
from collections import Counter, defaultdict
from pathlib import Path

from eval_public import find, split_item

ap = argparse.ArgumentParser()
ap.add_argument("--data", default=str(Path(os.getenv("COMPETITION_DIR", "/home/elicer/competition")) / "public" / "guard.jsonl"))
ap.add_argument("--examples", type=int, default=3)
args = ap.parse_args()

items = [json.loads(l) for l in Path(args.data).read_text(encoding="utf-8").splitlines() if l.strip()]
print(f"{len(items)} items. first keys: {list(items[0].keys())}")

cross = Counter()
examples = defaultdict(list)
has_cand = Counter()
for i, it in enumerate(items):
    req, exp = split_item(it)
    cat = find(exp, "input.category", "category", "input_category")
    dec = find(exp, "input.decision", "decision", "input_decision")
    cross[(cat, dec)] += 1
    has_cand[req.get("candidate_answer") is not None] += 1
    if len(examples[cat]) < args.examples:
        last = next((m["content"] for m in reversed(req.get("messages", [])) if m.get("role") == "user"), "")
        examples[cat].append((i + 1, dec, last[:120].replace("\n", " ")))

print(f"\ncandidate_answer 있음: {has_cand[True]}, 없음: {has_cand[False]}")
print(f"\n{'category':<22}{'allow':>7}{'block':>7}  → 제안")
for cat in sorted({c for c, _ in cross}, key=str):
    a, b = cross[(cat, "allow")], cross[(cat, "block")]
    sug = "block" if b > a else "allow"
    mixed = " (혼재: USE_LLM_DECISION 검토)" if a and b else ""
    print(f"{str(cat):<22}{a:>7}{b:>7}  → {sug}{mixed}")

print("\n대표 예시 (행 번호, 결정, 마지막 발화):")
for cat, exs in examples.items():
    print(f"\n[{cat}]")
    for ln, dec, txt in exs:
        print(f"  #{ln} {dec}: {txt}")
