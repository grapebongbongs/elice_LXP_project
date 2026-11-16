#!/usr/bin/env python3
"""실험 한 번: 설정을 바꿔 서버를 따로 띄우고 → 공개 세트를 공식 채점기로 채점 → 장부에 기록.

    python3 loop/exp_run.py --name fewshot-k4 --env FEWSHOT_K=4
    python3 loop/exp_run.py --name base --repeat 2          # 판정 모델 흔들림 확인
    python3 loop/exp_run.py --name fmt --mock               # LLM 없이 형식·속도만

- 제출용 서버(보통 8000)는 건드리지 않고 --port(기본 8011)에 실험 서버를 따로 띄웠다가 끈다.
- 채점은 grader/scoring.py 를 그대로 쓴다(공식 로컬 채점기와 같은 계산).
- holdout(문항 id 해시 30%)은 프롬프트 예시를 고를 때 보지 않는 문항이다. 채택 판단은 holdout 을 함께 본다.
- 표준 라이브러리만 쓴다. 기록은 experiments/ledger.jsonl (점수·설정만),
  문항별 결과는 baseline/results/ (git 제외, 공개 세트만).
"""
import argparse
import hashlib
import json
import os
import secrets
import statistics
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "grader"))
sys.path.insert(0, str(ROOT / "harness"))
import scoring  # noqa: E402
import score_local as sl  # noqa: E402
from judge import make_judge  # noqa: E402

LEDGER = Path(os.getenv("EXP_LEDGER") or ROOT / "experiments" / "ledger.jsonl")
COMP = Path("/home/elicer/competition")


def pick(name: str) -> Path:
    for base in (ROOT, COMP):
        if (base / name).exists():
            return base / name
    sys.exit(f"{name} 폴더를 찾지 못했습니다")


def load_env_files() -> dict:
    env = {}
    for f in (COMP / ".env", ROOT / ".env", ROOT / "baseline" / ".env"):
        if not f.exists():
            continue
        for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def git(*args) -> str:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip()
    except Exception:
        return ""


def is_holdout(item_id: str) -> bool:
    return int(hashlib.sha1(item_id.encode()).hexdigest(), 16) % 10 < 3


def mean(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 4) if xs else None


def evaluate(public, call, judge, kb, workers):
    def one(p):
        item, v = sl.as_scoring_input(p)
        payload = {"spec_version": "1.0", "session_id": "s_" + secrets.token_hex(8), "messages": v["messages"]}
        if v["candidate_answer"]:
            payload["candidate_answer"] = v["candidate_answer"]
        t0 = time.perf_counter()
        try:
            resp = call(p["endpoint"], payload)
        except Exception:
            resp = None
        ms = (time.perf_counter() - t0) * 1000
        if p["endpoint"] == "tutor":
            r = scoring.score_tutor(item, v, resp, judge or sl._no_judge, kb)
            if judge is None:
                r["judge_error"] = False
            pts = scoring.tutor_points(r)
        else:
            r = scoring.score_guard(item, v, resp)
            pts = scoring.guard_points(r)
        return {"p": p, "r": r, "pts": pts, "ms": ms, "resp": resp}

    with ThreadPoolExecutor(workers) as ex:
        return list(ex.map(one, public))


def _f1(p, r):
    return 2 * p * r / (p + r) if p + r else 0.0


def summarize(rows):
    tutor = [x["r"] for x in rows if x["p"]["endpoint"] == "tutor"]
    guard = [x["r"] for x in rows if x["p"]["endpoint"] == "guard"]
    agg = scoring.aggregate(tutor, guard, sl.WEIGHTS)
    outs = [r for r in guard if r["has_output"] and r["valid"]]
    cats = [r["category_correct"] for r in guard if r["category_scored"]]
    agg["detail"] = {
        # 판정 모델과 무관한 튜터 점수: action 일치와 인용 F1(해당 문항만)의 문항 평균. 실행 간 흔들림이 작다.
        "tutor_rule_score": mean([
            (float(r["action_correct"]) + _f1(r["citation_p"], r["citation_r"])) / 2 if r["citation_p"] is not None else float(r["action_correct"])
            for r in tutor
        ]),
        "tutor_action_acc": mean([float(r["action_correct"]) for r in tutor]),
        "tutor_citation_f1": mean([_f1(r["citation_p"], r["citation_r"]) for r in tutor if r["citation_p"] is not None]),
        "tutor_facts": mean([r["facts"] for r in tutor]),
        "tutor_faithful": mean([None if r["faithful"] is None else float(r["faithful"]) for r in tutor]),
        "tutor_complied_zero": sum(bool(r["complied"]) for r in tutor),
        "tutor_over_refusal": sum(r["over_refusal"] for r in tutor),
        "guard_block_f1": round(scoring.block_f1(guard), 4) if guard else None,
        "guard_false_block": sum(r["false_block"] for r in guard),
        "guard_missed_block": sum(r["missed_block"] for r in guard),
        "guard_category_acc": mean([float(c) for c in cats]),
        "guard_grounded_acc": mean([float(r["output_correct"]) for r in outs]),
        "guard_span_f1": mean([r["span_f1"] for r in outs]),
        "guard_safe_acc": mean([float(r["safe_correct"]) for r in outs]),
    }
    return agg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True, help="실험 이름 (장부에 남는다)")
    ap.add_argument("--env", action="append", default=[], help="KEY=VALUE 설정 덮어쓰기 (여러 번 가능)")
    ap.add_argument("--port", type=int, default=8011)
    ap.add_argument("--workers", type=int, default=16, help="동시 요청 수 (과한 호출 금지: 16 권장)")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--part", choices=["all", "tutor", "guard"], default="all")
    ap.add_argument("--no-judge", action="store_true", help="판정 모델 없이 action·인용·가드만")
    ap.add_argument("--mock", action="store_true", help="LLM_MOCK=1 (형식·속도 점검, 장부에 남기지 않음)")
    ap.add_argument("--note", default="")
    a = ap.parse_args()
    if a.port == 8080:
        sys.exit("8080 은 VSCode 가 씁니다")

    overrides = dict(e.split("=", 1) for e in a.env)
    env = {**os.environ, **load_env_files(), **overrides}
    if not (env.get("SERVER_TOKEN") or env.get("TEAM_TOKEN")):
        env["SERVER_TOKEN"] = "exp-local-token"
    env["PYTHONIOENCODING"] = "utf-8"
    if a.mock:
        env["LLM_MOCK"] = "1"
    token = env.get("SERVER_TOKEN") or env["TEAM_TOKEN"]

    public_dir, kb_dir = pick("public"), pick("kb")
    parts = ("tutor", "guard") if a.part == "all" else (a.part,)
    public = [p for e in parts for p in sl._read_jsonl(public_dir / f"{e}.jsonl")]
    kb = sl.load_kb_dir(kb_dir)
    judge = None
    if not a.no_judge and not a.mock and all(env.get(k) for k in ("OPENAI_BASE_URL", "OPENAI_MODEL", "OPENAI_API_KEY")):
        judge = make_judge(env["OPENAI_BASE_URL"], env["OPENAI_MODEL"], env["OPENAI_API_KEY"])

    log = open(ROOT / "loop" / f".server_{a.port}.log", "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(a.port), "--no-access-log"],
        cwd=ROOT / "baseline", env=env, stdout=log, stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{a.port}"
    runs = []
    try:
        for _ in range(120):
            try:
                urllib.request.urlopen(base + "/health", timeout=2).read()
                break
            except Exception:
                if proc.poll() is not None:
                    sys.exit(f"서버가 뜨지 않았습니다. loop/.server_{a.port}.log 를 확인하세요")
                time.sleep(0.5)
        else:
            sys.exit("서버 health 응답 없음")

        def call(endpoint, payload):
            req = urllib.request.Request(
                f"{base}/{endpoint}", data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), method="POST",
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read(256 * 1024 + 1))

        for i in range(a.repeat):
            t0 = time.time()
            rows = evaluate(public, call, judge, kb, a.workers)
            ms = sorted(x["ms"] for x in rows)
            res = {
                "all": summarize(rows),
                "holdout": summarize([x for x in rows if is_holdout(x["p"]["id"])]),
                "latency_ms": {"p50": round(ms[len(ms) // 2]), "p95": round(ms[int(len(ms) * 0.95)]), "max": round(ms[-1])},
                "no_response": sum(x["resp"] is None for x in rows),
                "seconds": round(time.time() - t0),
            }
            runs.append(res)
            out_dir = ROOT / "baseline" / "results"
            out_dir.mkdir(exist_ok=True)
            lines = [
                json.dumps({"id": x["p"]["id"], "endpoint": x["p"]["endpoint"], "holdout": is_holdout(x["p"]["id"]),
                            "points": round(x["pts"], 3), "labels": x["p"]["labels"], "resp": x["resp"], "r": x["r"]},
                           ensure_ascii=False)
                for x in rows
            ]
            (out_dir / f"{a.name}_run{i + 1}.jsonl").write_text("\n".join(lines), encoding="utf-8")
            A, H = res["all"], res["holdout"]
            print(f"[run {i + 1}/{a.repeat}] 총점 {A['total']} (튜터 {A['tutor']}, 가드 {A['guard']})"
                  f" | holdout 총점 {H['total']} (튜터 {H['tutor']}, 가드 {H['guard']})"
                  f" | 형식 오류 {A['invalid']} 판정 오류 {A['judge_errors']} 무응답 {res['no_response']}"
                  f" | p95 {res['latency_ms']['p95']}ms max {res['latency_ms']['max']}ms | {res['seconds']}s")
            print("   ", json.dumps(A["detail"], ensure_ascii=False))
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except Exception:
            proc.kill()
        log.close()

    def avg(*path):
        vals = []
        for r in runs:
            cur = r
            for k in path:
                cur = cur[k]
            vals.append(cur)
        return round(statistics.mean(vals), 2), (round(statistics.pstdev(vals), 2) if len(vals) > 1 else 0.0)

    entry = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "name": a.name,
        "commit": git("rev-parse", "--short", "HEAD"),
        "dirty": bool(git("status", "--porcelain", "--untracked-files=no")),
        "env": overrides,
        "judge": judge is not None,
        "part": a.part,
        "repeat": a.repeat,
        "total": avg("all", "total")[0], "total_std": avg("all", "total")[1],
        "tutor": avg("all", "tutor")[0], "guard": avg("all", "guard")[0],
        "holdout_total": avg("holdout", "total")[0],
        "holdout_tutor": avg("holdout", "tutor")[0], "holdout_guard": avg("holdout", "guard")[0],
        "invalid": max(r["all"]["invalid"] for r in runs),
        "no_response": max(r["no_response"] for r in runs),
        "p95_ms": max(r["latency_ms"]["p95"] for r in runs), "max_ms": max(r["latency_ms"]["max"] for r in runs),
        "detail": runs[-1]["all"]["detail"],
        "note": a.note,
    }
    prev = []
    if LEDGER.exists():
        prev = [json.loads(l) for l in LEDGER.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not a.mock:
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with open(LEDGER, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"\n== {a.name}: 총점 {entry['total']} ±{entry['total_std']} (튜터 {entry['tutor']}, 가드 {entry['guard']}),"
          f" holdout {entry['holdout_total']}")
    comparable = [e for e in prev if e.get("judge") == entry["judge"] and e.get("part") == a.part]
    if comparable and not a.mock:
        best = max(comparable, key=lambda e: e["total"])
        print(f"   장부 최고: {best['name']} @{best['commit']} 총점 {best['total']} (holdout {best.get('holdout_total')})"
              f" → 차이 {entry['total'] - best['total']:+.2f}")
    if entry["max_ms"] > 20000 or entry["no_response"]:
        print("   [경고] 응답이 느리거나 빠졌습니다. 이 설정은 제출하지 마세요 (요청당 30초, 전체 채점 시간 제한).")


if __name__ == "__main__":
    main()
