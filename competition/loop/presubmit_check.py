#!/usr/bin/env python3
"""제출 버튼을 누르기 전 점검. 하나라도 실패하면 제출하지 않는다.

    python3 loop/presubmit_check.py                 # 점검만
    python3 loop/presubmit_check.py --write-commit  # 통과하면 submission.json 의 commit 을 현재 HEAD 로 채움

점검 항목 (공지 §8, §14, §17)
- submission.json 주소가 …tunnel.elice.io/proxy/<포트>/tutor|guard 형식이고 8080 이 아닌가
- token 이 영문·숫자·기호뿐이고 LLM 키와 다른가
- 터널 주소로 실제 호출했을 때 리다이렉트 없이 200 + 규격 JSON 이 오는가, 틀린 토큰은 거절되는가
- 64개를 한꺼번에 보내도 전부 규격 응답이 오고 가장 느린 응답이 20초 안인가
- baseline/ 에 커밋하지 않은 변경이 없는가 (시연에서 마지막 제출 커밋의 코드를 확인한다)

점검에 쓰는 요청은 공개 세트 문항이다. 표준 라이브러리만 쓴다.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "grader"))
import scoring  # noqa: E402

COMP = Path("/home/elicer/competition")
URL_RX = re.compile(r"^https?://[A-Za-z0-9.-]+\.tunnel\.elice\.io/proxy/(\d+)/(tutor|guard)$")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


OPENER = urllib.request.build_opener(NoRedirect)


def post(url, token, payload, timeout=30):
    req = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    t0 = time.perf_counter()
    try:
        with OPENER.open(req, timeout=timeout) as r:
            return r.status, json.loads(r.read(256 * 1024 + 1)), time.perf_counter() - t0
    except urllib.error.HTTPError as e:
        return e.code, None, time.perf_counter() - t0
    except Exception as e:
        return type(e).__name__, None, time.perf_counter() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=str(COMP / "submission.json") if (COMP / "submission.json").exists() else str(ROOT / "submission.json"))
    ap.add_argument("--write-commit", action="store_true")
    ap.add_argument("--burst", type=int, default=64)
    a = ap.parse_args()

    fails: list[str] = []

    def check(ok: bool, msg: str):
        print(("  ok   " if ok else "  FAIL ") + msg)
        if not ok:
            fails.append(msg)

    path = Path(a.file)
    sub = json.loads(path.read_text(encoding="utf-8"))
    token = sub.get("token", "")
    print(f"submission: {path}")
    for key, part in (("tutor_url", "tutor"), ("guard_url", "guard")):
        m = URL_RX.match(sub.get(key, ""))
        check(bool(m) and m.group(2) == part and m.group(1) != "8080",
              f"{key} 형식 (…tunnel.elice.io/proxy/<포트>/{part}, 8080 아님)")
    check(bool(token) and bool(re.fullmatch(r"[\x21-\x7e]+", token)), "token: 영문·숫자·기호만 (한글·공백 없음)")
    check(token != os.getenv("OPENAI_API_KEY", "\0"), "token 이 LLM API 키와 다름")
    if fails:
        sys.exit("\n주소·토큰부터 고치세요. (호출 점검은 건너뜀)")

    pub = ROOT / "public" if (ROOT / "public").exists() else COMP / "public"
    tutor_items = [json.loads(l) for l in (pub / "tutor.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    guard_items = [json.loads(l) for l in (pub / "guard.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]

    def payload(it):
        p = {"spec_version": "1.0", "session_id": "s_precheck", "messages": it["messages"]}
        if it.get("candidate_answer"):
            p["candidate_answer"] = it["candidate_answer"]
        return p

    def valid(part, it, body):
        if part == "tutor":
            return scoring.parse_tutor(body) is not None
        return scoring.parse_guard(body, bool(it.get("candidate_answer"))) is not None

    for part, items in (("tutor", tutor_items), ("guard", guard_items)):
        url = sub[f"{part}_url"]
        st, body, sec = post(url, token, payload(items[0]))
        check(st == 200 and valid(part, items[0], body), f"/{part} 단건: 상태 {st}, 규격 JSON, {sec:.1f}s")
        st, _, _ = post(url, token + "x", payload(items[0]))
        check(st in (401, 403), f"/{part} 틀린 토큰 거절 (상태 {st})")

    n = a.burst // 2
    jobs = [("tutor", it) for it in tutor_items[:n]] + [("guard", it) for it in guard_items[: a.burst - n]]

    def one(job):
        part, it = job
        st, body, sec = post(sub[f"{part}_url"], token, payload(it))
        return st == 200 and valid(part, it, body), sec

    t0 = time.perf_counter()
    with ThreadPoolExecutor(a.burst) as ex:
        res = list(ex.map(one, jobs))
    wall = time.perf_counter() - t0
    okn = sum(ok for ok, _ in res)
    slow = max(sec for _, sec in res)
    check(okn == len(res), f"동시 {len(res)}개: 규격 응답 {okn}/{len(res)}")
    check(slow < 20, f"동시 {len(res)}개: 가장 느린 응답 {slow:.1f}s (<20s), 전체 {wall:.1f}s")
    est = wall * 600 / len(res)
    check(est < 180, f"600개 예상 소요 약 {est:.0f}s (<180s 권장. 전체 채점 제한은 5분보다 짧을 수 있음)")

    dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no", "--", "baseline"],
                           cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip()
    check(not dirty, "baseline/ 에 커밋하지 않은 변경 없음")
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip()

    if fails:
        sys.exit(f"\n제출 금지: {len(fails)}개 항목 실패")
    if a.write_commit:
        sub["commit"] = head
        path.write_text(json.dumps(sub, ensure_ascii=False), encoding="utf-8")
        print(f"\ncommit = {head} 을 submission.json 에 적었습니다.")
    print("\n모두 통과. 제출해도 됩니다. (채점이 끝날 때까지 서버와 런박스 탭을 그대로 두세요)")


if __name__ == "__main__":
    main()
