"""Qdrant service — collection management, ingestion, and semantic search."""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

from config.llm import (
    COLLECTION,
    EXPAND_MAX,
    MAX_CONTEXT_PAGES,
    QDRANT_KEY,
    QDRANT_URL,
    SIM_THRESHOLD,
    TOP_K,
    WINDOW_HARD,
)

_DEFAULT_VECTOR_SIZE = 4096
_DISTANCE = qm.Distance.COSINE

_qdrant_client: QdrantClient | None = None


def get_qdrant_client() -> QdrantClient:
    global _qdrant_client
    if _qdrant_client is None:
        _qdrant_client = QdrantClient(
            url=QDRANT_URL,
            port=None,
            api_key=QDRANT_KEY,
            verify=False,
            check_compatibility=False,
        )
    return _qdrant_client


# ── Collection management ─────────────────────────────────────────────────────

def ensure_collection(vector_size: int = _DEFAULT_VECTOR_SIZE) -> None:
    client = get_qdrant_client()
    existing = {c.name for c in client.get_collections().collections}
    if COLLECTION not in existing:
        print(f"[QDRANT] Creating collection '{COLLECTION}' (size={vector_size})…")
        client.create_collection(
            collection_name=COLLECTION,
            vectors_config=qm.VectorParams(size=vector_size, distance=_DISTANCE),
        )
        for field in ("process", "module", "equipment_type", "package"):
            client.create_payload_index(
                collection_name=COLLECTION,
                field_name=field,
                field_schema=qm.PayloadSchemaType.KEYWORD,
            )
        client.create_payload_index(
            collection_name=COLLECTION,
            field_name="doc_id",
            field_schema=qm.PayloadSchemaType.INTEGER,
        )
        print(f"[QDRANT] Collection '{COLLECTION}' created with payload indexes.")
    else:
        # Ensure doc_id index exists on collections created before this was added
        try:
            client.create_payload_index(
                collection_name=COLLECTION,
                field_name="doc_id",
                field_schema=qm.PayloadSchemaType.INTEGER,
            )
            print(f"[QDRANT] Created doc_id payload index on existing collection.")
        except Exception:
            pass  # Index already exists — that's fine
        print(f"[QDRANT] Collection '{COLLECTION}' already exists.")


# ── Ingestion ─────────────────────────────────────────────────────────────────

def ingest_to_qdrant(records: list[dict], batch_size: int = 100) -> None:
    """Upsert embedded records into the Qdrant collection.

    Each record must have: id (int), embedding_combined (list[float]).
    Payload stored: doc_id, chunk_sequence, process, module, equipment_type, package.
    All other fields (content, description, img_url_path) are fetched from Oracle at query time.
    """
    if not records:
        print("[QDRANT] No records to ingest.")
        return

    vector_size = _DEFAULT_VECTOR_SIZE
    for r in records:
        vec = r.get("embedding_combined") or []
        if vec:
            vector_size = len(vec)
            break

    ensure_collection(vector_size)
    client = get_qdrant_client()

    for start in range(0, len(records), batch_size):
        batch = records[start : start + batch_size]
        points = []
        for r in batch:
            vec = r.get("embedding_combined") or []
            if not vec:
                print(f"[QDRANT] Skipping record id={r.get('id')} — no embedding.")
                continue
            payload = {
                k: r.get(k)
                for k in (
                    "doc_id", "chunk_sequence", "process", "module", "equipment_type", "package",
                )
            }
            points.append(qm.PointStruct(id=int(r["id"]), vector=vec, payload=payload))
        if points:
            client.upsert(collection_name=COLLECTION, points=points)
            print(f"[QDRANT] Upserted {len(points)} points (batch {start // batch_size + 1}).")

    print(f"[QDRANT] Ingestion complete — {len(records)} records processed.")


# ── Semantic search ───────────────────────────────────────────────────────────

def _qdrant_search(
    query_vec: np.ndarray,
    top_k: int = TOP_K,
    filters: qm.Filter | None = None,
) -> list[dict]:
    """Return top-k payload dicts from Qdrant for the given query vector."""
    client = get_qdrant_client()
    results = client.query_points(
        collection_name=COLLECTION,
        query=query_vec.tolist(),
        limit=top_k,
        with_payload=True,
        with_vectors=False,
        query_filter=filters,
    )
    point_list = results.points if hasattr(results, "points") else results
    print(f"[SEARCH] Qdrant returned {len(point_list)} result(s) (top_k={top_k})")
    hits = []
    for r in point_list:
        p = dict(r.payload or {})
        p["_score"] = r.score
        p["_id"]    = r.id
        hits.append(p)
        print(
            f"  → id={r.id}  score={r.score:.4f}"
            f"  process={p.get('process')}  module={p.get('module')}"
            f"  equipment_type={p.get('equipment_type')}  package={p.get('package')}"
        )
    return hits


def build_context_chunks(
    query_vec: np.ndarray,
    filters: qm.Filter | None = None,
    top_k: int = TOP_K,
) -> list[dict]:
    """Filter + top-k semantic search, returning up to MAX_CONTEXT_PAGES chunks."""
    hits = _qdrant_search(query_vec, top_k=top_k, filters=filters)
    if not hits:
        print("[CONTEXT] No matches found.")
        return []
    chunks = hits[:MAX_CONTEXT_PAGES]
    print(f"[CONTEXT] Returning {len(chunks)} chunk(s)")
    return chunks
