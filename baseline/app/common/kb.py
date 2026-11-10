"""지식 베이스 로드 + BM25 검색. 서버 시작 시 한 번 색인한다.

kb/<과목>/<섹션ID>.md  → 파일명(stem)이 인용할 섹션 ID.
한국어 형태소 분석기 없이 '단어 + 글자 bigram' 토큰으로 BM25 를 돌린다.
검색 고도화(임베딩, 청킹 등)는 이 파일 인터페이스(search)를 유지한 채 바꾸면
튜터·가드 양쪽에 같이 적용된다.
"""
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Section:
    id: str
    subject: str
    title: str
    text: str
    tokens: Counter = field(default_factory=Counter, repr=False)
    length: int = 0


_WORD = re.compile(r"[0-9A-Za-z_]+|[가-힣]+")


def tokenize(text: str) -> list[str]:
    text = text.lower()
    toks: list[str] = []
    for w in _WORD.findall(text):
        toks.append(w)
        if re.match(r"[가-힣]", w) and len(w) >= 2:
            toks.extend(w[i : i + 2] for i in range(len(w) - 1))
    return toks


class KnowledgeBase:
    def __init__(self, kb_dir: str):
        self.kb_dir = Path(kb_dir)
        self.sections: dict[str, Section] = {}
        self._df: Counter = Counter()
        self._avgdl = 1.0
        self._load()

    # ---------- 로드 ----------
    def _load(self) -> None:
        if not self.kb_dir.exists():
            print(f"[kb] WARNING: {self.kb_dir} not found - KB empty")
            return
        for p in sorted(self.kb_dir.rglob("*.md")):
            text = p.read_text(encoding="utf-8", errors="ignore")
            title = next(
                (l.lstrip("# ").strip() for l in text.splitlines() if l.startswith("#")),
                p.stem,
            )
            subject = p.relative_to(self.kb_dir).parts[0] if p.parent != self.kb_dir else ""
            sec = Section(id=p.stem, subject=subject, title=title, text=text)
            # 제목은 가중치를 주기 위해 3번 넣는다
            toks = tokenize((title + "\n") * 3 + text)
            sec.tokens = Counter(toks)
            sec.length = len(toks)
            self.sections[sec.id] = sec
        for s in self.sections.values():
            self._df.update(s.tokens.keys())
        if self.sections:
            self._avgdl = sum(s.length for s in self.sections.values()) / len(self.sections)
        print(f"[kb] loaded {len(self.sections)} sections from {self.kb_dir}")

    # ---------- 검색 ----------
    def search(self, query: str, k: int = 5, k1: float = 1.5, b: float = 0.75) -> list[tuple[Section, float]]:
        q = Counter(tokenize(query))
        if not q or not self.sections:
            return []
        n = len(self.sections)
        scores: list[tuple[Section, float]] = []
        for s in self.sections.values():
            sc = 0.0
            for t, qf in q.items():
                f = s.tokens.get(t)
                if not f:
                    continue
                idf = math.log(1 + (n - self._df[t] + 0.5) / (self._df[t] + 0.5))
                sc += qf * idf * f * (k1 + 1) / (f + k1 * (1 - b + b * s.length / self._avgdl))
            if sc > 0:
                scores.append((s, sc))
        scores.sort(key=lambda x: -x[1])
        return scores[:k]

    def has(self, sid: str) -> bool:
        return sid in self.sections


def format_context(hits: list[tuple[Section, float]], max_chars: int = 2500) -> str:
    """LLM 프롬프트에 넣을 근거 블록."""
    blocks = []
    for s, _ in hits:
        body = s.text if len(s.text) <= max_chars else s.text[:max_chars] + "\n...(생략)"
        blocks.append(f"<section id=\"{s.id}\">\n{body}\n</section>")
    return "\n\n".join(blocks) if blocks else "(검색된 자료 없음)"


def conversation_text(messages: list[dict], max_turns: int = 8) -> str:
    """대화 이력을 프롬프트용 문자열로. 사용자 발화는 '데이터'로만 취급하도록 태그로 감싼다."""
    lines = []
    for m in messages[-max_turns:]:
        role = "학습자" if m.get("role") == "user" else "튜터"
        lines.append(f"[{role}]\n{m.get('content', '')}")
    return "\n\n".join(lines)


def retrieval_query(messages: list[dict]) -> str:
    """마지막 사용자 발화를 중심으로, 이전 사용자 발화로 맥락(실습/예제 이름)을 보강."""
    users = [m.get("content", "") for m in messages if m.get("role") == "user"]
    if not users:
        return ""
    last = users[-1]
    prev = " ".join(users[-4:-1])
    return f"{last} {last} {prev}"
