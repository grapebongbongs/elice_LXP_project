"""64 동시 요청 + 규격 검사 + 인증 거절 + 리다이렉트 없음 확인.

    python scripts/load_test.py --n 128            # 로컬
    python scripts/load_test.py --url https://<런박스>.tunnel.elice.io/proxy/8000 --n 64

LLM_MOCK=1 로 서버를 띄우면 크레딧 없이 형식·동시성만 확인할 수 있다.
"""
import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.common.schema import GUARD_CATEGORIES, TUTOR_ACTIONS  # noqa: E402

TUTOR_REQ = {"spec_version": "1.0", "session_id": "s_load", "messages": [{"role": "user", "content": "리스트에서 for 문으로 반복하는 방법을 알려주세요"}]}
GUARD_REQ = {**TUTOR_REQ, "candidate_answer": "for 문을 사용하면 리스트의 각 요소를 차례로 꺼낼 수 있습니다."}


def check_tutor(j):
    assert set(j) >= {"answer", "action", "citations"}, j
    assert isinstance(j["answer"], str) and len(j["answer"]) <= 4000
    assert j["action"] in TUTOR_ACTIONS
    assert isinstance(j["citations"], list) and all(isinstance(c, str) for c in j["citations"])


def check_guard(j, has_cand):
    assert j["input"]["decision"] in ("allow", "block")
    assert j["input"]["category"] in GUARD_CATEGORIES
    if has_cand:
        o = j["output"]
        assert isinstance(o["grounded"], bool) and isinstance(o["safe"], bool)
        assert isinstance(o["unsupported"], list)


async def main(a):
    base = a.url.rstrip("/")
    h = {"Authorization": f"Bearer {a.token}"} if a.token else {}
    async with httpx.AsyncClient(timeout=35, follow_redirects=False) as cli:
        if a.token:
            r = await cli.post(f"{base}/tutor", json=TUTOR_REQ, headers={"Authorization": "Bearer wrong"})
            print(f"wrong token → {r.status_code} (200 이 아니어야 함)")
        for p in ("/tutor", "/guard"):
            r = await cli.post(base + p, json=GUARD_REQ, headers=h)
            assert not (300 <= r.status_code < 400), f"redirect on {p}!"

        sem = asyncio.Semaphore(64)
        lat, errs = [], []

        async def one(i):
            part = "tutor" if i % 2 == 0 else "guard"
            body = TUTOR_REQ if part == "tutor" else (GUARD_REQ if i % 4 == 1 else TUTOR_REQ)
            async with sem:
                t0 = time.perf_counter()
                try:
                    r = await cli.post(f"{base}/{part}", json=body, headers=h)
                    assert r.status_code == 200, r.status_code
                    j = r.json()
                    check_tutor(j) if part == "tutor" else check_guard(j, "candidate_answer" in body)
                except Exception as e:
                    errs.append(f"{part}: {type(e).__name__} {e}")
                lat.append(time.perf_counter() - t0)

        t0 = time.perf_counter()
        await asyncio.gather(*(one(i) for i in range(a.n)))
        total = time.perf_counter() - t0
    lat.sort()
    print(f"{a.n} reqs in {total:.1f}s  p50={lat[len(lat)//2]:.2f}s  p95={lat[int(len(lat)*.95)-1]:.2f}s  max={lat[-1]:.2f}s")
    print(f"errors: {len(errs)}")
    for e in errs[:10]:
        print("  ", e)
    sys.exit(1 if errs else 0)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.getenv("SERVER_URL", "http://127.0.0.1:8000"))
    ap.add_argument("--token", default=os.getenv("SERVER_TOKEN", ""))
    ap.add_argument("--n", type=int, default=128)
    asyncio.run(main(ap.parse_args()))
