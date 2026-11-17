#!/usr/bin/env python3
"""답 끝에 덧붙인 자료 문장(TUTOR_APPEND_QUOTE, TUTOR_APPEND_CONTEXT)의 판정 효과를, 같은 답에서 붙인 것과 뗀 것으로 짝지어 잰다. 런박스에서 실행한다.

    python3 loop/exp_run.py --name quote-append --part tutor --env TUTOR_APPEND_QUOTE=1 --env EXP_DEBUG=1
    python3 loop/quote_judge_eval.py quote-append --repeat 2                 # 근거 문장을 붙인 답 / 뗀 답
    python3 loop/quote_judge_eval.py context3 --note context --repeat 2      # 관련 문장까지 붙인 답 / 근거 문장만 남긴 답

- 서버는 띄우지 않는다. 저장된 실행(baseline/results/<이름>_run*.jsonl)의 튜터 답 가운데 근거 문장이 붙은 것을 골라,
  붙은 답과 그 문장을 뗀 답을 채점기의 판정(grader/judge.py 의 facts·faithful)에 그대로 넣는다. 근거 자료는 채점기와 같이 인용한 섹션의 글이다.
- 답을 새로 만들지 않으므로 생성의 흔들림이 섞이지 않는다. 남는 차이는 붙인 문장의 효과와 판정 모델의 흔들림이다(--repeat 로 줄인다).
- tune(70%)과 holdout(30%)을 따로 보고하고 holdout 은 숫자만 낸다.
"""
import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "loop"))
sys.path.insert(0, str(ROOT / "grader"))
sys.path.insert(0, str(ROOT / "harness"))
from exp_run import load_env_files, pick  # noqa: E402
from judge import make_judge  # noqa: E402
import score_local as sl  # noqa: E402

sys.path.insert(0, str(ROOT / "baseline"))
from app.tutor import prompts  # noqa: E402

MARKS = {
    "quote": " " + prompts.QUOTE_NOTE.split("{quote}")[0],  # ' 자료에는 이렇게 적혀 있어요: "'
    "context": " " + prompts.CONTEXT_NOTE.split("{sentences}")[0],  # ' 함께 보면 좋은 자료 문장: '
}
MARK = MARKS["quote"]


def strip_note(answer: str) -> str | None:
    """덧붙인 문장(MARK 부터 끝까지)을 뗀 답. 개인정보 안내 문장은 남긴다. 붙은 문장이 없으면 None."""
    i = answer.find(MARK)
    if i < 0:
        return None
    tail = " " + prompts.PII_NOTE if answer.endswith(prompts.PII_NOTE) else ""
    return answer[:i].rstrip() + tail


def short(text: str, n: int = 150) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="+", help="실행 이름(앞부분) 또는 결과 파일 경로")
    ap.add_argument("--repeat", type=int, default=2)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--show", type=int, default=8, help="tune 에서 판정이 달라진 문항을 몇 개까지 보여 줄지")
    ap.add_argument("--note", choices=sorted(MARKS), default="quote", help="어느 덧붙임을 떼어 견줄지")
    a = ap.parse_args()
    global MARK
    MARK = MARKS[a.note]

    files = []
    for n in a.names:
        p = Path(n)
        files += [p] if p.exists() else sorted((ROOT / "baseline" / "results").glob(n + "*_run*.jsonl"))
    if not files:
        sys.exit("결과 파일이 없습니다")
    public = {}
    for line in (pick("public") / "tutor.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            d = json.loads(line)
            public[d["id"]] = d
    kb = sl.load_kb_dir(pick("kb"))
    env = {**os.environ, **load_env_files()}
    judge = make_judge(env["OPENAI_BASE_URL"], env["OPENAI_MODEL"], env["OPENAI_API_KEY"])

    rows, answered = [], 0
    for f in files:
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            x = json.loads(line)
            resp = x.get("resp") or {}
            if x["endpoint"] != "tutor" or resp.get("action") != "answer" or not isinstance(resp.get("answer"), str):
                continue
            answered += 1
            base = strip_note(resp["answer"])
            if base is None:
                continue
            lab = public[x["id"]]["labels"]
            texts = [kb[s] for s in dict.fromkeys(resp.get("citations") or []) if s in kb]
            rows.append({"id": x["id"], "holdout": x["holdout"], "gold_action": lab["action"], "gold_facts": lab.get("gold_facts") or [],
                         "full": resp["answer"], "base": base, "texts": texts})
    n_calls = sum((2 if r["gold_facts"] else 0) + 2 for r in rows) * a.repeat
    print(f"실행 {len(files)}개: answer 로 답한 {answered}건 중 덧붙인 문장({a.note})이 있는 답 {len(rows)}건 ({len(rows) / max(answered, 1):.0%}), 판정 호출 {n_calls}개")

    jobs = []
    for i, r in enumerate(rows):
        for ver in ("base", "full"):
            for _ in range(a.repeat):
                if r["gold_facts"]:
                    jobs.append((i, ver, "facts"))
                jobs.append((i, ver, "faithful"))

    def one(job):
        i, ver, kind = job
        r = rows[i]
        try:
            if kind == "facts":
                return float(judge("facts", {"gold_facts": r["gold_facts"], "answer": r[ver], "evidence": r["texts"]})["score"])
            return float(bool(judge("faithful", {"answer": r[ver], "evidence": r["texts"]})["ok"]))
        except Exception:
            return None

    with ThreadPoolExecutor(a.workers) as ex:
        outs = list(ex.map(one, jobs))
    vals: dict[tuple[int, str, str], list[float]] = {}
    for job, o in zip(jobs, outs):
        if o is not None:
            vals.setdefault(job, []).append(o)
    print(f"판정 실패 {sum(o is None for o in outs)}건")

    def avg(i, ver, kind):
        v = vals.get((i, ver, kind))
        return sum(v) / len(v) if v else None

    for split in ("tune", "holdout"):
        idx = [i for i, r in enumerate(rows) if r["holdout"] == (split == "holdout")]
        print(f"\n[{split}] 근거 문장이 붙은 답 {len(idx)}건 (정답 answer {sum(rows[i]['gold_action'] == 'answer' for i in idx)}, 그 밖 {sum(rows[i]['gold_action'] != 'answer' for i in idx)})")
        for kind, label in (("facts", "정답 사실"), ("faithful", "근거 충실")):
            pairs = [(avg(i, "base", kind), avg(i, "full", kind)) for i in idx]
            pairs = [(b, f) for b, f in pairs if b is not None and f is not None]
            if not pairs:
                continue
            b = sum(p[0] for p in pairs) / len(pairs)
            f = sum(p[1] for p in pairs) / len(pairs)
            up = sum(f_ > b_ for b_, f_ in pairs)
            down = sum(f_ < b_ for b_, f_ in pairs)
            print(f"   {label}: 떼면 {b:.3f} → 붙이면 {f:.3f} (차이 {f - b:+.3f}, 문항 {len(pairs)}건 중 오른 것 {up}, 내린 것 {down})")
        if split == "tune" and a.show:
            changed = []
            for i in idx:
                d = [(avg(i, "full", k) or 0) - (avg(i, "base", k) or 0) for k in ("facts", "faithful")]
                if any(abs(v) >= 0.5 for v in d):
                    changed.append((i, d))
            for i, d in changed[: a.show]:
                r = rows[i]
                print(f"   - 정답 사실 {d[0]:+.2f} 근거 충실 {d[1]:+.2f} :: 정답 사실={r['gold_facts']}")
                print(f"       답: {short(r['full'], 260)}")


if __name__ == "__main__":
    main()
