#!/usr/bin/env python3
"""저장된 튜터 답에 자료 문장을 덧붙였을 때 정답 사실이 답에 담기는 비율을 고르는 방법별로 잰다. 런박스에서 실행(임베딩 호출만).

    python3 loop/fact_cover_eval.py baseline/results/quote-append_run1.jsonl [다른 실행 ...]

- 대상: 정답 응대가 answer 이고 실제로 answer 로 답했으며 인용이 있는 문항. 덧붙일 문장은 답이 인용한 섹션에서만 고른다.
- 담김의 기준: 정답 사실의 글자 bigram 가운데 0.6 이상이 답에 있음(#69·#72 와 같은 기준).
- 고르는 방법: BM25(#72 의 방식), 임베딩(질문 / 답 / 둘의 평균과의 유사도). 본문 문장만 쓰는 것과 표의 행까지 쓰는 것을 나눈다.
- 출력은 개수와 비율뿐이다(문항 내용을 찍지 않는다).
"""
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "loop"))
from exp_run import load_env_files, pick  # noqa: E402

os.environ.update({k: v for k, v in load_env_files().items() if k not in os.environ})
sys.path.insert(0, str(ROOT / "baseline"))
from openai import OpenAI  # noqa: E402

from app import config  # noqa: E402
from app.common.kb import KnowledgeBase  # noqa: E402
from app.common.normalize import normalize_messages  # noqa: E402
from app.tutor.pipeline import is_quick_button, rank_units, section_units  # noqa: E402

SKIP_OVERLAP = 0.8  # 답에 이미 이만큼 들어 있는 문장은 건너뛴다
MAX_UNIT = 160


def squash(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())


def bigrams(text: str) -> set:
    s = squash(text)
    return {s[i : i + 2] for i in range(len(s) - 1)}


def embed(client, model, texts, batch=48):
    out = []
    for i in range(0, len(texts), batch):
        resp = client.embeddings.create(model=model, input=[t[:6000] or " " for t in texts[i : i + batch]])
        out.extend(d.embedding for d in sorted(resp.data, key=lambda d: d.index))
    m = np.asarray(out, dtype=np.float32)
    return m / np.maximum(np.linalg.norm(m, axis=1, keepdims=True), 1e-9)


def main():
    files = sys.argv[1:]
    if not files:
        sys.exit(__doc__)
    base_url = os.environ.get("EMBEDDING_BASE_URL")
    if not base_url:
        sys.exit("EMBEDDING_BASE_URL 이 없습니다(운영진 제공 주소로만 호출한다).")
    client = OpenAI(api_key=os.environ.get("EMBEDDING_API_KEY") or os.environ.get("OPENAI_API_KEY"), base_url=base_url, timeout=60)
    model = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small")

    kb = KnowledgeBase(config.KB_DIR)
    unit_vec, unit_prose = {}, {}
    texts, where = [], []
    for s in kb.sections.values():
        body = squash(s.text)
        for j, (t, _) in enumerate(section_units(s)):
            texts.append(t)
            where.append((s.id, j))
            unit_prose[(s.id, j)] = squash(t) in body  # 표의 행은 '칸 이름: 값'으로 다시 적은 것이라 본문에 그대로 없다
    vecs = embed(client, model, texts)
    for w, v in zip(where, vecs):
        unit_vec[w] = v
    print(f"units {len(texts)} (본문 문장 {sum(unit_prose.values())}, 표의 행 {len(texts) - sum(unit_prose.values())})")

    public = {}
    for line in (pick("public") / "tutor.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            d = json.loads(line)
            public[d["id"]] = d

    for f in files:
        rows = [json.loads(l) for l in Path(f).read_text(encoding="utf-8").splitlines() if l.strip()]
        sel = []
        for x in rows:
            L, resp = x["labels"], x.get("resp") or {}
            if x.get("endpoint") != "tutor" or L.get("action") != "answer" or not L.get("gold_facts"):
                continue
            if resp.get("action") != "answer" or not resp.get("citations") or resp["citations"][0] not in kb.sections:
                continue
            if x["id"] not in public:
                continue
            sel.append(x)
        ms = [normalize_messages(public[x["id"]]["messages"]) for x in sel]

        def last_user(m):
            u = [t["content"] for t in m if t["role"] == "user" and t["content"]]
            u = [t for t in u if not is_quick_button(t)] or u or [""]
            return u

        qv = embed(client, model, [last_user(m)[-1] for m in ms])
        av = embed(client, model, [x["resp"]["answer"] for x in sel])
        res = defaultdict(Counter)
        added = defaultdict(list)
        table_share = defaultdict(list)
        tot = Counter()
        for k, x in enumerate(sel):
            split = "holdout" if x.get("holdout") else "tune"
            tot[split] += 1
            answer = x["resp"]["answer"]
            gold = bigrams(" ".join(x["labels"]["gold_facts"]))
            sec = kb.sections[x["resp"]["citations"][0]]
            units = section_units(sec)
            U = np.stack([unit_vec[(sec.id, j)] for j in range(len(units))]) if units else np.zeros((0, 1))
            u = last_user(ms[k])
            bm_query = f"{u[-1]} {u[-1]} {' '.join(u[:-1][-3:])}"
            orders = {
                "bm25": rank_units(bm_query, units),
                "dense(질문)": list(np.argsort(-(U @ qv[k]))) if len(units) else [],
                "dense(답)": list(np.argsort(-(U @ av[k]))) if len(units) else [],
                "dense(질문+답)": list(np.argsort(-(U @ (qv[k] + av[k])))) if len(units) else [],
            }
            res[("덧붙이지 않음", 0)][split] += len(gold & bigrams(answer)) / len(gold) >= 0.6
            best = max((len(gold & (bigrams(answer) | bigrams(t))) / len(gold) for t, _ in units), default=0.0)
            res[("상한: 가장 좋은 문장 1개", 1)][split] += best >= 0.6
            for name, order in orders.items():
                for prose_only in (True, False):
                    for n in (1, 2, 3):
                        have = bigrams(answer)
                        picked = []
                        for j in order:
                            if len(picked) >= n:
                                break
                            t = units[j][0]
                            if prose_only and not unit_prose[(sec.id, j)]:
                                continue
                            g = bigrams(t)
                            if len(g) < 8 or len(t) > MAX_UNIT or len(g & have) >= SKIP_OVERLAP * len(g):
                                continue
                            picked.append(j)
                            have |= g
                        key = (f"{name} {'본문만' if prose_only else '표 포함'}", n)
                        res[key][split] += len(gold & have) / len(gold) >= 0.6
                        added[key].append(sum(len(units[j][0]) for j in picked))
                        if picked:
                            table_share[key].append(sum(not unit_prose[(sec.id, j)] for j in picked) / len(picked))
        print(f"== {Path(f).name}: answer 로 답한 정답 answer 문항 tune {tot['tune']}, holdout {tot['holdout']}")
        for key in sorted(res):
            cells = [f"{s} {res[key][s]}/{tot[s]} ({100 * res[key][s] / max(1, tot[s]):.0f}%)" for s in ("tune", "holdout")]
            extra = ""
            if key in added:
                extra = f" | 더한 글자 평균 {np.mean(added[key]):.0f}"
                if table_share[key]:
                    extra += f", 표의 행 비율 {100 * np.mean(table_share[key]):.0f}%"
            print(f"  {key[0]:22s} n={key[1]}  " + " | ".join(cells) + extra)


if __name__ == "__main__":
    main()
