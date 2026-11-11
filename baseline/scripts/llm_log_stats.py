"""서버 로그에서 LLM 호출·요청의 소요 시간과 결과 이름을 집계한다 (파일만 읽음, 호출 없음).

서버 로그에는 시간·길이·상태·오류 이름만 있다 (logging_util.py). 이 스크립트도 그것만 읽는다.

사용:
  python3 scripts/llm_log_stats.py /tmp/server_<커밋>.log [--since "2026-11-11 21:10"]

출력:
  - LLM 호출: tag 별 호출 수, ok 비율, 결과 이름별 개수, ms p50/p90/p95/max
  - 요청: 경로별 수, ms p50/p95/max, err 이름별 개수
"""
import argparse
import re
from collections import Counter, defaultdict

LLM = re.compile(r"^(\S+ \S+) llm tag=(\S+) attempt=(\d+) ms=(\d+) outcome=(\S+)")
REQ = re.compile(r"^(\S+ \S+) path=(\S+) req_len=\d+ status=(\d+) ms=(\d+) err=(\S*)")


def pct(vals, p):
    if not vals:
        return 0
    vals = sorted(vals)
    return vals[min(len(vals) - 1, int(round(p / 100 * (len(vals) - 1))))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("log")
    ap.add_argument("--since", default="", help="이 시각 이후 줄만 (로그 앞부분 'YYYY-MM-DD HH:MM' 문자열 비교)")
    a = ap.parse_args()

    llm_ms, llm_out = defaultdict(list), defaultdict(Counter)
    req_ms, req_err = defaultdict(list), defaultdict(Counter)
    with open(a.log, encoding="utf-8", errors="ignore") as f:
        for line in f:
            m = LLM.match(line)
            if m:
                if a.since and m.group(1) < a.since:
                    continue
                key = f"{m.group(2)}#{m.group(3)}"
                llm_ms[key].append(int(m.group(4)))
                llm_out[key][m.group(5)] += 1
                continue
            m = REQ.match(line)
            if m:
                if a.since and m.group(1) < a.since:
                    continue
                path = m.group(2)
                req_ms[path].append(int(m.group(4)))
                req_err[path][m.group(5) or "-"] += 1

    print("LLM 호출 (tag#시도)        수    ok%    p50    p90    p95    max  결과")
    for k in sorted(llm_ms):
        ms, oc = llm_ms[k], llm_out[k]
        n = len(ms)
        print(f"  {k:24s} {n:5d} {100 * oc['ok'] / n:6.1f} {pct(ms, 50):6d} {pct(ms, 90):6d} {pct(ms, 95):6d} {max(ms):6d}  "
              + ", ".join(f"{o}={c}" for o, c in oc.most_common()))
    print("요청 (경로)                 수    p50    p95    max  err")
    for k in sorted(req_ms):
        ms = req_ms[k]
        print(f"  {k:24s} {len(ms):5d} {pct(ms, 50):6d} {pct(ms, 95):6d} {max(ms):6d}  "
              + ", ".join(f"{o}={c}" for o, c in req_err[k].most_common()))


if __name__ == "__main__":
    main()
