#!/usr/bin/env python3
"""제출 결과를 기록하고, 최고점이면 방법을 정리해 저장(세이브)한다.

    python3 loop/record_submission.py "총점 71.3 (튜터 68.2, 가드 74.4) / 요청 600개 중 시간 초과 0개, 형식 오류·응답 없음 0개, 판정 오류 1개 (seed 1234)"
    python3 loop/record_submission.py --clipboard-file result.txt --note "fewshot k=4"
    python3 loop/record_submission.py --status          # 지금 최고점과 마지막 제출 보기

하는 일
1. 결과 문장에서 점수·오류 수를 읽어 experiments/submissions.jsonl 에 한 줄 추가 (점수와 커밋만, 문항 내용 없음).
2. 같은 커밋의 제출 평균이 지금까지의 최고 평균보다 높으면 → experiments/BEST.md 에 방법을 정리해 커밋하고,
   브랜치 best/<점수>-<커밋> 을 만들어 GitHub 에 올린다 (브랜치 이름만 봐도 최고점임을 알 수 있다).
3. 낮으면 → 최고 커밋이 무엇인지와, 마감 전에 그 커밋으로 되돌려 다시 제출해야 한다는 안내를 출력
   (최종 점수는 계정별 '마지막' 제출이다).

제출 한 번은 문항 표본이 달라 점수가 흔들린다. 그래서 '한 번의 최고점'이 아니라 '커밋별 평균'으로 비교한다.
"""
import argparse
import json
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUBS = ROOT / "experiments" / "submissions.jsonl"
BEST = ROOT / "experiments" / "BEST.md"

NUM = r"(\d+(?:\.\d+)?)"
SCORE = re.compile(rf"총점\s*{NUM}\s*\(\s*튜터\s*{NUM}\s*,\s*가드\s*{NUM}\s*\)")
FIELDS = {
    "requests": re.compile(r"요청\s*(\d+)\s*개"),
    "timeouts": re.compile(r"시간\s*초과\s*(\d+)\s*개"),
    "format_errors": re.compile(r"형식\s*오류[^0-9]*(\d+)\s*개"),
    "judge_errors": re.compile(r"판정\s*오류\s*(\d+)\s*개"),
    "seed": re.compile(r"seed\s*([0-9A-Za-z]+)"),  # 숫자뿐 아니라 16진 문자열로도 나온다
}


def git(*args) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip()


def parse(text: str) -> dict | None:
    m = SCORE.search(text)
    if not m:
        return None
    out = {"total": float(m.group(1)), "tutor": float(m.group(2)), "guard": float(m.group(3))}
    for k, rx in FIELDS.items():
        f = rx.search(text)
        out[k] = (int(f.group(1)) if f.group(1).isdigit() else f.group(1)) if f else None
    return out


def load() -> list[dict]:
    if not SUBS.exists():
        return []
    return [json.loads(l) for l in SUBS.read_text(encoding="utf-8").splitlines() if l.strip()]


def by_commit(subs: list[dict]) -> dict[str, list[float]]:
    """서버 문제(시간 초과·형식 오류)가 있던 제출은 방법 비교에서 뺀다."""
    out: dict[str, list[float]] = {}
    for s in subs:
        if (s.get("timeouts") or 0) + (s.get("format_errors") or 0) > 0:
            continue
        out.setdefault(s["commit"], []).append(s["total"])
    return out


def best_of(groups: dict[str, list[float]]):
    if not groups:
        return None, None
    c = max(groups, key=lambda k: statistics.mean(groups[k]))
    return c, statistics.mean(groups[c])


def status(subs):
    if not subs:
        print("기록된 제출이 없습니다.")
        return
    groups = by_commit(subs)
    c, m = best_of(groups)
    last = subs[-1]
    print(f"제출 {len(subs)}회. 마지막: {last['time']} @{last['commit']} 총점 {last['total']}")
    for k, v in sorted(groups.items(), key=lambda kv: -statistics.mean(kv[1])):
        sd = statistics.pstdev(v) if len(v) > 1 else 0.0
        print(f"  @{k}  평균 {statistics.mean(v):.2f}  (n={len(v)}, ±{sd:.2f})  {git('log', '-1', '--format=%s', k)[:70]}")
    if c and last["commit"] != c:
        print(f"\n[주의] 마지막 제출(@{last['commit']})이 최고 커밋(@{c}, 평균 {m:.2f})이 아닙니다.")
        print(f"       마감 전에:  git checkout {c} -- baseline  → 서버 재시작 → presubmit_check → 다시 제출")


def write_best(commit: str, mean: float, n: int, prev_commit: str | None, note: str, sub: dict):
    changes = git("log", "--format=- %h %s", f"{prev_commit}..{commit}") if prev_commit else git("log", "-5", "--format=- %h %s", commit)
    files = git("diff", "--stat", f"{prev_commit}..{commit}", "--", "baseline") if prev_commit else ""
    BEST.write_text(
        f"""# 현재 최고 설정 (자동 기록)

- 커밋: `{commit}` (브랜치 `best/{mean:.2f}-{commit}`)
- 제출 점수: 평균 **{mean:.2f}** (같은 커밋 {n}회) — 마지막 회차 총점 {sub['total']} (튜터 {sub['tutor']}, 가드 {sub['guard']})
- 기록 시각: {sub['time']}
- 메모: {note or '-'}

## 이전 최고(`{prev_commit or '없음'}`) 이후 바뀐 것

{changes or '(커밋 기록 없음)'}

```text
{files}
```

## 복원 방법

```bash
git checkout {commit} -- baseline
bash baseline/run.sh            # 서버 재시작
python3 loop/presubmit_check.py --write-commit
# → 제출 버튼
```
""",
        encoding="utf-8",
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("text", nargs="?", help="제출 기록에 나온 결과 문장")
    ap.add_argument("--clipboard-file", help="결과 문장이 든 파일")
    ap.add_argument("--commit", help="제출에 쓴 커밋 (기본: 현재 HEAD)")
    ap.add_argument("--note", default="")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--min-gain", type=float, default=0.5, help="이만큼 넘게 올라야 최고점으로 인정 (표본 흔들림 방지)")
    ap.add_argument("--trailer", default="", help="최고점 커밋 메시지 끝에 붙일 줄 (예: Co-Authored-By: …)")
    ap.add_argument("--no-best", action="store_true", help="기록만 하고 best/ 브랜치와 BEST.md 는 건드리지 않는다 (이미 최고인 커밋을 되풀이 제출할 때: 평균이 바뀔 때마다 브랜치가 새로 생기는 것을 막는다)")
    a = ap.parse_args()

    subs = load()
    if a.status:
        return status(subs)
    text = a.text or (Path(a.clipboard_file).read_text(encoding="utf-8") if a.clipboard_file else sys.stdin.read())
    res = parse(text)
    if not res:
        sys.exit("결과 문장에서 '총점 … (튜터 …, 가드 …)' 을 찾지 못했습니다. 채점 오류 메시지라면 기록하지 않습니다.")
    commit = git("rev-parse", "--short", a.commit or "HEAD") or a.commit
    if not a.commit and git("status", "--porcelain", "--untracked-files=no", "--", "baseline"):
        print("[주의] baseline/ 에 커밋하지 않은 변경이 있습니다. 제출한 코드와 커밋이 다를 수 있습니다.")

    old_groups = by_commit(subs)
    old_best_commit, old_best = best_of({k: v for k, v in old_groups.items() if k != commit})
    sub = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "commit": commit, **res, "note": a.note}
    SUBS.parent.mkdir(exist_ok=True)
    with open(SUBS, "a", encoding="utf-8") as f:
        f.write(json.dumps(sub, ensure_ascii=False) + "\n")
    subs.append(sub)

    bad = (res.get("timeouts") or 0) + (res.get("format_errors") or 0)
    print(f"기록: @{commit} 총점 {res['total']} (튜터 {res['tutor']}, 가드 {res['guard']})"
          f" 시간 초과 {res.get('timeouts')} 형식 오류 {res.get('format_errors')} 판정 오류 {res.get('judge_errors')}")
    if bad:
        print("[주의] 시간 초과·형식 오류가 있습니다. 서버 문제이므로 방법 비교에서 빼고, 원인을 먼저 고칩니다 (LOOP.md §5).")
        return status(subs)

    mine = by_commit(subs)[commit]
    mean = statistics.mean(mine)
    if a.no_best:
        print(f"기록만 했습니다(--no-best): 이 커밋 평균 {mean:.2f} (같은 커밋 {len(mine)}회). best/ 브랜치와 BEST.md 는 그대로입니다.")
        return
    if old_best is None or mean > old_best + a.min_gain:
        branch = f"best/{mean:.2f}-{commit}"
        write_best(commit, mean, len(mine), old_best_commit, a.note, sub)
        gain = "" if old_best is None else f" (이전 최고 {old_best:.2f} @{old_best_commit}, +{mean - old_best:.2f})"
        print(f"\n★ 최고점 갱신: 평균 {mean:.2f}{gain}")

        # 방법 정리(BEST.md)와 제출 기록을 커밋한다. 지금 작업 트리의 서버 코드가 제출한 커밋과 같으면
        # 그 정리 커밋까지 포함해 브랜치를 만들고, 다르면 제출한 커밋 자체에 브랜치를 건다.
        def run(*c):
            return subprocess.run(["git", *c], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")

        files = ["experiments/BEST.md", "experiments/submissions.jsonl"]
        run("add", *files)
        run("commit", "-m", f"best: {mean:.2f} @{commit} ({a.note or 'no note'})", *(["-m", a.trailer] if a.trailer else []), "--", *files)
        same_code = run("diff", "--quiet", commit, "HEAD", "--", "baseline/app", "baseline/run.sh", "baseline/requirements.txt").returncode == 0
        run("branch", "-f", branch, "HEAD" if same_code else commit)
        pushed = run("push", "origin", branch).returncode == 0
        how = "및 push" if pushed else f"(push 실패 — 직접: git push origin {branch})"
        print(f"  브랜치 {branch} 생성 {how}, experiments/BEST.md 갱신.")
    else:
        print(f"\n최고점 아님: 이 커밋 평균 {mean:.2f} vs 최고 {old_best:.2f} @{old_best_commit} (기준: +{a.min_gain} 초과)")
        print("  이 계정의 '마지막 제출'이 지금 이 점수입니다. 더 실험하지 않을 거라면 최고 커밋으로 되돌려 다시 제출하세요:")
        print(f"    git checkout {old_best_commit} -- baseline && (서버 재시작) && python3 loop/presubmit_check.py --write-commit")


if __name__ == "__main__":
    main()
