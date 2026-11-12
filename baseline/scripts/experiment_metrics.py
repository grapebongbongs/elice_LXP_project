#!/usr/bin/env python3
"""Experiment runner metrics helper. It only handles aggregate numeric results."""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path
from typing import Any

SCORE_RE = re.compile(
    r"총점\s+([0-9.]+)\s+\(튜터\s+([0-9.]+),\s+가드\s+([0-9.]+)\),\s+"
    r"형식 오류\s+(\d+),\s+판정 오류\s+(\d+)"
)
LOAD_RE = re.compile(
    r"(\d+)\s+reqs in\s+([0-9.]+)s\s+p50=([0-9.]+)s\s+p95=([0-9.]+)s\s+max=([0-9.]+)s"
)
ITEM_POINTS_RE = re.compile(r"^[^\t]+\t(?:tutor|guard)\t[^\t]+\t([0-9.]+)(?:\t형식 오류)?$", re.MULTILINE)
FORBIDDEN = {"answer", "candidate_answer", "content", "messages", "request", "response", "unsupported"}


def read_json(path: str) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object expected: {path}")
    return value


def write_json(path: str, value: dict[str, Any]) -> None:
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def summary(value: dict[str, Any]) -> dict[str, Any]:
    value = value.get("candidate_summary", value)
    if not {"total", "tutor", "guard"}.issubset(value):
        raise ValueError("total/tutor/guard summary required")
    return value


def stat(values: list[float]) -> dict[str, Any]:
    return {
        "values": values,
        "median": round(statistics.median(values), 4),
        "mean": round(statistics.fmean(values), 4),
        "min": round(min(values), 4),
        "max": round(max(values), 4),
    }


def parse_score(args: argparse.Namespace) -> None:
    text = Path(args.input).read_text(encoding="utf-8", errors="replace")
    matches = SCORE_RE.findall(text)
    if not matches:
        raise ValueError("score_local.py aggregate line was not found")
    total, tutor, guard, invalid, judge_errors = matches[-1]
    points = [float(value) for value in ITEM_POINTS_RE.findall(text)]
    correct = sum(point >= 99.95 for point in points)
    wrong = sum(point <= 0.05 for point in points)
    partial = len(points) - correct - wrong
    row = {"total": float(total), "tutor": float(tutor), "guard": float(guard),
           "invalid": int(invalid), "judge_errors": int(judge_errors),
           "correct": correct, "partial": partial, "wrong": wrong}
    with Path(args.output).open("a", encoding="utf-8") as output:
        output.write(json.dumps(row, ensure_ascii=False) + "\n")


def parse_load(args: argparse.Namespace) -> None:
    text = Path(args.input).read_text(encoding="utf-8", errors="replace")
    matches = LOAD_RE.findall(text)
    errors = re.findall(r"errors:\s+(\d+)", text)
    if not matches or not errors:
        raise ValueError("load_test.py aggregate line was not found")
    count, elapsed, p50, p95, maximum = matches[-1]
    error_count = int(errors[-1])
    write_json(args.output, {
        "requests": int(count), "elapsed_seconds": float(elapsed),
        "p50_seconds": float(p50), "p95_seconds": float(p95),
        "max_seconds": float(maximum), "errors": error_count,
        "passed": error_count == 0,
    })


def summarize_runs(args: argparse.Namespace) -> None:
    rows = [json.loads(line) for line in Path(args.runs).read_text(encoding="utf-8").splitlines() if line]
    if len(rows) != args.expected_runs:
        raise ValueError(f"expected {args.expected_runs} runs, got {len(rows)}")
    if any(row["invalid"] or row["judge_errors"] for row in rows):
        raise ValueError("invalid responses or judge failures exist")
    write_json(args.output, {
        "runs": len(rows),
        "total": stat([row["total"] for row in rows]),
        "tutor": stat([row["tutor"] for row in rows]),
        "guard": stat([row["guard"] for row in rows]),
        "invalid_total": 0,
        "judge_errors_total": 0,
    })


def precheck(args: argparse.Namespace) -> None:
    candidate, champion = summary(read_json(args.candidate)), summary(read_json(args.champion))
    candidate_median = candidate["total"]["median"]
    champion_median = champion["total"]["median"]
    write_json(args.output, {
        "candidate_total_median": candidate_median,
        "historical_champion_total_median": champion_median,
        "potential_margin": args.potential_margin,
        "reevaluate_champion": candidate_median >= champion_median - args.potential_margin,
    })


def screen(args: argparse.Namespace) -> None:
    """Decide whether a short candidate screen warrants the remaining runs."""
    candidate, baseline = summary(read_json(args.candidate)), summary(read_json(args.baseline))
    candidate_median = candidate["total"]["median"]
    baseline_median = baseline["total"]["median"]
    continue_full_runs = candidate_median >= baseline_median - args.early_stop_margin
    write_json(args.output, {
        "candidate_total_median": candidate_median,
        "screen_baseline_total_median": baseline_median,
        "early_stop_margin": args.early_stop_margin,
        "continue_full_runs": continue_full_runs,
    })


def decide(args: argparse.Namespace) -> None:
    candidate, champion = summary(read_json(args.candidate)), summary(read_json(args.champion))
    differences = {key: round(candidate[key]["median"] - champion[key]["median"], 4)
                   for key in ("total", "tutor", "guard")}
    guard_only_threshold = round(args.delta * args.guard_review_multiplier, 4)
    reasons: list[str] = []
    if differences["total"] >= args.delta and differences["guard"] < 0:
        reasons.append("total_gain_with_guard_regression")
    if differences["guard"] >= guard_only_threshold and differences["tutor"] <= 0:
        reasons.append("large_guard_only_gain")
    decision = "promote_candidate" if differences["total"] >= args.delta and not reasons else "review_required" if reasons else "keep_champion"
    write_json(args.output, {
        "decision": decision,
        "delta": args.delta,
        "guard_review_threshold": guard_only_threshold,
        "candidate_minus_champion_median": differences,
        "reasons": reasons,
    })


def report(args: argparse.Namespace) -> None:
    value: dict[str, Any] = {
        "experiment_id": args.experiment_id,
        "base_commit": args.base_commit,
        "candidate_commit": args.candidate_commit,
        "scope": args.scope,
        "hypothesis": args.hypothesis,
        "load_test": read_json(args.load_test),
        "candidate_summary": summary(read_json(args.candidate)),
        "decision": read_json(args.decision),
    }
    if args.champion:
        value["champion_summary"] = summary(read_json(args.champion))
    write_json(args.output, value)


def validate_report(args: argparse.Namespace) -> None:
    def walk(value: Any) -> None:
        if isinstance(value, dict):
            bad = FORBIDDEN & set(value)
            if bad:
                raise ValueError(f"forbidden report keys: {sorted(bad)}")
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(read_json(args.report))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    p = subs.add_parser("parse-score"); p.add_argument("--input", required=True); p.add_argument("--output", required=True); p.set_defaults(func=parse_score)
    p = subs.add_parser("parse-load"); p.add_argument("--input", required=True); p.add_argument("--output", required=True); p.set_defaults(func=parse_load)
    p = subs.add_parser("summarize"); p.add_argument("--runs", required=True); p.add_argument("--output", required=True); p.add_argument("--expected-runs", type=int, default=10); p.set_defaults(func=summarize_runs)
    p = subs.add_parser("precheck"); p.add_argument("--candidate", required=True); p.add_argument("--champion", required=True); p.add_argument("--potential-margin", type=float, default=3.0); p.add_argument("--output", required=True); p.set_defaults(func=precheck)
    p = subs.add_parser("screen"); p.add_argument("--candidate", required=True); p.add_argument("--baseline", required=True); p.add_argument("--early-stop-margin", type=float, default=2.0); p.add_argument("--output", required=True); p.set_defaults(func=screen)
    p = subs.add_parser("decide"); p.add_argument("--candidate", required=True); p.add_argument("--champion", required=True); p.add_argument("--delta", type=float, default=3.0); p.add_argument("--guard-review-multiplier", type=float, default=1.4); p.add_argument("--output", required=True); p.set_defaults(func=decide)
    p = subs.add_parser("report"); p.add_argument("--experiment-id", required=True); p.add_argument("--base-commit", required=True); p.add_argument("--candidate-commit", required=True); p.add_argument("--scope", choices=("tutor", "guard", "common"), required=True); p.add_argument("--hypothesis", required=True); p.add_argument("--load-test", required=True); p.add_argument("--candidate", required=True); p.add_argument("--champion"); p.add_argument("--decision", required=True); p.add_argument("--output", required=True); p.set_defaults(func=report)
    p = subs.add_parser("validate-report"); p.add_argument("--report", required=True); p.set_defaults(func=validate_report)
    args = parser.parse_args()
    try:
        args.func(args)
    except (OSError, ValueError, json.JSONDecodeError, statistics.StatisticsError) as error:
        print(f"[experiment-metrics] {type(error).__name__}: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
