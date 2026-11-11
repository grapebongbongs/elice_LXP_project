"""로컬 채점 결과 두 개를 문항 단위로 짝지어 비교한다 (LLM·서버 호출 없음, 파일만 읽음).

score_local.py 출력(/tmp/score_<커밋>.txt)의 문항 줄 `id\tendpoint\tcategory\tpoints`를 읽는다.
본문은 들어 있지 않으므로 로그 규칙(LOOP.md A-7)에 걸리지 않는다.

사용:
  python3 compare_runs.py BEST1.txt [BEST2.txt ...] -- CAND1.txt [CAND2.txt ...] [--endpoint guard]

- `--` 앞은 best 실행들, 뒤는 후보 실행들. 같은 코드의 여러 실행은 문항별 평균을 낸다.
- best를 2회 이상 주면 '같은 코드인데 점수가 바뀐 문항'(불안정 문항)을 따로 센다.
- 출력: 짝지은 차이의 평균과 표준오차, 범주별 차이, 불안정 문항을 뺀 차이.
"""
import argparse
import math
import sys
from collections import defaultdict


def load(path):
    rows = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 4 or parts[1] not in ("tutor", "guard"):
                continue
            try:
                pts = float(parts[3])
            except ValueError:
                continue
            rows[parts[0]] = (parts[1], parts[2], pts)
    return rows


def merge(runs):
    """문항별 평균과 실행 사이 변동 여부."""
    ids = set.intersection(*(set(r) for r in runs))
    out = {}
    for i in ids:
        vals = [r[i][2] for r in runs]
        ep, cat = runs[0][i][0], runs[0][i][1]
        out[i] = (ep, cat, sum(vals) / len(vals), max(vals) - min(vals) > 1e-9)
    return out


def summarize(diffs):
    n = len(diffs)
    if n == 0:
        return 0.0, 0.0, 0
    m = sum(diffs) / n
    sd = math.sqrt(sum((d - m) ** 2 for d in diffs) / (n - 1)) if n > 1 else 0.0
    return m, sd / math.sqrt(n), n


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--" not in argv:
        sys.exit("best 파일들과 후보 파일들 사이에 -- 를 넣으세요")
    k = argv.index("--")
    best_files, rest = argv[:k], argv[k + 1:]
    ap = argparse.ArgumentParser()
    ap.add_argument("cand", nargs="+")
    ap.add_argument("--endpoint", choices=["tutor", "guard"])
    a = ap.parse_args(rest)

    best = merge([load(p) for p in best_files])
    cand = merge([load(p) for p in a.cand])
    ids = sorted(i for i in best if i in cand and (not a.endpoint or best[i][0] == a.endpoint))

    all_d, stable_d = [], []
    per_cat = defaultdict(list)
    unstable = 0
    changed = []
    for i in ids:
        d = cand[i][2] - best[i][2]
        all_d.append(d)
        per_cat[(best[i][0], best[i][1])].append(d)
        if best[i][3]:
            unstable += 1
        else:
            stable_d.append(d)
        if abs(d) > 1e-9:
            changed.append((d, i, best[i][0], best[i][1]))

    m, se, n = summarize(all_d)
    print(f"짝지은 문항 {n}개, 후보-best 평균 {m:+.2f}점 (표준오차 {se:.2f}, 대략 95% 구간 {m - 2 * se:+.2f} ~ {m + 2 * se:+.2f})")
    if len(best_files) > 1:
        ms, ses, ns = summarize(stable_d)
        print(f"best 실행끼리 점수가 바뀐 불안정 문항 {unstable}개 / {n}개")
        print(f"불안정 문항을 뺀 {ns}개: 평균 {ms:+.2f}점 (표준오차 {ses:.2f})")
    print("범주별 (평균 차이, 문항 수):")
    for (ep, cat), ds in sorted(per_cat.items()):
        print(f"  {ep:5s} {cat:24s} {sum(ds) / len(ds):+7.2f}  n={len(ds)}")
    changed.sort()
    print(f"점수가 달라진 문항 {len(changed)}개 (가장 나빠진 5개 / 가장 좋아진 5개):")
    for d, i, ep, cat in changed[:5] + changed[-5:]:
        print(f"  {d:+7.1f}  {i}  {ep}/{cat}")


if __name__ == "__main__":
    main()
