#!/usr/bin/env python3
"""Full-screen, numeric-only monitor for local experiment runs."""
from __future__ import annotations

import argparse
import curses
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

DEFAULT_STATUS_FILE = "/tmp/kosa-experiment-status.json"
SCORE_KEYS = ("total", "tutor", "guard", "correct", "partial", "wrong", "invalid", "judge_errors")


def read_status(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def write_status(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def latest_score(path: Path) -> dict[str, Any]:
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"score file cannot be read: {error}") from error
    if not rows or not isinstance(rows[-1], dict):
        raise ValueError("score file has no completed run")
    row = rows[-1]
    result: dict[str, Any] = {}
    for key in SCORE_KEYS:
        if key not in row or not isinstance(row[key], (int, float)):
            raise ValueError(f"score row lacks numeric {key}")
        result[key] = row[key]
    return result


def load_result(path: Path) -> dict[str, Any]:
    value = read_status(path)
    allowed = {"requests", "elapsed_seconds", "p50_seconds", "p95_seconds", "max_seconds", "errors", "passed"}
    if not value or not allowed.issuperset(value):
        raise ValueError("load result has unexpected fields")
    return {key: value[key] for key in allowed if key in value}


def update(args: argparse.Namespace) -> int:
    path = Path(args.status_file)
    if args.reset:
        value: dict[str, Any] = {
            "version": 1,
            "experiment_id": args.experiment_id,
            "scope": args.scope,
            "runs_expected": args.runs_expected,
            "phase": "preparing",
            "server": "stopped",
            "series": {},
            "load_test": {"state": "pending"},
        }
    else:
        value = read_status(path)
        if not value:
            value = {"version": 1, "series": {}, "load_test": {"state": "pending"}}
    if args.phase:
        value["phase"] = args.phase
    if args.server:
        value["server"] = args.server
    if args.active_series:
        value["active_series"] = args.active_series
    if args.current_run is not None:
        value["current_run"] = args.current_run
    if args.finished:
        value["finished"] = args.finished
    if args.load_state:
        load = value.setdefault("load_test", {})
        load["state"] = args.load_state
        if args.load_file:
            load.update(load_result(Path(args.load_file)))
    if args.score_file:
        if not args.active_series or args.current_run is None:
            raise ValueError("--score-file requires --active-series and --current-run")
        series = value.setdefault("series", {}).setdefault(args.active_series, {"runs": []})
        runs = [entry for entry in series.get("runs", []) if entry.get("run") != args.current_run]
        runs.append({"run": args.current_run, **latest_score(Path(args.score_file))})
        series["runs"] = sorted(runs, key=lambda entry: entry["run"])
    value["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    write_status(path, value)
    return 0


def clip(text: str, width: int) -> str:
    return text[: max(0, width - 1)]


def draw_line(window: curses.window, row: int, text: str, width: int, attr: int = 0) -> None:
    try:
        window.addnstr(row, 0, clip(text, width), max(0, width - 1), attr)
    except curses.error:
        pass


def box_border(window: curses.window, row: int, width: int, label: str = "") -> None:
    outer = max(4, width - 1)
    inner = outer - 2
    prefix = f"__ {label} " if label else ""
    line = f"+{prefix}{'_' * max(0, inner - len(prefix))}+"
    draw_line(window, row, line, width, curses.A_BOLD)


def box_content(window: curses.window, row: int, width: int, text: str, attr: int = 0) -> None:
    outer = max(4, width - 1)
    inner = outer - 2
    line = f"|{clip(text, inner).ljust(inner)}|"
    draw_line(window, row, line, width, attr)


def split_content(window: curses.window, row: int, width: int, left_text: str, right_text: str) -> None:
    outer = max(4, width - 1)
    inner = outer - 2
    left_width = max(1, (inner - 1) // 2)
    right_width = max(1, inner - left_width - 1)
    line = f"|{clip(left_text, left_width).ljust(left_width)}|{clip(right_text, right_width).ljust(right_width)}|"
    draw_line(window, row, line, width)


def render(window: curses.window, status_path: Path) -> None:
    curses.curs_set(0)
    window.nodelay(True)
    selected_series: str | None = None
    while True:
        status = read_status(status_path)
        height, width = window.getmaxyx()
        window.erase()
        title = " KOSA local experiment monitor  |  c: candidate  h: champion  q: close "
        draw_line(window, 0, title, width, curses.A_REVERSE)
        if height < 24 or width < 80:
            draw_line(window, 2, "Resize terminal to at least 80 columns x 24 rows for the AFL-style dashboard.", width, curses.A_BOLD)
            draw_line(window, 4, f"Status file: {status_path}", width)
        elif not status:
            box_border(window, 2, width, "WAITING")
            box_content(window, 3, width, "Waiting for an experiment status file...")
            box_content(window, 4, width, str(status_path))
            box_border(window, 5, width)
        else:
            experiment_id = status.get("experiment_id", "-")
            scope = status.get("scope", "-")
            phase = status.get("phase", "-")
            server = status.get("server", "-")
            updated = status.get("updated_at", "-")
            load = status.get("load_test", {})
            all_series = status.get("series", {})
            default_series = status.get("active_series", "candidate")
            active = selected_series if selected_series in all_series else default_series
            expected = int(status.get("runs_expected", 10))
            current = status.get("current_run") if active == default_series else None
            runs = all_series.get(active, {}).get("runs", [])
            completed = {entry.get("run"): entry for entry in runs}
            cumulative_correct = sum(entry.get("correct", 0) for entry in runs)
            cumulative_partial = sum(entry.get("partial", 0) for entry in runs)
            cumulative_wrong = sum(entry.get("wrong", 0) for entry in runs)
            cumulative_invalid = sum(entry.get("invalid", 0) for entry in runs)

            box_border(window, 2, width, "EXPERIMENT STATUS")
            box_content(window, 3, width, f"experiment: {experiment_id}    scope: {scope}    phase: {phase}", curses.A_BOLD)
            box_content(window, 4, width, f"server: {server}    updated: {updated}")
            box_border(window, 5, width)
            load_info = f"64 concurrency: {load.get('state', 'pending')}"
            if "requests" in load:
                load_info += f"   requests: {load.get('requests')}   errors: {load.get('errors', '-')}"
            cumulative_info = f"runs: {len(completed)}/{expected} ok:{cumulative_correct} pt:{cumulative_partial} bad:{cumulative_wrong} fmt:{cumulative_invalid}"
            box_border(window, 6, width, "LIVE TOTALS")
            split_content(window, 7, width, load_info, cumulative_info)
            box_border(window, 8, width)
            box_border(window, 9, width, f"{active.upper()} RUNS")
            box_content(window, 10, width, " # | stage     | ok | pt | bad | fmt | total | tutor | guard | state", curses.A_UNDERLINE)
            for index in range(1, min(expected, 10) + 1):
                entry = completed.get(index)
                if entry:
                    text = (f"{index:>2} | {active:<9.9} | {entry['correct']:>2} | {entry['partial']:>2} | {entry['wrong']:>3} |"
                            f" {entry['invalid']:>3} | {entry['total']:>5.1f} | {entry['tutor']:>5.1f} | {entry['guard']:>5.1f} | done")
                elif current == index and phase.endswith("scoring"):
                    text = f"{index:>2} | {active:<9.9} |  - |  - |   - |   - |     - |     - |     - | running"
                else:
                    text = f"{index:>2} | {active:<9.9} |  - |  - |   - |   - |     - |     - |     - | pending"
                box_content(window, 10 + index, width, text)
            box_border(window, 21, width)
            box_content(window, 22, width, "ok=100 points; bad=0; pt=partial. Numeric only. c=can h=champ q=quit")
            box_border(window, 23, width)
        window.refresh()
        key = window.getch()
        if key in (ord("q"), ord("Q"), 3):
            return
        if key in (ord("c"), ord("C")):
            selected_series = "candidate"
        elif key in (ord("h"), ord("H")):
            selected_series = "champion"
        time.sleep(0.25)
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command")
    update_parser = subparsers.add_parser("update", help="write numeric progress state")
    update_parser.add_argument("--status-file", default=DEFAULT_STATUS_FILE)
    update_parser.add_argument("--reset", action="store_true")
    update_parser.add_argument("--experiment-id")
    update_parser.add_argument("--scope")
    update_parser.add_argument("--runs-expected", type=int)
    update_parser.add_argument("--phase")
    update_parser.add_argument("--server")
    update_parser.add_argument("--active-series")
    update_parser.add_argument("--current-run", type=int)
    update_parser.add_argument("--load-state")
    update_parser.add_argument("--load-file")
    update_parser.add_argument("--score-file")
    update_parser.add_argument("--finished")
    parser.add_argument("--status-file", default=DEFAULT_STATUS_FILE, help="status JSON to watch")
    args = parser.parse_args()
    try:
        if args.command == "update":
            if args.reset and (not args.experiment_id or not args.scope or not args.runs_expected):
                raise ValueError("--reset requires id, scope, and expected runs")
            return update(args)
        curses.wrapper(render, Path(args.status_file))
        return 0
    except KeyboardInterrupt:
        # curses.wrapper가 화면을 원복한 뒤 여기로 온다. Ctrl+C는 오류가 아닌 종료 요청이다.
        return 0
    except (OSError, ValueError, TypeError) as error:
        print(f"[experiment-dashboard] {type(error).__name__}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
