"""Optional local Ollama embeddings and exact PostgreSQL/pgvector search for public notices."""
import hashlib
import json
import math
import os
import urllib.request

from database import connect
from guide import CATALOG, published_passages


def model_name():
    return os.getenv("EVENT_SUPPORT_AGENT_EMBED_MODEL", "bge-m3")


def embed(texts, model):
    url = os.getenv("EVENT_SUPPORT_AGENT_EMBED_URL", "http://localhost:11434").rstrip("/") + "/api/embed"
    body = {"model": model, "input": texts, "truncate": False}
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            vectors = json.load(response)["embeddings"]
    except (OSError, ValueError, KeyError) as error:
        raise RuntimeError(f"讀取 Ollama 向量失敗，請確認服務與嵌入模型 {model}。") from error
    if not isinstance(vectors, list) or len(vectors) != len(texts):
        raise ValueError("嵌入模型回傳的向量數量與輸入不符。")
    for vector in vectors:
        if not isinstance(vector, list) or not vector or len(vector) != len(vectors[0]):
            raise ValueError("嵌入向量維度不一致。")
        if not all(isinstance(x, (int, float)) and math.isfinite(x) for x in vector) or not any(vector):
            raise ValueError("嵌入模型回傳了無效的向量。")
    return vectors


def document_text(passage):
    return "\n".join(passage[k] for k in ("檔期", "文件", "發布日", "章節", "原文"))


def edition_hash(passages):
    documents = sorted((p["段落ID"], document_text(p)) for p in passages)
    return hashlib.sha256(json.dumps(documents, ensure_ascii=False).encode()).hexdigest()


# 向量索引只建立於公開公告；內容版本與嵌入模型是索引有效性的依據。
def build_index(catalog=CATALOG):
    model = model_name()
    rows = []
    for edition in catalog.editions:
        passages = published_passages(edition, catalog)
        fingerprint = edition_hash(passages)
        for start in range(0, len(passages), 16):
            batch = passages[start:start + 16]
            vectors = embed([document_text(p) for p in batch], model)
            rows.extend((model, edition, fingerprint, p["段落ID"], json.dumps(vector))
                        for p, vector in zip(batch, vectors))
    with connect() as db, db.cursor() as cur:
        # First-time setup and re-indexes publish one complete database transaction.
        cur.execute("SELECT pg_advisory_xact_lock(721915034)")
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        cur.execute("""CREATE TABLE IF NOT EXISTS event_support_agent_embeddings (
            model text NOT NULL, edition text NOT NULL, revision text NOT NULL,
            passage_id text NOT NULL, embedding vector NOT NULL,
            PRIMARY KEY (model, edition, revision, passage_id))""")
        cur.executemany("""INSERT INTO event_support_agent_embeddings VALUES (%s,%s,%s,%s,%s::vector)
            ON CONFLICT (model, edition, revision, passage_id)
            DO UPDATE SET embedding = EXCLUDED.embedding""", rows)
    return len(rows)


# 詞彙與向量排名使用 RRF 融合；過期或缺失索引不能當成有效資料。
def hybrid_search(query, candidates, corpus, lexical, limit):
    """Fuse lexical and dense shortlists by rank within the same published edition."""
    model = model_name()
    by_id = {p["段落ID"]: p for p in candidates}
    depth = min(2 * limit, len(candidates))
    vector = embed([query], model)[0]
    with connect() as db:
        if db.execute("SELECT to_regclass('event_support_agent_embeddings')").fetchone()[0] is None:
            raise RuntimeError("資料庫尚未建立公告索引，請執行 python vector_search.py。")
        matches = db.execute("""
            SELECT passage_id, 1 - (embedding <=> %s::vector) AS similarity, count(*) OVER ()
            FROM event_support_agent_embeddings
            WHERE model = %s AND edition = %s AND revision = %s AND passage_id = ANY(%s)
            ORDER BY similarity DESC, passage_id LIMIT %s
            """, (json.dumps(vector), model, candidates[0]["檔期"], edition_hash(corpus),
                  list(by_id), depth)).fetchall()
    if not matches or matches[0][2] != len(candidates):
        raise RuntimeError("此模型與公告版本尚未建好向量索引，請執行 python vector_search.py。")

    ranks = {}
    for rank, passage in enumerate(lexical[:depth], start=1):
        ranks[passage["段落ID"]] = {"詞彙名次": rank, "BM25": passage["BM25"]}
    for rank, (pid, similarity, _) in enumerate(matches, start=1):
        ranks.setdefault(pid, {}).update({"向量名次": rank, "向量相似度": round(similarity, 5)})
    fused = []
    for pid, info in ranks.items():
        score = sum(1 / (60 + info[key]) for key in ("詞彙名次", "向量名次") if key in info)
        fused.append((score, pid, info))
    fused.sort(key=lambda item: (-item[0], item[1]))
    return [{**by_id[pid], **info, "RRF": round(score, 6)} for score, pid, info in fused[:limit]]


if __name__ == "__main__":
    print(f"已為 {model_name()} 建立 {build_index()} 個公開公告段落的向量。")
