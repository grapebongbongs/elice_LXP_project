"""입력 정규화 (튜터·가드 공통으로 쓸 수 있음).

- NFKC 유니코드 정규화 (전각·호환 문자 통일)
- 제로폭·bidi 제어 문자 제거 (숨긴 지시·키워드 우회 방지)
- 공백 정리
원문 의미를 바꾸지 않는 변환만 한다.
"""
import re
import unicodedata

# 제로폭 문자, bidi 제어 문자, BOM, soft hyphen 등 화면에 보이지 않는 문자
_INVISIBLE = re.compile(
    "[­᠎​-‏‪-‮⁠-⁤⁦-⁯﻿]"
)
_SPACES = re.compile(r"[ \t　]+")


def has_invisible(text: str) -> bool:
    return bool(_INVISIBLE.search(text or ""))


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = _INVISIBLE.sub("", text)
    text = _SPACES.sub(" ", text)
    return text.strip()


def normalize_messages(messages: list[dict]) -> list[dict]:
    return [{"role": m.get("role", "user"), "content": normalize_text(m.get("content", ""))} for m in messages]


_KEY_STRIP = re.compile(r"[\s.,!?~·…'\"]+")


def compact(text: str) -> str:
    """비교용 키: 정규화 후 공백·문장부호 제거."""
    return _KEY_STRIP.sub("", normalize_text(text))
