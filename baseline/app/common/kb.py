"""지식 베이스 로드 + BM25 검색. 서버 시작 시 한 번 색인한다.

kb/<과목>/<섹션ID>.md  → 파일명(stem)이 인용할 섹션 ID.
한국어 형태소 분석기 없이 '단어 + 글자 bigram' 토큰으로 BM25 를 돌린다.
검색 고도화(임베딩, 청킹 등)는 이 파일 인터페이스(search)를 유지한 채 바꾸면
튜터·가드 양쪽에 같이 적용된다.
"""
import csv
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


def _split_sub_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    cur: list[str] = []
    for ln in text.splitlines():
        if re.match(r"^#{2,3}\s+", ln) and cur:
            blk = "\n".join(cur).strip()
            if blk:
                blocks.append(blk)
            cur = [ln]
        else:
            cur.append(ln)
    if cur:
        blk = "\n".join(cur).strip()
        if blk:
            blocks.append(blk)
    return blocks or [text]


class KnowledgeBase:
    def __init__(self, kb_dir: str):
        self.kb_dir = Path(kb_dir)
        self.sections: dict[str, Section] = {}
        self._df: Counter = Counter()
        self._avgdl = 1.0
        self._sub_blocks: list[tuple[str, Counter, int]] = []
        self._sub_df: Counter = Counter()
        self._sub_avgdl = 1.0
        self._load()

    # ---------- 로드 ----------
    def _load(self) -> None:
        if not self.kb_dir.exists():
            print(f"[kb] WARNING: {self.kb_dir} not found - KB empty")
            return
        for p in sorted(self.kb_dir.rglob("*.md")):
            text = self._inline_tables(p, p.read_text(encoding="utf-8", errors="ignore"))
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
        for sid in sorted(self.sections.keys()):
            sec = self.sections[sid]
            for blk in _split_sub_blocks(sec.text):
                btoks = tokenize(blk)
                bc = Counter(btoks)
                self._sub_blocks.append((sid, bc, len(btoks)))
                self._sub_df.update(bc.keys())
        if self._sub_blocks:
            self._sub_avgdl = sum(b[2] for b in self._sub_blocks) / len(self._sub_blocks)
        print(f"[kb] loaded {len(self.sections)} sections from {self.kb_dir}")

    @staticmethod
    def _inline_tables(md_path: Path, text: str) -> str:
        """섹션의 표는 같은 이름의 CSV(<섹션ID>-table<n>.csv)로 분리되어 있다 (SPEC §1).
        마크다운에는 '(표: 파일명)' 자리 표시만 있으므로, 표 내용을 그 자리에 넣어
        검색과 근거 판정에 함께 쓰이게 한다. 자리 표시가 없으면 끝에 붙인다."""
        for t in sorted(md_path.parent.glob(f"{md_path.stem}-table*.csv")):
            try:
                rows = [r for r in csv.reader(t.read_text(encoding="utf-8-sig", errors="ignore").splitlines()) if r]
            except Exception:
                continue
            if not rows:
                continue
            body = "\n".join("| " + " | ".join(c.strip() for c in r) + " |" for r in rows)
            marker = f"(표: {t.name})"
            text = text.replace(marker, body) if marker in text else text + "\n\n" + body
        return text

    # ---------- 검색 ----------
    def search(
        self, query: str, k: int = 5, k1: float = 1.5, b: float = 0.75, max_qf: int | None = None
    ) -> list[tuple[Section, float]]:
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
                qf_eff = min(qf, max_qf) if max_qf else qf
                idf = math.log(1 + (n - self._df[t] + 0.5) / (self._df[t] + 0.5))
                sc += qf_eff * idf * f * (k1 + 1) / (f + k1 * (1 - b + b * s.length / self._avgdl))
            if sc > 0:
                scores.append((s, sc))
        scores.sort(key=lambda x: -x[1])
        return scores[:k]

    def search_sub_maxp(
        self,
        query: str,
        k: int = 5,
        k1: float = 1.2,
        b: float = 0.4,
        max_qf: int | None = 3,
        doc_weight: float = 0.5,
        sub_b: float = 0.2,
    ) -> list[tuple[Section, float]]:
        q = Counter(tokenize(query))
        if not q or not self.sections:
            return []
        doc_hits = self.search(query, k=len(self.sections), k1=k1, b=b, max_qf=max_qf)
        doc_map = {s.id: sc for s, sc in doc_hits}
        sub_map: dict[str, float] = {}
        sub_n = len(self._sub_blocks)
        if sub_n:
            sub_df = self._sub_df
            sub_avgdl = self._sub_avgdl
            for sid, toks, dl in self._sub_blocks:
                sc = 0.0
                for t, qf in q.items():
                    f = toks.get(t)
                    if not f:
                        continue
                    qf_eff = min(qf, max_qf) if max_qf else qf
                    idf = math.log(1 + (sub_n - sub_df[t] + 0.5) / (sub_df[t] + 0.5))
                    sc += qf_eff * idf * f * (k1 + 1) / (f + k1 * (1 - sub_b + sub_b * dl / sub_avgdl))
                if sc > sub_map.get(sid, 0.0):
                    sub_map[sid] = sc
        max_doc = max(doc_map.values(), default=0.0)
        max_sub = max(sub_map.values(), default=0.0)
        all_sids = set(doc_map.keys()) | set(sub_map.keys())
        if not all_sids:
            return []
        scored: list[tuple[str, float]] = []
        for sid in all_sids:
            dn = (doc_map.get(sid, 0.0) / max_doc) if max_doc > 0 else 0.0
            sn = (sub_map.get(sid, 0.0) / max_sub) if max_sub > 0 else 0.0
            scored.append((sid, doc_weight * dn + (1.0 - doc_weight) * sn))
        scored.sort(key=lambda x: (-x[1], x[0]))
        return [(self.sections[sid], sc) for sid, sc in scored[:k]]

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
