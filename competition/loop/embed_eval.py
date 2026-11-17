#!/usr/bin/env python3
"""문장 단위 임베딩을 세 가지 용도로 재 보는 실험. 런박스에서 실행한다(임베딩 호출만, 채팅 호출 없음).

    python3 loop/embed_eval.py [--dump /tmp/rb/embed_cache.npz]

#4(loop/retrieval_eval.py)는 섹션·소제목 단위 임베딩만 봤다. 여기서는 자료를 문장(표는 행) 단위로 임베딩한다.

1. 근거 섹션 검색: 섹션 점수 = 그 섹션 문장들의 유사도 최댓값. 단독, BM25 와 섞기, BM25 상위 k 안에서만 다시 고르기.
2. 자료에 없는 질문 가려내기: 질문과 가장 가까운 자료 문장의 유사도가 정답 answer 문항과 abstain 문항에서 갈리는지(AUC).
   가드의 out_of_material 범주에도 같은 값을 잰다.
3. 정답 사실 문장 찾기: 정답 근거 섹션에서 질문과 가장 가까운 문장 n개가 정답 사실을 담는 비율(BM25 로 고른 것과 비교).

출력은 개수와 비율뿐이다(문항 내용을 찍지 않는다). tune 과 holdout 을 따로 보고한다.
"""
import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "loop"))
from exp_run import is_holdout, load_env_files, pick  # noqa: E402

os.environ.update({k: v for k, v in load_env_files().items() if k not in os.environ})
sys.path.insert(0, str(ROOT / "baseline"))
from openai import OpenAI  # noqa: E402

from app import config  # noqa: E402
from app.common import focus  # noqa: E402
from app.common.kb import KnowledgeBase, tokenize  # noqa: E402
from app.common.normalize import normalize_messages  # noqa: E402
from app.tutor.pipeline import build_query, is_quick_button, rank_units, section_units  # noqa: E402


def embed(client, model, texts, batch=48):
    out = []
    for i in range(0, len(texts), batch):
        resp = client.embeddings.create(model=model, input=[t[:6000] or " " for t in texts[i : i + batch]])
        out.extend(d.embedding for d in sorted(resp.data, key=lambda d: d.index))
    m = np.asarray(out, dtype=np.float32)
    return m / np.maximum(np.linalg.norm(m, axis=1, keepdims=True), 1e-9)


def bigrams(text: str) -> set:
    s = "".join(ch for ch in text.lower() if ch.isalnum())
    return {s[i : i + 2] for i in range(len(s) - 1)}


def auc(pos, neg):
    """pos 가 neg 보다 클 확률."""
    if not pos or not neg:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return round(wins / (len(pos) * len(neg)), 3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", default="")
    a = ap.parse_args()

    kb = KnowledgeBase(config.KB_DIR)
    secs = list(kb.sections.values())
    sid_index = {s.id: i for i, s in enumerate(secs)}
    base_url = os.environ.get("EMBEDDING_BASE_URL")
    if not base_url:
        sys.exit("EMBEDDING_BASE_URL 이 없습니다(운영진 제공 주소로만 호출한다).")
    client = OpenAI(api_key=os.environ.get("EMBEDDING_API_KEY") or os.environ.get("OPENAI_API_KEY"), base_url=base_url, timeout=60)
    model = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small")

    # 자료 단위: 문장·표의 행(section_units) + 소제목 줄
    unit_text, unit_sec = [], []
    for s in secs:
        for line in s.text.splitlines():
            if line.startswith("#") and line.lstrip("# ").strip():
                unit_text.append(line.lstrip("# ").strip())
                unit_sec.append(sid_index[s.id])
        for text, _ in section_units(s):
            unit_text.append(text)
            unit_sec.append(sid_index[s.id])
    unit_sec = np.asarray(unit_sec)
    titled = [f"{secs[si].title}: {t}" for t, si in zip(unit_text, unit_sec)]
    U = {"raw": embed(client, model, unit_text), "titled": embed(client, model, titled)}
    print(f"sections {len(secs)}, units {len(unit_text)}, dim {U['raw'].shape[1]}")

    def load(name):
        rows = [json.loads(l) for l in (pick("public") / name).read_text(encoding="utf-8").splitlines() if l.strip()]
        for d in rows:
            d["ms"] = normalize_messages(d["messages"])
        return rows

    def users(ms):
        u = [m["content"] for m in ms if m["role"] == "user" and m["content"]]
        return [x for x in u if not is_quick_button(x)] or u or [""]

    def qforms(rows):
        last = [users(d["ms"])[-1] for d in rows]
        us = ["\n".join(users(d["ms"])[-4:]) for d in rows]
        conv = ["\n".join(f"{'학습자' if m['role'] == 'user' else '튜터'}: {m['content']}" for m in d["ms"][-6:]) for d in rows]
        return {"last": embed(client, model, last), "users": embed(client, model, us), "conv": embed(client, model, conv)}

    tutor = load("tutor.jsonl")
    guard = load("guard.jsonl")
    QT, QG = qforms(tutor), qforms(guard)
    if a.dump:
        np.savez_compressed(a.dump, raw=U["raw"], titled=U["titled"], unit_sec=unit_sec,
                            **{f"t_{k}": v for k, v in QT.items()}, **{f"g_{k}": v for k, v in QG.items()})

    def sec_scores(sim_row, how="max"):
        """문장 유사도 한 줄 → 섹션별 점수(길이 = 섹션 수)."""
        out = np.full(len(secs), -1.0, dtype=np.float32)
        for si in range(len(secs)):
            v = sim_row[unit_sec == si]
            if len(v):
                out[si] = v.max() if how == "max" else np.sort(v)[-2:].mean()
        return out

    def bm25_vec(ms):
        v = np.zeros(len(secs), dtype=np.float32)
        for s, sc in kb.search(build_query(ms), k=len(secs)):
            v[sid_index[s.id]] = sc
        return v

    def norm(v):
        lo, hi = float(v.min()), float(v.max())
        return (v - lo) / ((hi - lo) or 1.0)

    # ---------- 1. 근거 섹션 검색 ----------
    ev_rows = [(i, d) for i, d in enumerate(tutor) if d["labels"].get("evidence")]
    BM = {i: bm25_vec(d["ms"]) for i, d in ev_rows}
    DS = {}
    for qn in ("last", "users", "conv"):
        for un in ("raw", "titled"):
            sim = QT[qn] @ U[un].T
            for how in ("max", "top2"):
                DS[(qn, un, how)] = {i: sec_scores(sim[i], how) for i, _ in ev_rows}

    def report(label, scorer):
        cells = []
        for split in ("tune", "holdout"):
            hit, n = 0, 0
            for i, d in ev_rows:
                if is_holdout(d["id"]) != (split == "holdout"):
                    continue
                n += 1
                best = secs[int(np.argmax(scorer(i)))].id
                hit += best in d["labels"]["evidence"]
            cells.append(f"{split} {hit}/{n}")
        print(f"  {label:52s} " + " | ".join(cells))

    print("== 1. 근거 섹션 1위 적중 (정답 근거가 있는 튜터 문항)")
    report("bm25 (현재)", lambda i: BM[i])
    for key in DS:
        report("dense " + "/".join(key), lambda i, k=key: DS[k][i])
    print("  -- 섞기: w*bm25 + (1-w)*dense (각각 0~1 로 맞춤)")
    for key in DS:
        if key[2] != "max":
            continue
        for w in (0.9, 0.8, 0.7, 0.5):
            report(f"mix w={w} " + "/".join(key), lambda i, k=key, w=w: w * norm(BM[i]) + (1 - w) * norm(DS[k][i]))
    print("  -- BM25 상위 k 안에서 dense 로 다시 고르기 (gap: 2위/1위 점수 비가 이 값 이상일 때만)")
    for key in DS:
        if key[2] != "max":
            continue
        for k in (2, 3):
            for gap in (0.0, 0.8, 0.9):
                def rer(i, key=key, k=k, gap=gap):
                    b = BM[i]
                    order = np.argsort(-b)
                    if b[order[0]] <= 0 or b[order[1]] / b[order[0]] < gap:
                        return b
                    out = np.full(len(secs), -9.0, dtype=np.float32)
                    out[order[:k]] = DS[key][i][order[:k]]
                    return out
                report(f"rerank k={k} gap={gap} " + "/".join(key), rer)

    # ---------- 2. 자료에 없는 질문 ----------
    print("== 2. 가장 가까운 자료 문장의 유사도: 자료에 있는 질문(pos) 대 자료에 없는 질문(neg) 의 AUC")
    for name, rows, Q, pos_f, neg_f in (
        ("tutor answer vs abstain", tutor, QT, lambda d: d["labels"]["action"] == "answer", lambda d: d["labels"]["action"] == "abstain"),
        ("guard normal vs out_of_material", guard, QG, lambda d: d["labels"].get("category") == "normal",
         lambda d: d["labels"].get("category") == "out_of_material"),
    ):
        for qn in ("last", "users"):
            for un in ("raw", "titled"):
                sim = Q[qn] @ U[un].T
                feats = {"all": sim.max(axis=1)}
                top1 = []
                for i, d in enumerate(rows):
                    b = bm25_vec(d["ms"])
                    m = unit_sec == int(np.argmax(b))
                    top1.append(float(sim[i][m].max()) if m.any() else -1.0)
                feats["bm25top1"] = np.asarray(top1)
                for fn, f in feats.items():
                    cells = []
                    for split in ("tune", "holdout"):
                        idx = [i for i, d in enumerate(rows) if is_holdout(d["id"]) == (split == "holdout")]
                        pos = [float(f[i]) for i in idx if pos_f(rows[i])]
                        neg = [float(f[i]) for i in idx if neg_f(rows[i])]
                        cells.append(f"{split} auc={auc(pos, neg)} (pos {len(pos)}, neg {len(neg)})")
                    print(f"  {name:32s} q={qn:5s} unit={un:6s} feat={fn:8s} " + " | ".join(cells))
    # 이름 붙은 규칙 꼴(#36)이 이미 잡는 문항을 뺀 나머지에서의 분포
    sim = QT["last"] @ U["raw"].T
    f = sim.max(axis=1)
    rest_neg, rest_pos = [], []
    for i, d in enumerate(tutor):
        if is_holdout(d["id"]):
            continue
        named = bool(focus.NAMED_RULE.search(users(d["ms"])[-1]))
        if d["labels"]["action"] == "abstain" and not named:
            rest_neg.append(float(f[i]))
        if d["labels"]["action"] == "answer":
            rest_pos.append(float(f[i]))
    print(f"  tune, 이름 붙은 규칙 꼴이 아닌 abstain {len(rest_neg)}개 대 answer {len(rest_pos)}개: auc={auc(rest_pos, rest_neg)}")
    if rest_neg and rest_pos:
        qs = [0.05, 0.25, 0.5, 0.75, 0.95]
        print("    answer  분위수", [round(float(np.quantile(rest_pos, q)), 3) for q in qs])
        print("    abstain 분위수", [round(float(np.quantile(rest_neg, q)), 3) for q in qs])

    # ---------- 3. 정답 사실 문장 찾기 ----------
    print("== 3. 정답 근거 섹션에서 고른 문장 n개가 정답 사실을 담는 비율 (정답 사실 글자 bigram 의 0.6 이상)")
    fact_rows = [(i, d) for i, d in enumerate(tutor) if d["labels"].get("gold_facts") and d["labels"].get("evidence")]
    res = Counter()
    tot = Counter()
    for i, d in fact_rows:
        split = "holdout" if is_holdout(d["id"]) else "tune"
        sec = kb.sections.get(d["labels"]["evidence"][0])
        if sec is None:
            continue
        units = section_units(sec)
        if not units:
            continue
        gold = bigrams(" ".join(d["labels"]["gold_facts"]))
        if not gold:
            continue
        texts = [t for t, _ in units]
        tot[split] += 1
        # 이 섹션의 단위들만 다시 임베딩하지 않고, 전체 단위 행렬에서 같은 글을 찾는다
        rows_idx = [j for j in np.where(unit_sec == sid_index[sec.id])[0] if unit_text[j] in set(texts)]
        q_last = users(d["ms"])[-1]
        orders = {"bm25": [texts[j] for j in rank_units(build_query(d["ms"]), units)]}
        for qn in ("last", "users"):
            for un in ("raw", "titled"):
                s = (QT[qn][i] @ U[un][rows_idx].T)
                orders[f"dense {qn}/{un}"] = [unit_text[rows_idx[j]] for j in np.argsort(-s)]
        for name, order in orders.items():
            for n in (1, 2, 3):
                got = bigrams(" ".join(order[:n]))
                res[(name, n, split)] += len(gold & got) / len(gold) >= 0.6
        best_single = max(len(gold & bigrams(t)) / len(gold) for t in texts)
        res[("상한: 가장 잘 맞는 문장 1개", 1, split)] += best_single >= 0.6
    names = sorted({k[0] for k in res})
    for name in names:
        cells = []
        for split in ("tune", "holdout"):
            cells.append(split + " " + " ".join(f"n={n}: {res[(name, n, split)]}/{tot[split]}" for n in (1, 2, 3) if (name, n, split) in res))
        print(f"  {name:28s} " + " | ".join(cells))


if __name__ == "__main__":
    main()
