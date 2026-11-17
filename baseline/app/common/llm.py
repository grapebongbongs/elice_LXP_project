"""운영진 제공 LLM(OpenAI 호환) 호출 래퍼. 튜터·가드가 같이 쓴다.

- 동시성 세마포어로 64개 동시 요청에서도 과호출 방지
- JSON 응답 강제 + 파싱 실패 시 None 반환 (호출 측이 fallback)
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
    backup_effort: str | None = None,
    backup_after: float = 0.0,
) -> dict[str, Any] | None:
    """system/user 프롬프트로 JSON 객체 하나를 받는다. 실패하면 None.

    backup_effort·backup_after 를 주면, 호출이 backup_after 초 안에 끝나지 않을 때 같은 프롬프트를 더 낮은 추론 수준
    (backup_effort)으로 한 번 더 보내 먼저 끝난 쪽을 쓴다. 느린 호출 하나 때문에 요청이 제한 시간에 걸리는 것을 막는 용도다.
    """
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

    hedge_after = config.LLM_HEDGE_MEDIUM if reasoning_effort in ("medium", "high") else config.LLM_HEDGE_LOW
    backup = None
    if backup_effort and backup_after > 0 and _supports_effort and backup_effort != reasoning_effort:
        hedge_after = backup_after
        backup = ({**kwargs, "extra_body": {"reasoning_effort": backup_effort}}, f"backup_{backup_effort}")
    resp = await _call_hedged(kwargs, reasoning_effort or "none", hedge_after, backup)
    return _parse_json(resp.choices[0].message.content or "")


async def _call_once(kwargs: dict[str, Any], effort: str, started: asyncio.Event | None = None):
    """LLM 호출 한 번. 걸린 시간과 오류 이름만 로그에 남긴다(본문 없음)."""
    global _supports_effort
    async with _sem:
        if started is not None:
            started.set()  # 동시 호출 자리를 얻어 실제로 호출을 시작했다
        t0 = time.perf_counter()
        err = ""
        try:
            try:
                return await client().chat.completions.create(**kwargs)
            except BadRequestError as e:
                # reasoning_effort 때문에 거절된 경우에만 끄고 재시도 (다른 400 은 그대로 올림)
                if "extra_body" not in kwargs or "reasoning" not in str(e).lower():
                    raise
                _supports_effort = False
                kwargs = {k: v for k, v in kwargs.items() if k != "extra_body"}
                return await client().chat.completions.create(**kwargs)
        except asyncio.CancelledError:
            err = "Cancelled"
            raise
        except Exception as e:
            err = type(e).__name__
            raise
        finally:
            log_llm(effort, (time.perf_counter() - t0) * 1000, err)


async def _call_hedged(kwargs: dict[str, Any], effort: str, hedge_after: float, backup: tuple[dict[str, Any], str] | None = None):
    """호출이 hedge_after 초 안에 끝나지 않으면 같은 호출을 하나 더 보내 먼저 끝난 쪽을 쓴다.
    backup=(호출 인자, 로그 이름)을 주면 두 번째 호출은 그 인자(더 낮은 추론 수준)로 보낸다.

    추론형 모델은 가끔 한 호출이 20초 넘게 걸린다. 그 한 번 때문에 요청 전체가 제한 시간을 넘겨
    기본 답으로 빠지는 것을 막는다(실측: 튜터 요청의 약 1.5%). hedge_after <= 0 이면 한 번만 호출한다.
    """
    started = asyncio.Event()
    tasks = [asyncio.create_task(_call_once(kwargs, effort, started))]
    try:
        if hedge_after > 0:
            # 기다린 시간은 '호출을 실제로 시작한 뒤'부터 센다. 동시 호출 한도 때문에 줄을 서 있던 시간까지 세면
            # 서버가 바쁠 때 같은 호출을 하나 더 보내 줄을 더 길게 만든다.
            waiter = asyncio.create_task(started.wait())
            try:
                await asyncio.wait({tasks[0], waiter}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                waiter.cancel()
            done, _ = await asyncio.wait(tasks, timeout=hedge_after)
            if not done:
                second, label = backup if backup else (dict(kwargs), effort)
                tasks.append(asyncio.create_task(_call_once(second, label)))
        pending = set(tasks)
        last_exc: BaseException | None = None
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for t in done:
                if t.exception() is None:
                    return t.result()
                last_exc = t.exception()
        raise last_exc  # 둘 다 실패
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()
