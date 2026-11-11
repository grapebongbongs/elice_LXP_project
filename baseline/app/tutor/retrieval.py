"""튜터 전용 임베딩 + BM25 하이브리드 검색.

가드의 검색 정책과 분리해, tutor 실험만 독립적으로 비교할 수 있다. KB 임베딩은 lifespan에서
한 번 생성하고, 요청별 질문 임베딩은 응답을 만들 때만 메모리에 존재한다. 질문이나 벡터를
파일·로그·캐시에 저장하지 않는다.
"""
import asyncio
import math
from collections.abc import Sequence

from openai import AsyncOpenAI

from app import config
from app.common.kb import KnowledgeBase, Section
from app.tutor import config as C


def _normalize(vector: Sequence[float]) -> list[float] | None:
    """L2 정규화. 잘못된/영 벡터는 검색 결과에 쓰지 않는다."""
    try:
        values = [float(x) for x in vector]
    except (TypeError, ValueError):
        return None
    norm = math.sqrt(sum(x * x for x in values))
    if not values or norm == 0.0:
        return None
    return [x / norm for x in values]


def _dot(left: Sequence[float], right: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(left, right))


def rrf_fuse(
    bm25_hits: list[tuple[Section, float]],
    dense_hits: list[tuple[Section, float]],
    *,
    rrf_k: int,
    top_k: int,
) -> list[tuple[Section, float]]:
    """두 순위를 Reciprocal Rank Fusion으로 합친다.

    점수 스케일이 서로 다른 BM25와 cosine을 직접 더하지 않기 위해 순위만 사용한다.
    동점은 BM25 점수, 섹션 ID 순으로 고정해 실행마다 결과가 흔들리지 않게 한다.
    """
    fused: dict[str, tuple[Section, float, float]] = {}
    for hits in (bm25_hits, dense_hits):
        for rank, (section, source_score) in enumerate(hits, start=1):
            old = fused.get(section.id)
            score = (old[1] if old else 0.0) + 1.0 / (rrf_k + rank)
            bm25_score = old[2] if old else 0.0
            if hits is bm25_hits:
                bm25_score = source_score
            fused[section.id] = (section, score, bm25_score)
    ranked = sorted(fused.values(), key=lambda item: (-item[1], -item[2], item[0].id))
    return [(section, score) for section, score, _ in ranked[:top_k]]


class TutorRetriever:
    """서버 수명 동안 KB 벡터만 보관하는 튜터 검색기."""

    def __init__(self, kb: KnowledgeBase):
        self.kb = kb
        self._client: AsyncOpenAI | None = None
        self._sem = asyncio.Semaphore(config.EMBEDDING_MAX_CONCURRENCY)
        self._sections: list[Section] = []
        self._vectors: list[list[float]] = []
        self.enabled = False

    async def initialize(self) -> None:
        """KB 전체를 임베딩한다. 실패해도 BM25 서버는 정상 기동한다."""
        if not C.EMBEDDING_ENABLED:
            print("[tutor-retriever] dense disabled by TUTOR_EMBEDDING_ENABLED=0")
            return
        if config.LLM_MOCK:
            print("[tutor-retriever] dense disabled by LLM_MOCK=1")
            return
        if not config.EMBEDDING_BASE_URL or not config.EMBEDDING_API_KEY or not self.kb.sections:
            print("[tutor-retriever] dense disabled: embedding configuration or KB unavailable")
            return
        if C.EMBEDDING_DIMENSIONS <= 0:
            print("[tutor-retriever] dense disabled: invalid embedding dimensions")
            return

        sections = list(self.kb.sections.values())
        documents = [f"{section.title}\n{section.text}" for section in sections]
        try:
            self._client = AsyncOpenAI(
                api_key=config.EMBEDDING_API_KEY,
                base_url=config.EMBEDDING_BASE_URL,
                timeout=config.EMBEDDING_TIMEOUT,
                max_retries=1,
            )
            vectors = await self._embed(documents)
            normalized = [_normalize(vector) for vector in vectors]
            if len(normalized) != len(sections) or any(vector is None for vector in normalized):
                raise ValueError("invalid embedding response")
            dimensions = len(normalized[0] or [])
            if dimensions != C.EMBEDDING_DIMENSIONS or any(
                len(vector or []) != dimensions for vector in normalized
            ):
                raise ValueError("unexpected embedding dimensions")
        except Exception as exc:
            # 예외 문자열에는 프롬프트·입력 본문이 포함될 가능성이 있어 이름만 남긴다.
            print(f"[tutor-retriever] dense disabled: startup {type(exc).__name__}")
            await self.close()
            return

        self._sections = sections
        self._vectors = [vector for vector in normalized if vector is not None]
        self.enabled = True
        print(
            f"[tutor-retriever] dense ready: {len(self._sections)} KB sections, "
            f"{dimensions} dimensions, RRF"
        )

    async def close(self) -> None:
        self.enabled = False
        self._sections = []
        self._vectors = []
        if self._client is not None:
            await self._client.close()
            self._client = None

    async def _embed(self, inputs: list[str]) -> list[list[float]]:
        if self._client is None:
            raise RuntimeError("embedding client unavailable")
        kwargs: dict = {"model": config.EMBEDDING_MODEL, "input": inputs}
        # 기본 1536은 dimensions 인자를 생략해 프록시 호환성을 높인다.
        if C.EMBEDDING_DIMENSIONS != 1536:
            kwargs["dimensions"] = C.EMBEDDING_DIMENSIONS
        async with self._sem:
            response = await self._client.embeddings.create(**kwargs)
        return [item.embedding for item in response.data]

    async def search(self, query: str, *, k: int) -> list[tuple[Section, float]]:
        """질문 벡터는 이 호출이 끝나면 버려진다. 오류 시 BM25 결과를 돌려준다."""
        bm25_hits = self.kb.search(query, k=max(k, C.HYBRID_BM25_K))
        if not query.strip() or not self.enabled:
            return bm25_hits[:k]
        try:
            embedded = await self._embed([query])
            query_vector = _normalize(embedded[0]) if len(embedded) == 1 else None
            if query_vector is None or len(query_vector) != C.EMBEDDING_DIMENSIONS:
                raise ValueError("invalid query embedding")
            dense_hits = sorted(
                ((section, _dot(query_vector, vector)) for section, vector in zip(self._sections, self._vectors)),
                key=lambda item: (-item[1], item[0].id),
            )[: C.HYBRID_DENSE_K]
            return rrf_fuse(
                bm25_hits,
                dense_hits,
                rrf_k=C.HYBRID_RRF_K,
                top_k=k,
            )
        except Exception as exc:
            # 질의 내용·벡터는 남기지 않는다. 오류 한 번으로 KB 인덱스를 폐기하지도 않는다.
            print(f"[tutor-retriever] query embedding fallback: {type(exc).__name__}")
            return bm25_hits[:k]
