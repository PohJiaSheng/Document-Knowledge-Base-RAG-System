"""Embedding service — SFR-Embedding-Mistral via OpenAI-compatible API with retry logic."""
from __future__ import annotations

import random
import time
from typing import Dict, Iterable, List, Optional

import httpx
import numpy as np
import openai

from config.llm import (
    BASE_RETRY_SECONDS,
    BASE_URL,
    CERT_PATH,
    EMBED_MODEL as EMBEDDING_MODEL,
    MAX_CHARS,
    MAX_RETRIES,
    MAX_RETRY_SECONDS,
    MIN_REQUEST_INTERVAL,
    OVERLAP,
    TOKEN,
)
from services.db_service import fetch_chunks_for_doc


def _build_client() -> openai.OpenAI:
    return openai.OpenAI(
        api_key=TOKEN,
        base_url=BASE_URL,
        default_headers={"Authorization": f"Basic {TOKEN}"},
        http_client=httpx.Client(verify=CERT_PATH),
    )


# ── Text helpers ──────────────────────────────────────────────────────────────

def chunk_text(text: str, max_chars: int = MAX_CHARS, overlap: int = OVERLAP) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    start = 0
    step = max(1, max_chars - overlap)
    while start < len(text):
        end = min(len(text), start + max_chars)
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end == len(text):
            break
        start += step
    return chunks


def l2_normalize(v: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(v)
    return v if norm == 0.0 else v / norm


def mean_pool(vectors: list[list[float]]) -> list[float]:
    arr = np.asarray(vectors, dtype=np.float32)
    return l2_normalize(np.mean(arr, axis=0)).astype(np.float32).tolist()


# ── Rate-limit helpers ────────────────────────────────────────────────────────

def _get_retry_after_seconds(exc: Exception) -> float | None:
    response = getattr(exc, "response", None)
    if response is None:
        return None
    retry_after = response.headers.get("retry-after")
    if not retry_after:
        return None
    try:
        value = float(retry_after)
        return value if value > 0 else None
    except (TypeError, ValueError):
        return None


def _sleep_for_rate_limit(min_interval: float, state: dict) -> None:
    if min_interval <= 0:
        return
    elapsed = time.monotonic() - state.get("last_request_ts", 0.0)
    remaining = min_interval - elapsed
    if remaining > 0:
        time.sleep(remaining)


# ── Embedding with retry ──────────────────────────────────────────────────────

def create_embedding_with_retry(
    client: openai.OpenAI,
    model: str,
    chunk: str,
    max_retries: int = MAX_RETRIES,
    base_retry: float = BASE_RETRY_SECONDS,
    max_retry: float = MAX_RETRY_SECONDS,
    min_interval: float = MIN_REQUEST_INTERVAL,
    rate_state: dict | None = None,
) -> list[float]:
    if rate_state is None:
        rate_state = {"last_request_ts": 0.0}
    for attempt in range(1, max_retries + 1):
        try:
            _sleep_for_rate_limit(min_interval, rate_state)
            resp = client.embeddings.create(model=model, input=chunk)
            rate_state["last_request_ts"] = time.monotonic()
            return resp.data[0].embedding
        except openai.RateLimitError as exc:
            rate_state["last_request_ts"] = time.monotonic()
            if attempt >= max_retries:
                raise
            wait = _get_retry_after_seconds(exc) or min(
                max_retry, base_retry * (2 ** (attempt - 1)) * (1 + random.uniform(0, 0.25))
            )
            print(f"  - Rate limit. Retry {attempt}/{max_retries} in {wait:.1f}s…")
            time.sleep(wait)
        except openai.APIStatusError as exc:
            rate_state["last_request_ts"] = time.monotonic()
            if exc.status_code not in {408, 409, 425, 500, 502, 503, 504} or attempt >= max_retries:
                raise
            wait = min(max_retry, base_retry * (2 ** (attempt - 1)) * (1 + random.uniform(0, 0.25)))
            print(f"  - API error {exc.status_code}. Retry {attempt}/{max_retries} in {wait:.1f}s…")
            time.sleep(wait)
        except (openai.APIConnectionError, openai.APITimeoutError):
            rate_state["last_request_ts"] = time.monotonic()
            if attempt >= max_retries:
                raise
            wait = min(max_retry, base_retry * (2 ** (attempt - 1)) * (1 + random.uniform(0, 0.25)))
            print(f"  - Network error. Retry {attempt}/{max_retries} in {wait:.1f}s…")
            time.sleep(wait)
    raise RuntimeError("Embedding failed after max retries")


def embed_single_text(
    client: openai.OpenAI,
    model: str,
    text: str,
    rate_state: dict | None = None,
) -> list[float]:
    if rate_state is None:
        rate_state = {"last_request_ts": 0.0}
    chunks = chunk_text(text)
    if not chunks:
        return []
    vectors = [
        create_embedding_with_retry(client, model, c, rate_state=rate_state)
        for c in chunks
    ]
    return mean_pool(vectors) if len(vectors) > 1 else l2_normalize(np.asarray(vectors[0], dtype=np.float32)).tolist()


# ── Document embedding pipeline ───────────────────────────────────────────────

def _split_multi(val: str | None) -> list[str] | None:
    """Split a comma-separated multi-value string into a list for Qdrant payload."""
    if not val:
        return None
    parts = [v.strip() for v in val.split(",") if v.strip()]
    return parts if parts else None


def embed_doc_chunks(doc_id: int) -> list[dict]:
    """Fetch all chunks for *doc_id*, embed them, and return records for Qdrant ingestion."""
    rows = fetch_chunks_for_doc(doc_id)
    if not rows:
        print(f"[EMBED] No chunks found for DOC_ID={doc_id}.")
        return []

    client = _build_client()
    rate_state: dict = {"last_request_ts": 0.0}
    records: list[dict] = []
    total = len(rows)

    for idx, row in enumerate(rows, start=1):
        title       = (row.get("TITLE")       or "").strip()
        content     = (row.get("CONTENT")     or "").strip()
        description = (row.get("DESCRIPTION") or "").strip()
        key_topics  = (row.get("KEY_TOPICS")  or "").strip()
        # Match test.py: embed_text = "{title}. {key_topics}. {content[:3000]}"
        combined    = f"{title}. {key_topics}. {content[:3000]}"

        print(f"[EMBED] Chunk {idx}/{total} (ID={row['ID']}, seq={row['CHUNK_SEQUENCE']})…")

        emb_content     = embed_single_text(client, EMBEDDING_MODEL, content,     rate_state)
        emb_description = embed_single_text(client, EMBEDDING_MODEL, description, rate_state)
        emb_combined    = embed_single_text(client, EMBEDDING_MODEL, combined,    rate_state)

        records.append({
            "id":                int(row["ID"]),
            "doc_id":            int(row["DOC_ID"]),
            "chunk_sequence":    int(row["CHUNK_SEQUENCE"])    if row.get("CHUNK_SEQUENCE")    is not None else None,
            "content":           content,
            "description":       description,
            "img_url_path":      row.get("IMG_URL_PATH"),
            "process":           row.get("PROCESS"),
            # Module / eq_type / package may be multi-value (comma-separated); split to list for Qdrant
            "module":            _split_multi(row.get("MODULE")),
            "equipment_type":    _split_multi(row.get("EQ_TYPE")),
            "package":           _split_multi(row.get("PACKAGE")),
            "embedding_content":     emb_content,
            "embedding_description": emb_description,
            "embedding_combined":    emb_combined,
        })

    print(f"[EMBED] {total} chunks embedded for DOC_ID={doc_id}.")
    return records
