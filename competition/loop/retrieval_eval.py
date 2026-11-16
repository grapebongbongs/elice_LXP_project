#!/usr/bin/env python3
"""검색만 따로 재 보는 실험: BM25(현재) / 임베딩 / 둘을 섞은 방식의 근거 섹션 적중률. 런박스에서 실행한다.

    python3 loop/retrieval_eval.py

- 공개 세트 튜터 문항 중 정답 근거(evidence)가 있는 119개로, 근거 섹션이 검색 1·3·5위 안에 드는 비율을 잰다.
- 임베딩은 운영진 제공 API(EMBEDDING_BASE_URL / EMBEDDING_MODEL / EMBEDDING_API_KEY)만 쓴다.
  호출은 섹션·문단 임베딩 몇 번과 질문 임베딩 몇 번(묶음 호출)뿐이다. LLM 채팅 호출은 없다.
- tune(70%)과 holdout(30%)을 따로 보고한다. 섞는 비율은 tune 으로 고르고 holdout 으로 확인한다.
"""
import hashlib
import json
import math
import os
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "loop"))
from exp_run import load_env_files, pick  # noqa: E402

os.environ.update({k: v for k, v in load_env_files().items() if k not in os.environ})
sys.path.insert(0, str(ROOT / "baseline"))
from openai import OpenAI  # noqa: E402

from app import config  # noqa: E402
from app.common.kb import KnowledgeBase  # noqa: E402
from app.common.normalize import normalize_messages  # noqa: E402
from app.tutor.pipeline import build_query, is_quick_button  # noqa: E402


def is_holdout(item_id: str) -> bool:
    return int(hashlib.sha1(item_id.encode()).hexdigest(), 16) % 10 < 3


def embed(client, model, texts, batch=48):
    out = []
    for i in range(0, len(texts), batch):
        resp = client.embeddings.create(model=model, input=texts[i : i + batch])
        out.extend(d.embedding for d in sorted(resp.data, key=lambda d: d.index))
    return out


def cos(a, b):
    return sum(x * y for x, y in zip(a, b)) / ((math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))) or 1.0)


def passages(text: str) -> list[str]:
    """'## ' 소제목 단위로 나눈다. 섹션 제목(첫 줄)을 각 조각 앞에 붙인다."""
    lines = text.splitlines()
    title = lines[0] if lines else ""
    parts, cur = [], []
    for line in lines[1:]:
        if line.startswith("## ") and cur:
            parts.append("\n".join(cur))
            cur = []
        cur.append(line)
    if cur:
        parts.append("\n".join(cur))
    return [f"{title}\n{p}".strip() for p in parts if p.strip()] or [text]


def main():
    kb = KnowledgeBase(config.KB_DIR)
    secs = list(kb.sections.values())
    client = OpenAI(api_key=os.environ.get("EMBEDDING_API_KEY") or os.environ.get("OPENAI_API_KEY"),
                    base_url=os.environ.get("EMBEDDING_BASE_URL"), timeout=60)
    model = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small")

    items = [json.loads(l) for l in (pick("public") / "tutor.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    items = [d for d in items if d["labels"].get("evidence")]
    msgs = [normalize_messages(d["messages"]) for d in items]

    def users(ms):
        u = [m["content"] for m in ms if m["role"] == "user" and m["content"]]
        return [x for x in u if not is_quick_button(x)] or u

    q_last = [users(ms)[-1] for ms in msgs]
    q_users = ["\n".join(users(ms)[-4:]) for ms in msgs]
    q_conv = ["\n".join(f"{'학습자' if m['role'] == 'user' else '튜터'}: {m['content']}" for m in ms[-6:]) for ms in msgs]

    sec_vec = embed(client, model, [s.text[:6000] for s in secs])
    pas, pas_sid = [], []
    for s in secs:
        for p in passages(s.text):
            pas.append(p[:3000])
            pas_sid.append(s.id)
    pas_vec = embed(client, model, pas)
    qv = {"last": embed(client, model, q_last), "users": embed(client, model, q_users), "conv": embed(client, model, q_conv)}
    print(f"sections {len(secs)}, passages {len(pas)}, queries {len(items)} x3, dim {len(sec_vec[0])}")

    def bm25_scores(ms):
        return {s.id: sc for s, sc in kb.search(build_query(ms), k=len(secs))}

    def dense_scores(vec, unit):
        if unit == "section":
            return {s.id: cos(vec, v) for s, v in zip(secs, sec_vec)}
        best: dict[str, float] = {}
        for sid, v in zip(pas_sid, pas_vec):
            c = cos(vec, v)
            if c > best.get(sid, -1):
                best[sid] = c
        return best

    def norm(d):
        if not d:
            return {}
        lo, hi = min(d.values()), max(d.values())
        return {k: (v - lo) / ((hi - lo) or 1.0) for k, v in d.items()}

    def ranks(d):
        return {k: r for r, (k, _) in enumerate(sorted(d.items(), key=lambda kv: -kv[1]))}

    def evaluate(label, scorer):
        row = []
        for split in ("tune", "holdout"):
            hit, n = Counter(), 0
            for i, d in enumerate(items):
                if is_holdout(d["id"]) != (split == "holdout"):
                    continue
                n += 1
                sc = scorer(i)
                order = [k for k, _ in sorted(sc.items(), key=lambda kv: -kv[1])]
                r = min((order.index(e) for e in d["labels"]["evidence"] if e in order), default=99)
                for k in (1, 3, 5):
                    hit[k] += r < k
            row.append(f"{split} n={n} @1={hit[1] / n:.3f} @3={hit[3] / n:.3f} @5={hit[5] / n:.3f}")
        print(f"{label:40s} " + " | ".join(row))

    bm = [bm25_scores(ms) for ms in msgs]
    evaluate("bm25 (현재)", lambda i: bm[i])
    dense = {}
    for qn in ("last", "users", "conv"):
        for unit in ("section", "passage"):
            dense[(qn, unit)] = [dense_scores(v, unit) for v in qv[qn]]
            evaluate(f"dense q={qn} unit={unit}", lambda i, k=(qn, unit): dense[k][i])
    for qn, unit in (("conv", "section"), ("conv", "passage"), ("users", "passage"), ("last", "passage")):
        for a in (0.3, 0.5, 0.7):
            def mix(i, k=(qn, unit), a=a):
                b, dn = norm(bm[i]), norm(dense[k][i])
                return {s.id: a * b.get(s.id, 0.0) + (1 - a) * dn.get(s.id, 0.0) for s in secs}
            evaluate(f"mix bm25*{a} + dense({qn},{unit})", mix)

        def rrf(i, k=(qn, unit)):
            rb, rd = ranks(bm[i]), ranks(dense[k][i])
            return {s.id: 1 / (10 + rb.get(s.id, 60)) + 1 / (10 + rd.get(s.id, 60)) for s in secs}
        evaluate(f"rrf bm25 + dense({qn},{unit})", rrf)


if __name__ == "__main__":
    main()
