"""자료 문장 임베딩. 운영진이 제공한 임베딩 API(EMBEDDING_BASE_URL, text-embedding-3-small)만 부른다.

- 자료 문장의 임베딩은 서버를 띄울 때 한 번 만든다(ensure). 같은 자료면 디스크에 둔 사본(.embed_cache/)을 다시 읽는다.
  사본은 지식 베이스를 운영진 API 로 가공한 것이고, 요청 내용은 들어가지 않는다.
- 요청을 처리할 때는 견줄 글(후보 답변의 문장 등)만 임베딩한다. 실패하거나 EMBED_TIMEOUT 을 넘기면 None 을 돌려주고,
  쓰는 쪽은 임베딩 없이 그대로 동작한다.
- numpy 가 없거나 EMBEDDING_BASE_URL 이 없으면 꺼진 상태로 남는다(ready() 가 False).
"""
import asyncio
import hashlib
import os
from pathlib import Path

from openai import AsyncOpenAI

from app import config
from app.common.logging_util import log_error

try:
    import numpy as np
except Exception:  # numpy 가 없으면 임베딩 기능을 쓰지 않는다
    np = None

BASE_URL = os.getenv("EMBEDDING_BASE_URL")
API_KEY = os.getenv("EMBEDDING_API_KEY") or config.OPENAI_API_KEY
MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
TIMEOUT = float(os.getenv("EMBED_TIMEOUT", "4"))  # 요청 처리 중 임베딩 호출 한 번의 한도(초)
BATCH = 48
CACHE_DIR = Path(__file__).resolve().parents[2] / ".embed_cache"

_client: AsyncOpenAI | None = None
_lock = asyncio.Lock()
_units: list[tuple[str, str]] = []  # (섹션 id, 문장)
_vecs = None  # numpy 배열 (문장 수, 차원), 행마다 길이 1
_rows_by_sid: dict[str, list[int]] = {}
_tried = False


def available() -> bool:
    return np is not None and bool(BASE_URL) and not config.LLM_MOCK


def ready() -> bool:
    return _vecs is not None


def client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=API_KEY, base_url=BASE_URL, max_retries=0)
    return _client


async def embed(texts: list[str], timeout: float | None = None):
    """글 목록 → 길이 1 로 맞춘 임베딩 행렬. 실패하면 None."""
    if not available() or not texts:
        return None
    try:
        out = []
        for i in range(0, len(texts), BATCH):
            chunk = [t[:6000] or " " for t in texts[i : i + BATCH]]
            resp = await asyncio.wait_for(client().embeddings.create(model=MODEL, input=chunk), timeout or TIMEOUT)
            out.extend(d.embedding for d in sorted(resp.data, key=lambda d: d.index))
        m = np.asarray(out, dtype=np.float32)
        return m / np.maximum(np.linalg.norm(m, axis=1, keepdims=True), 1e-9)
    except Exception as e:
        log_error("embed", e)
        return None


async def ensure(units: list[tuple[str, str]]) -> bool:
    """자료 문장 임베딩을 한 번 만든다. units = [(섹션 id, 문장)]. 이미 만들었거나 만들 수 없으면 바로 돌아온다."""
    global _units, _vecs, _rows_by_sid, _tried
    if ready() or _tried or not available():
        return ready()
    async with _lock:
        if ready() or _tried:
            return ready()
        _tried = True
        key = hashlib.sha1((MODEL + "\x00" + "\x00".join(f"{s}\x01{t}" for s, t in units)).encode("utf-8")).hexdigest()[:16]
        path = CACHE_DIR / f"units_{key}.npy"
        vecs = None
        if path.exists():
            try:
                vecs = np.load(path)
            except Exception as e:
                log_error("embed.cache", e)
        if vecs is None or len(vecs) != len(units):
            vecs = await embed([t for _, t in units], timeout=120)
            if vecs is None:
                return False
            try:
                CACHE_DIR.mkdir(exist_ok=True)
                np.save(path, vecs)
            except Exception as e:
                log_error("embed.cache", e)
        rows: dict[str, list[int]] = {}
        for i, (sid, _) in enumerate(units):
            rows.setdefault(sid, []).append(i)
        _units, _rows_by_sid, _vecs = list(units), rows, vecs
        print(f"[embed] {len(units)} units ready (dim {vecs.shape[1]})")
        return True


def nearest(vec, sids: list[str] | None, k: int) -> list[tuple[float, str, str]]:
    """임베딩 하나와 가장 가까운 자료 문장 k개: (유사도, 섹션 id, 문장). sids 를 주면 그 섹션들 안에서만 찾는다."""
    if not ready() or k <= 0:
        return []
    if sids is None:
        rows = np.arange(len(_units))
    else:
        rows = np.asarray([i for s in sids for i in _rows_by_sid.get(s, [])], dtype=np.int64)
    if not len(rows):
        return []
    sims = _vecs[rows] @ vec
    top = np.argsort(-sims)[:k]
    return [(float(sims[j]), _units[rows[j]][0], _units[rows[j]][1]) for j in top]
