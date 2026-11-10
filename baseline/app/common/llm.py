"""운영진 제공 LLM(OpenAI 호환) 호출 래퍼. 튜터·가드가 같이 쓴다.

- 동시성 세마포어로 64개 동시 요청에서도 과호출 방지
- JSON 응답 강제 + 파싱 실패 시 None 반환 (호출 측이 fallback)
"""
import asyncio
import json
import re
from typing import Any

from openai import AsyncOpenAI, BadRequestError

from app import config

_client: AsyncOpenAI | None = None
_sem = asyncio.Semaphore(config.LLM_MAX_CONCURRENCY)
# 모델이 reasoning_effort 를 지원하지 않으면 한 번 실패 후 끈다
_supports_effort = True


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
    max_tokens: int = 4000,
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
        max_completion_tokens=max_tokens,
    )
    if _supports_effort and reasoning_effort:
        kwargs["reasoning_effort"] = reasoning_effort

    async with _sem:
        try:
            resp = await client().chat.completions.create(**kwargs)
        except BadRequestError:
            if "reasoning_effort" not in kwargs:
                raise
            _supports_effort = False
            kwargs.pop("reasoning_effort")
            resp = await client().chat.completions.create(**kwargs)
    return _parse_json(resp.choices[0].message.content or "")
