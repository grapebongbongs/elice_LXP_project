"""FastAPI 서버 (공통). 튜터/가드 로직은 app/tutor, app/guard 에만 있다.

이 파일이 보장하는 것 (파트 담당은 신경 쓰지 않아도 됨):
- Bearer 토큰 인증
- 리다이렉트 없음 (/tutor, /tutor/ 모두 직접 처리)
- 어떤 예외·시간 초과에도 HTTP 200 + 규격 JSON
- 본문 없이 시간·길이·상태·오류명만 로깅
"""
import asyncio
import json
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app import config, guard, tutor
from app.common.kb import KnowledgeBase
from app.common.logging_util import log_error, log_request
from app.common.schema import (
    enforce_guard,
    enforce_tutor,
    guard_fallback,
    normalize_request,
    tutor_fallback,
)

MAX_BODY = 256 * 1024
STATE: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 색인 같은 준비 작업은 서버 시작 때 끝낸다
    STATE["kb"] = KnowledgeBase(config.KB_DIR)
    if not config.SERVER_TOKEN:
        print("[server] WARNING: SERVER_TOKEN 미설정 - 인증 없이 동작합니다 (로컬 테스트용)")
    if config.LLM_MOCK:
        print("[server] LLM_MOCK=1 - LLM 호출 없이 fallback 응답만 냅니다")
    yield


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.router.redirect_slashes = False


def _authorized(request: Request) -> bool:
    if not config.SERVER_TOKEN:
        return True
    return request.headers.get("authorization", "") == f"Bearer {config.SERVER_TOKEN}"


async def _read_json(request: Request) -> tuple[dict, int]:
    raw = await request.body()
    n = len(raw)
    try:
        body = json.loads(raw[:MAX_BODY].decode("utf-8", errors="ignore") or "{}")
    except json.JSONDecodeError:
        body = {}
    return body, n


async def _serve(request: Request, part: str) -> JSONResponse:
    t0 = time.perf_counter()
    path = f"/{part}"
    if not _authorized(request):
        log_request(path, 0, 401, (time.perf_counter() - t0) * 1000, "Unauthorized")
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    body, n = await _read_json(request)
    req = normalize_request(body)
    kb: KnowledgeBase = STATE["kb"]
    err = ""
    try:
        if part == "tutor":
            raw = await asyncio.wait_for(tutor.handle(req, kb), timeout=config.REQUEST_DEADLINE)
            resp = enforce_tutor(raw, valid_ids=set(kb.sections) or None)
        else:
            raw = await asyncio.wait_for(guard.handle(req, kb), timeout=config.REQUEST_DEADLINE)
            cand = req.get("candidate_answer")
            resp = enforce_guard(raw, cand is not None, cand or "")
    except Exception as e:  # 시간 초과 포함 — 항상 규격 응답
        err = type(e).__name__
        log_error(part, e)
        resp = tutor_fallback() if part == "tutor" else guard_fallback(req.get("candidate_answer") is not None)

    log_request(path, n, 200, (time.perf_counter() - t0) * 1000, err)
    return JSONResponse(resp, status_code=200)


# 프록시가 /proxy/<port> 접두어를 떼지 않는 경우까지 대비해 두 경로 모두 등록
for prefix in ("", "/proxy/{port}"):
    for part in ("tutor", "guard"):
        for suffix in ("", "/"):

            def _make(p=part):
                async def endpoint(request: Request):
                    return await _serve(request, p)

                return endpoint

            app.add_api_route(f"{prefix}/{part}{suffix}", _make(), methods=["POST"])


@app.get("/health")
async def health():
    return {"ok": True, "sections": len(STATE.get("kb").sections) if STATE.get("kb") else 0}
