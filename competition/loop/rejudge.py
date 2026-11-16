#!/usr/bin/env python3
"""저장된 공개 세트 튜터 답변을 판정 모델 설정만 바꿔 다시 채점한다. 런박스에서 실행한다.

    python3 loop/rejudge.py baseline/results/<이름>_run1.jsonl --effort high

왜: 실제 채점의 판정 모델은 로컬 판정 모델(gpt-5-nano, 기본 설정)과 다르고, 공개 세트 튜터 점수(89)와
실제 튜터 점수(82)가 크게 벌어져 있다. 같은 모델이라도 추론 수준을 높이면 더 엄격하게 볼 수 있으므로,
어느 항목(정답 사실 / 근거 충실 / 응함)에서 판정이 달라지는지 본다. 서버는 다시 돌리지 않는다.

- 판정 프롬프트와 채점식은 grader/ 그대로 쓴다. 바꾸는 것은 reasoning_effort 와 출력 토큰 상한뿐이다.
- 입력 파일은 loop/exp_run.py 가 저장한 공개 세트 결과(답변 포함)이다. 채점 요청과는 무관하다.
- 판정이 달라진 문항은 tune 문항만 내용을 보여 준다(holdout 은 개수만).
"""
import argparse
import json
import os
import sys
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "loop"))
sys.path.insert(0, str(ROOT / "grader"))
sys.path.insert(0, str(ROOT / "harness"))
from exp_run import load_env_files, pick  # noqa: E402
import judge as J  # noqa: E402
import scoring  # noqa: E402
import score_local as sl  # noqa: E402

ENV = {**load_env_files(), **os.environ}


def make_judge(effort: str, max_tokens: int, timeout=120, retries=2):
    url = ENV["OPENAI_BASE_URL"].rstrip("/") + "/chat/completions"

    def judge(kind, payload):
        body = {
            "model": ENV["OPENAI_MODEL"], "stream": True, "max_completion_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": f"{J.SYSTEM}\n{J.PROMPTS[kind]}"},
                         {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        }
        if effort:
            body["reasoning_effort"] = effort
        req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers={
            "Content-Type": "application/json", "Accept": "text/event-stream", "Authorization": f"Bearer {ENV['OPENAI_API_KEY']}"})
        for _ in range(retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    value = json.loads(J._content(resp)).get(J.FIELD[kind])
                if J._valid(kind, value):
                    return {J.FIELD[kind]: value}
            except Exception:
                continue
        raise scoring.JudgeError(kind)

    return judge


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("results")
    ap.add_argument("--effort", default="high")
    ap.add_argument("--max-tokens", type=int, default=8000)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--show", type=int, default=10)
    ap.add_argument("--dump")
    a = ap.parse_args()

    rows = [json.loads(l) for l in Path(a.results).read_text(encoding="utf-8").splitlines() if l.strip()]
    rows = [x for x in rows if x["endpoint"] == "tutor"]
    pub = {d["id"]: d for d in sl._read_jsonl(pick("public") / "tutor.jsonl")}
    kb = sl.load_kb_dir(pick("kb"))
    judge = make_judge(a.effort, a.max_tokens)

    def one(x):
        item, v = sl.as_scoring_input(pub[x["id"]])
        resp = {k: x["resp"][k] for k in ("answer", "action", "citations")} if x["resp"] else None
        return scoring.score_tutor(item, v, resp, judge, kb)

    with ThreadPoolExecutor(a.workers) as ex:
        new = list(ex.map(one, rows))

    def summary(rs):
        agg = scoring.aggregate(rs, [], {"tutor": 1, "guard": 0})
        facts = [r["facts"] for r in rs if r["facts"] is not None]
        faith = [float(r["faithful"]) for r in rs if r["faithful"] is not None]
        return (f"튜터 {agg['tutor']}, 정답 사실 {sum(facts) / len(facts):.3f} (n={len(facts)}), 근거 충실 {sum(faith) / len(faith):.3f} (n={len(faith)}),"
                f" 응함 0점 {sum(bool(r['complied']) for r in rs)}, 판정 오류 {agg['judge_errors']}")

    old = [x["r"] for x in rows]
    print(f"저장된 판정(기본 설정): {summary(old)}")
    print(f"다시 판정(effort={a.effort}): {summary(new)}")
    for split in ("tune", "holdout"):
        idx = [i for i, x in enumerate(rows) if x["holdout"] == (split == "holdout")]
        print(f"  {split}: 기본 {summary([old[i] for i in idx])}")
        print(f"  {split}: 다시 {summary([new[i] for i in idx])}")

    diff = Counter()
    shown = 0
    for x, o, n in zip(rows, old, new):
        for k in ("facts", "faithful", "complied"):
            if o[k] is None or n[k] is None:
                continue
            ov, nv = (o[k] >= 1, n[k] >= 1) if k == "facts" else (bool(o[k]), bool(n[k]))
            if ov != nv:
                diff[(k, f"{ov}→{nv}", x["labels"]["action"], (x["resp"] or {}).get("action"))] += 1
                if not x["holdout"] and shown < a.show and x["resp"]:
                    shown += 1
                    print(f"\n[{k} {ov}→{nv}] 정답 action={x['labels']['action']} 응대={x['resp']['action']} 인용={x['resp']['citations']} 정답 근거={x['labels'].get('evidence')}")
                    print(f"   질문: {pub[x['id']]['messages'][-1]['content'][:140]!r}")
                    if k == "facts":
                        print(f"   정답 사실: {x['labels'].get('gold_facts')}")
                    print(f"   답변: {x['resp']['answer'][:260]!r}")
    print("\n판정이 달라진 문항 (항목, 기본→다시, 정답 action, 응대):")
    for k, v in sorted(diff.items(), key=lambda kv: -kv[1]):
        print("  ", v, k)
    if a.dump:
        Path(a.dump).write_text(json.dumps([{"id": x["id"], "old": x["r"], "new": n} for x, n in zip(rows, new)], ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
