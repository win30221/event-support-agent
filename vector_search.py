"""Multi-provider exact PostgreSQL/pgvector search for public notices."""
import argparse
import hashlib
import json

from database import connect
from embedding_client import EmbeddingError, embed, resolve_embedding, PROVIDERS
from guide import CATALOG, published_passages


def model_name():
    return resolve_embedding().model


def document_text(passage):
    return "\n".join(passage[k] for k in ("檔期", "文件", "發布日", "章節", "原文"))


def edition_hash(passages):
    documents = sorted((p["段落ID"], document_text(p)) for p in passages)
    return hashlib.sha256(json.dumps(documents, ensure_ascii=False).encode()).hexdigest()


# 向量索引只建立於公開公告；內容版本與嵌入模型是索引有效性的依據。
def build_index(catalog=CATALOG, *, settings=None):
    settings = settings or resolve_embedding()
    rows = []
    dimension = None
    for edition in catalog.editions:
        passages = published_passages(edition, catalog)
        fingerprint = edition_hash(passages)
        for start in range(0, len(passages), 16):
            batch = passages[start:start + 16]
            vectors = embed([document_text(p) for p in batch], settings=settings, input_type="passage")
            for p, vector in zip(batch, vectors):
                if dimension is None:
                    dimension = len(vector)
                if len(vector) != dimension:
                    raise EmbeddingError("Embedding dimensions changed during indexing.")
                rows.append((settings.provider, settings.model, edition, fingerprint,
                             p["段落ID"], json.dumps(vector)))
    with connect() as db, db.cursor() as cur:
        # First-time setup and re-indexes publish one complete database transaction.
        cur.execute("SELECT pg_advisory_xact_lock(721915034)")
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        cur.execute("""CREATE TABLE IF NOT EXISTS event_support_agent_embeddings (
            provider text NOT NULL DEFAULT 'ollama', model text NOT NULL,
            edition text NOT NULL, revision text NOT NULL,
            passage_id text NOT NULL, embedding vector NOT NULL,
            PRIMARY KEY (provider, model, edition, revision, passage_id))""")
        # Existing rows were all produced by Ollama. Migrate once in the same transaction.
        if not has_provider_column(cur):
            cur.execute("""ALTER TABLE event_support_agent_embeddings
                ADD COLUMN provider text NOT NULL DEFAULT 'ollama',
                DROP CONSTRAINT event_support_agent_embeddings_pkey,
                ADD PRIMARY KEY (provider, model, edition, revision, passage_id)""")
        cur.executemany("""INSERT INTO event_support_agent_embeddings
            (provider, model, edition, revision, passage_id, embedding)
            VALUES (%s,%s,%s,%s,%s,%s::vector)
            ON CONFLICT (provider, model, edition, revision, passage_id)
            DO UPDATE SET embedding = EXCLUDED.embedding""", rows)
    return len(rows)


def has_provider_column(db):
    return db.execute("""SELECT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = to_regclass('event_support_agent_embeddings')
        AND attname = 'provider' AND NOT attisdropped
        )""").fetchone()[0]


# 詞彙與向量排名使用 RRF 融合；過期或缺失索引不能當成有效資料。
def hybrid_search(query, candidates, corpus, lexical, limit, *, settings=None):
    """Fuse lexical and dense shortlists within one embedding space and edition."""
    settings = settings or resolve_embedding()
    by_id = {p["段落ID"]: p for p in candidates}
    depth = min(2 * limit, len(candidates))
    if not candidates:
        return []
    missing = f"Missing or stale embedding index; run python vector_search.py --provider {settings.provider}."
    with connect() as db:
        if db.execute("SELECT to_regclass('event_support_agent_embeddings')").fetchone()[0] is None:
            raise EmbeddingError(missing)
        scoped = has_provider_column(db)
        if not scoped and settings.provider != "ollama":
            raise EmbeddingError(missing)
        # Legacy Ollama-only table remains readable before its first migration.
        where = ("provider = %s AND " if scoped else "") + (
            "model = %s AND edition = %s AND revision = %s AND passage_id = ANY(%s)")
        args = ((settings.provider,) if scoped else ()) + (
            settings.model, candidates[0]["檔期"], edition_hash(corpus), list(by_id))
        count, min_dim, max_dim = db.execute(
            "SELECT count(*), min(vector_dims(embedding)), max(vector_dims(embedding)) "
            "FROM event_support_agent_embeddings WHERE " + where, args).fetchone()
        if count != len(candidates):
            raise EmbeddingError(missing)
        vector = embed([query], settings=settings, input_type="query")[0]
        if min_dim != max_dim or min_dim != len(vector):
            raise EmbeddingError("Embedding dimension mismatch; rebuild the selected model index.")
        matches = db.execute(
            "SELECT passage_id, CASE WHEN vector_dims(embedding) = %s "
            "THEN 1 - (embedding <=> %s::vector) END AS similarity, count(*) OVER () "
            "FROM event_support_agent_embeddings WHERE " + where +
            " ORDER BY similarity DESC NULLS LAST, passage_id LIMIT %s",
            (len(vector), json.dumps(vector), *args, depth)).fetchall()
    if not matches or matches[0][2] != len(candidates) or any(row[1] is None for row in matches):
        raise EmbeddingError(missing)

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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=PROVIDERS, help="Embedding provider to build")
    args = parser.parse_args()
    settings = resolve_embedding(selection=args.provider)
    print(f"Embedding: {settings.provider}/{settings.model}", flush=True)
    count = build_index(settings=settings)
    print(f"Indexed {count} public passages for {settings.provider}/{settings.model}.")
