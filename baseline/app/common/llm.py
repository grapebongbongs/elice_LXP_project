"""운영진 제공 LLM(OpenAI 호환) 호출 래퍼. 튜터·가드가 같이 쓴다.

- 동시성 세마포어로 64개 동시 요청에서도 과호출 방지
- JSON 응답 강제 + 파싱 실패 시 None 반환 (호출 측이 fallback)
- 호출마다 tag·소요 시간·결과 이름만 로그 (본문 금지, 규칙 A-7)
"""
import asyncio
import json
import re
import time
from typing import Any

from openai import AsyncOpenAI, BadRequestError

from app import config
from app.common.logging_util import log_llm

_client: AsyncOpenAI | None = None
_sem = asyncio.Semaphore(config.LLM_MAX_CONCURRENCY)
# 모델이 reasoning_effort 를 지원하지 않으면 한 번 실패 후 끈다
_supports_effort = True
MAX_COMPLETION_TOKENS = 2000


def client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            api_key=config.OPENAI_API_KEY or "missing",
            base_url=config.OPENAI_BASE_URL,
            timeout=config.LLM_TIMEOUT,
            max_retries=1,
        )
    return _client


def _parse_json(text: str) -> dict | None:
    if not text:
        return None
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                obj = json.loads(m.group(0))
                return obj if isinstance(obj, dict) else None
            except json.JSONDecodeError:
                return None
    return None


async def chat_json(
    system: str,
    user: str,
    *,
    reasoning_effort: str = "low",
    max_tokens: int = 2000,
    tag: str = "",
) -> dict[str, Any] | None:
    """system/user 프롬프트로 JSON 객체 하나를 받는다. 실패하면 None."""
    global _supports_effort
    if config.LLM_MOCK:
        return None

    kwargs: dict[str, Any] = dict(
        model=config.OPENAI_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        response_format={"type": "json_object"},
        # 운영진 프록시는 비스트리밍 응답에 2000 토큰을 넘으면 400 을 낸다
        max_completion_tokens=min(max_tokens, MAX_COMPLETION_TOKENS),
    )
    # openai SDK 1.58 미만은 reasoning_effort 키워드를 몰라 TypeError 가 나므로
    # SDK 버전과 무관하게 extra_body 로 그대로 전달한다.
    if _supports_effort and reasoning_effort:
        kwargs["extra_body"] = {"reasoning_effort": reasoning_effort}

    t0 = time.perf_counter()
    try:
        async with _sem:
            try:
                resp = await client().chat.completions.create(**kwargs)
            except BadRequestError as e:
                # reasoning_effort 때문에 거절된 경우에만 끄고 재시도 (다른 400 은 그대로 올림)
                if "extra_body" not in kwargs or "reasoning" not in str(e).lower():
                    raise
                _supports_effort = False
                kwargs.pop("extra_body")
                resp = await client().chat.completions.create(**kwargs)
    except asyncio.CancelledError:
        # 요청 마감(REQUEST_DEADLINE)에 걸려 바깥에서 끊긴 경우
        log_llm(tag, (time.perf_counter() - t0) * 1000, "cancelled")
        raise
    except Exception as e:
        log_llm(tag, (time.perf_counter() - t0) * 1000, type(e).__name__)
        raise
    ms = (time.perf_counter() - t0) * 1000
    choice = resp.choices[0]
    out = _parse_json(choice.message.content or "")
    log_llm(tag, ms, _outcome(choice, out))
    return out


def _outcome(choice: Any, out: dict | None) -> str:
    """기본값으로 빠지는 이유를 이름 하나로. 본문은 보지 않고 상태만 본다."""
    if out is not None:
        return "ok"
    if getattr(choice, "finish_reason", None) == "length":
        return "length"  # 추론 토큰이 한도를 먹어 본문이 비거나 잘림
    if not (choice.message.content or "").strip():
        return "empty"
    return "parse"
