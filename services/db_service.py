"""Database service — CRUD operations for KNOWLEDGE_DOCS, KNOWLEDGE_DOC_PROFILES, and KNOWLEDGE_CHUNKS_FROM_DOCS."""
from __future__ import annotations

from pathlib import Path

import oracledb
import pandas as pd

from config.db import get_conn

# ── Documents ─────────────────────────────────────────────────────────────────

def insert_knowledge_document(
    filepath: str,
    file_type: str,
    process: str | None = None,
    module: str | None = None,
    eq_type: str | None = None,
    package: str | None = None,
    title: str | None = None,
) -> int | None:
    """Insert a document record and return the generated ID, or None on failure."""
    conn = None
    cursor = None
    try:
        conn = get_conn()
        cursor = conn.cursor()
        new_id_var = cursor.var(oracledb.NUMBER)
        cursor.execute(
            """
            INSERT INTO KNOWLEDGE_DOCS (
                FILEPATH, CREATED_AT, UPDATED_AT,
                FILE_TYPE, PROCESS, MODULE, EQ_TYPE, PACKAGE, TITLE
            ) VALUES (
                :filepath, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP,
                :file_type, :process, :module, :eq_type, :package, :title
            ) RETURNING ID INTO :new_id
            """,
            {
                "filepath": filepath, "file_type": file_type, "process": process,
                "module": module, "eq_type": eq_type, "package": package,
                "title": title, "new_id": new_id_var,
            },
        )
        conn.commit()
        return int(new_id_var.getvalue()[0])
    except Exception as exc:
        print(f"[DB] insert_knowledge_document failed: {exc}")
        return None
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def filepath_exists_in_db(filepath: str) -> bool:
    """Return True if KNOWLEDGE_DOCS already has a row with this FILEPATH."""
    conn = None
    cursor = None
    try:
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM KNOWLEDGE_DOCS WHERE FILEPATH = :fp",
            {"fp": filepath},
        )
        return cursor.fetchone() is not None
    except Exception as exc:
        print(f"[DB] filepath_exists_in_db failed: {exc}")
        return False
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def insert_knowledge_chunk(
    doc_id: int,
    chunk_sequence: int,
    content: str,
    description: str,
    img_url_path: str,
    title: str | None = None,
    page_start: int | None = None,
    page_end: int | None = None,
    content_type: str | None = None,
    section_path: str | None = None,
    key_topics: str | None = None,
    keywords: str | None = None,
    is_table: int = 0,
    table_summary: str | None = None,
    continuity_type: str | None = None,
    is_continuation: int = 0,
    prev_chunk_id: int | None = None,
    next_chunk_id: int | None = None,
    topic_group_id: int | None = None,
    sequential_order: int | None = None,
    related_chunk_ids: str | None = None,
    topic_complete: int = 1,
    continuity_notes: str | None = None,
) -> int | None:
    """Insert a chunk record and return the generated ID, or None on failure."""
    conn = None
    cursor = None
    try:
        conn = get_conn()
        cursor = conn.cursor()
        cursor.setinputsizes(content=oracledb.DB_TYPE_CLOB, description=oracledb.DB_TYPE_CLOB)
        new_id_var = cursor.var(oracledb.NUMBER)
        cursor.execute(
            """
            INSERT INTO KNOWLEDGE_CHUNKS_FROM_DOCS (
                DOC_ID, CHUNK_SEQUENCE, CREATED_AT, UPDATED_AT,
                TITLE, CONTENT, DESCRIPTION, IMG_URL_PATH,
                PAGE_START, PAGE_END, CONTENT_TYPE, SECTION_PATH,
                KEY_TOPICS, KEYWORDS, IS_TABLE, TABLE_SUMMARY,
                CONTINUITY_TYPE, IS_CONTINUATION,
                PREV_CHUNK_ID, NEXT_CHUNK_ID, TOPIC_GROUP_ID,
                SEQUENTIAL_ORDER, RELATED_CHUNK_IDS, TOPIC_COMPLETE, CONTINUITY_NOTES
            ) VALUES (
                :doc_id, :chunk_sequence, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP,
                :title, :content, :description, :img_url_path,
                :page_start, :page_end, :content_type, :section_path,
                :key_topics, :keywords, :is_table, :table_summary,
                :continuity_type, :is_continuation,
                :prev_chunk_id, :next_chunk_id, :topic_group_id,
                :sequential_order, :related_chunk_ids, :topic_complete, :continuity_notes
            ) RETURNING ID INTO :new_id
            """,
            {
                "doc_id": doc_id, "chunk_sequence": chunk_sequence,
                "title": title, "content": content, "description": description,
                "img_url_path": img_url_path,
                "page_start": page_start, "page_end": page_end,
                "content_type": content_type, "section_path": section_path,
                "key_topics": key_topics, "keywords": keywords,
                "is_table": is_table, "table_summary": table_summary,
                "continuity_type": continuity_type, "is_continuation": is_continuation,
                "prev_chunk_id": prev_chunk_id, "next_chunk_id": next_chunk_id,
                "topic_group_id": topic_group_id,
                "sequential_order": sequential_order,
                "related_chunk_ids": related_chunk_ids,
                "topic_complete": topic_complete,
                "continuity_notes": continuity_notes,
                "new_id": new_id_var,
            },
        )
        conn.commit()
        return int(new_id_var.getvalue()[0])
    except Exception as exc:
        print(f"[DB] insert_knowledge_chunk failed: {exc}")
        return None
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def update_chunk_links(
    chunk_id: int,
    prev_chunk_id: int | None,
    next_chunk_id: int | None,
    sequential_order: int | None = None,
    related_chunk_ids: str | None = None,
    topic_complete: int | None = None,
) -> bool:
    """Update PREV_CHUNK_ID, NEXT_CHUNK_ID, and relationship fields for an existing chunk."""
    conn = None
    cursor = None
    try:
        conn = get_conn()
        cursor = conn.cursor()
        sets = [
            "PREV_CHUNK_ID = :prev_id",
            "NEXT_CHUNK_ID = :next_id",
            "UPDATED_AT    = CURRENT_TIMESTAMP",
        ]
        params: dict = {"prev_id": prev_chunk_id, "next_id": next_chunk_id, "chunk_id": chunk_id}
        if sequential_order is not None:
            sets.append("SEQUENTIAL_ORDER = :seq_order")
            params["seq_order"] = sequential_order
        if related_chunk_ids is not None:
            sets.append("RELATED_CHUNK_IDS = :related")
            params["related"] = related_chunk_ids
        if topic_complete is not None:
            sets.append("TOPIC_COMPLETE = :topic_complete")
            params["topic_complete"] = topic_complete
        cursor.execute(
            f"""
            UPDATE KNOWLEDGE_CHUNKS_FROM_DOCS
               SET {', '.join(sets)}
             WHERE ID = :chunk_id
            """,
            params,
        )
        conn.commit()
        return True
    except Exception as exc:
        print(f"[DB] update_chunk_links failed (chunk_id={chunk_id}): {exc}")
        return False
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def get_knowledge_documents() -> pd.DataFrame:
    """Return all KNOWLEDGE_DOCS rows as a DataFrame (used by DMS UI)."""
    conn = None
    cur = None
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT * FROM KNOWLEDGE_DOCS ORDER BY CREATED_AT DESC")
        columns = [d[0] for d in cur.description]
        rows = cur.fetchall()
        print(f"[DB] Fetched {len(rows)} documents.")
        return pd.DataFrame(rows, columns=columns)
    except Exception as exc:
        print(f"[DB] get_knowledge_documents failed: {exc}")
        return pd.DataFrame()
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def fetch_knowledge_documents() -> list[dict]:
    """Return all KNOWLEDGE_DOCS rows (with profile description) as list[dict]."""
    conn = None
    cur = None
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT kd.ID, kd.FILEPATH, kd.FILE_TYPE, kd.PROCESS, kd.MODULE, "
            "kd.EQ_TYPE, kd.PACKAGE, kd.CREATED_AT, kp.DESCRIPTION "
            "FROM KNOWLEDGE_DOCS kd "
            "LEFT JOIN KNOWLEDGE_DOC_PROFILES kp ON kd.ID = kp.DOC_ID "
            "ORDER BY kd.CREATED_AT DESC"
        )
        cols = [d[0] for d in cur.description]
        rows = []
        for row in cur.fetchall():
            d = dict(zip(cols, row))
            for k, v in d.items():
                if hasattr(v, "isoformat"):
                    d[k] = v.isoformat()
                elif v is None:
                    d[k] = None
                else:
                    d[k] = str(v) if not isinstance(v, (int, float, str, bool)) else v
            rows.append(d)
        return rows
    except Exception as exc:
        print(f"[DB] fetch_knowledge_documents failed: {exc}")
        return []
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def fetch_img_urls_from_db(point_ids: list) -> dict:
    """Fetch IMG_URL_PATH from KNOWLEDGE_CHUNKS_FROM_DOCS for the given Qdrant point IDs."""
    if not point_ids:
        return {}
    conn = None
    cur = None
    try:
        conn = get_conn()
        cur = conn.cursor()
        placeholders = ", ".join(f":id{i}" for i in range(len(point_ids)))
        bind_vars = {f"id{i}": pid for i, pid in enumerate(point_ids)}
        cur.execute(
            f"SELECT ID, IMG_URL_PATH FROM KNOWLEDGE_CHUNKS_FROM_DOCS WHERE ID IN ({placeholders})",
            bind_vars,
        )
        result = {row[0]: row[1] for row in cur.fetchall()}
        print(f"[DB] fetch_img_urls_from_db: {len(result)} row(s)")
        return result
    except Exception as exc:
        print(f"[DB] fetch_img_urls_from_db failed: {exc}")
        return {}
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def fetch_chunk_details_by_ids(chunk_ids: list) -> dict:
    """Fetch CONTENT, DESCRIPTION, and IMG_URL_PATH from Oracle for the given chunk IDs.

    Returns {chunk_id: {"content": ..., "description": ..., "img_url_path": ...}}.
    Used to enrich Qdrant search results, which no longer store these fields in their payload.
    """
    if not chunk_ids:
        return {}
    conn = None
    cur = None
    try:
        conn = get_conn()
        cur = conn.cursor()
        placeholders = ", ".join(f":id{i}" for i in range(len(chunk_ids)))
        bind_vars = {f"id{i}": cid for i, cid in enumerate(chunk_ids)}
        cur.execute(
            f"SELECT ID, CONTENT, DESCRIPTION, IMG_URL_PATH, PAGE_START, PAGE_END "
            f"FROM KNOWLEDGE_CHUNKS_FROM_DOCS WHERE ID IN ({placeholders})",
            bind_vars,
        )
        result = {}
        for row in cur.fetchall():
            cid, content, description, img_url_path, page_start, page_end = row
            # Read CLOBs if returned as LOB objects
            if content is not None and hasattr(content, "read"):
                content = content.read()
            if description is not None and hasattr(description, "read"):
                description = description.read()
            result[cid] = {
                "content":      content,
                "description":  description,
                "img_url_path": img_url_path,
                "page_start":   page_start,
                "page_end":     page_end,
            }
        print(f"[DB] fetch_chunk_details_by_ids: {len(result)} row(s)")
        return result
    except Exception as exc:
        print(f"[DB] fetch_chunk_details_by_ids failed: {exc}")
        return {}
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def query_matching_filenames(filters: dict) -> list[str] | None:
    """Return basenames of KNOWLEDGE_DOCS rows that match the active filters.

    filters = { "process": [...], "module": [...], "eq_type": [...], "package": [...] }
    Column mapping: eq_type → EQ_TYPE, others uppercase of key.

    Logic:
      - Categories with no selections (Any) are ignored entirely.
      - Each selected category becomes an AND condition (all must match).
      - Multiple values within one category are OR'd: e.g. MODULE IN ('LPL','DSO').
    Returns None when no filter values are selected (caller should show all files).
    """
    col_map = {"process": "PROCESS", "module": "MODULE", "eq_type": "EQ_TYPE", "package": "PACKAGE"}
    conditions = []
    bind_vars: dict = {}
    idx = 0
    for key, col in col_map.items():
        vals = filters.get(key) or []
        if not vals:
            continue  # Any → skip this category
        placeholders = []
        for v in vals:
            bind_vars[f"f{idx}"] = str(v)
            placeholders.append(f":f{idx}")
            idx += 1
        # OR within a category, AND across categories
        conditions.append(f"{col} IN ({', '.join(placeholders)})")

    if not conditions:
        return None  # no filters active → show everything

    sql = f"SELECT FILEPATH FROM KNOWLEDGE_DOCS WHERE {' AND '.join(conditions)}"
    conn = None
    cur = None
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(sql, bind_vars)
        rows = cur.fetchall()
        # Return the filename portion only (last segment of the path)
        return [Path(row[0]).name for row in rows if row[0]]
    except Exception as exc:
        print(f"[DB] query_matching_filenames failed: {exc}")
        return []
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def get_doc_title(doc_id: int) -> str | None:
    """Return the user-entered TITLE for a document, or None."""
    conn = None
    cursor = None
    try:
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT TITLE FROM KNOWLEDGE_DOCS WHERE ID = :id", {"id": doc_id})
        row = cursor.fetchone()
        return row[0] if row else None
    except Exception as exc:
        print(f"[DB] get_doc_title failed: {exc}")
        return None
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def fetch_chunks_for_doc(doc_id: int) -> list[dict]:
    """Return KNOWLEDGE_CHUNKS_FROM_DOCS joined with KNOWLEDGE_DOCS metadata for a doc."""
    conn = None
    cur = None
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT kc.ID, kc.DOC_ID, kc.CHUNK_SEQUENCE, kc.TITLE,
                   kc.CONTENT, kc.DESCRIPTION, kc.IMG_URL_PATH,
                   kc.PAGE_START, kc.PAGE_END, kc.CONTENT_TYPE,
                   kc.KEY_TOPICS, kc.KEYWORDS, kc.IS_TABLE,
                   kc.PREV_CHUNK_ID, kc.NEXT_CHUNK_ID, kc.TOPIC_GROUP_ID,
                   kd.PROCESS, kd.MODULE, kd.EQ_TYPE, kd.PACKAGE
            FROM KNOWLEDGE_CHUNKS_FROM_DOCS kc
            LEFT JOIN KNOWLEDGE_DOCS kd ON kc.DOC_ID = kd.ID
            WHERE kc.DOC_ID = :doc_id
            ORDER BY kc.CHUNK_SEQUENCE
            """,
            {"doc_id": doc_id},
        )
        columns = [d[0] for d in cur.description]
        rows = []
        for db_row in cur.fetchall():
            row = dict(zip(columns, db_row))
            for col in ("CONTENT", "DESCRIPTION", "KEY_TOPICS", "KEYWORDS"):
                v = row.get(col)
                if v is not None and hasattr(v, "read"):
                    row[col] = v.read()
            rows.append(row)
        print(f"[DB] Fetched {len(rows)} chunks for DOC_ID={doc_id}.")
        return rows
    except Exception as exc:
        print(f"[DB] fetch_chunks_for_doc failed: {exc}")
        return []
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def get_last_doc_id() -> int | None:
    """Return the ID of the most recently inserted KNOWLEDGE_DOCS row."""
    conn = None
    cur = None
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT ID FROM KNOWLEDGE_DOCS ORDER BY CREATED_AT DESC FETCH FIRST 1 ROW ONLY")
        row = cur.fetchone()
        return int(row[0]) if row else None
    except Exception as exc:
        print(f"[DB] get_last_doc_id failed: {exc}")
        return None
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def fetch_doc_filepaths(doc_ids: list) -> dict:
    """Return {doc_id: filepath} for the given list of KNOWLEDGE_DOCS IDs."""
    if not doc_ids:
        return {}
    conn = None
    cur = None
    try:
        conn = get_conn()
        cur = conn.cursor()
        placeholders = ", ".join(f":id{i}" for i in range(len(doc_ids)))
        bind_vars = {f"id{i}": did for i, did in enumerate(doc_ids)}
        cur.execute(
            f"SELECT ID, FILEPATH FROM KNOWLEDGE_DOCS WHERE ID IN ({placeholders})",
            bind_vars,
        )
        return {row[0]: row[1] for row in cur.fetchall()}
    except Exception as exc:
        print(f"[DB] fetch_doc_filepaths failed: {exc}")
        return {}
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def insert_knowledge_doc_profile(
    doc_id: int,
    doc_name: str,
    description: str | None = None,
    key_topics: str | None = None,
    key_entities: str | None = None,
    unique_keywords: str | None = None,
    shared_keywords: str | None = None,
    title: str | None = None,
    purpose: str | None = None,
    domain: str | None = None,
    document_type: str | None = None,
    differentiators: str | None = None,
) -> bool:
    """Insert a document profile record into KNOWLEDGE_DOC_PROFILES."""
    conn = None
    cursor = None
    try:
        conn = get_conn()
        cursor = conn.cursor()
        cursor.setinputsizes(description=oracledb.DB_TYPE_CLOB, differentiators=oracledb.DB_TYPE_CLOB)
        cursor.execute(
            """
            INSERT INTO KNOWLEDGE_DOC_PROFILES (
                DOC_ID, DOC_NAME, DESCRIPTION,
                KEY_TOPICS, KEY_ENTITIES, UNIQUE_KEYWORDS, SHARED_KEYWORDS,
                TITLE, PURPOSE, DOMAIN, DOCUMENT_TYPE, DIFFERENTIATORS
            ) VALUES (
                :doc_id, :doc_name, :description,
                :key_topics, :key_entities, :unique_keywords, :shared_keywords,
                :title, :purpose, :domain, :document_type, :differentiators
            )
            """,
            {
                "doc_id": doc_id, "doc_name": doc_name, "description": description,
                "key_topics": key_topics, "key_entities": key_entities,
                "unique_keywords": unique_keywords, "shared_keywords": shared_keywords,
                "title": title, "purpose": purpose, "domain": domain,
                "document_type": document_type, "differentiators": differentiators,
            },
        )
        conn.commit()
        return True
    except Exception as exc:
        print(f"[DB] insert_knowledge_doc_profile failed: {exc}")
        return False
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


# ── Retrieval helpers ─────────────────────────────────────────────────────────

def fetch_doc_profiles_for_retrieval(
    process: list[str] | None = None,
    module: list[str] | None = None,
    eq_type: list[str] | None = None,
    package: list[str] | None = None,
) -> list[dict]:
    """Fetch doc profiles joined with KNOWLEDGE_DOCS for document scoring.

    Returns list of dicts with keys:
      id, filepath, process, module, eq_type, package,
      description, key_topics, key_entities, unique_keywords, shared_keywords.
    Optional filter args narrow to docs matching those metadata values.
    """
    conn = None
    cur = None
    try:
        conn = get_conn()
        cur = conn.cursor()
        conditions: list[str] = []
        bind_vars: dict = {}
        idx = 0
        for col, vals in [("kd.PROCESS", process), ("kd.MODULE", module),
                          ("kd.EQ_TYPE", eq_type), ("kd.PACKAGE", package)]:
            if vals:
                phs = [f":f{idx + j}" for j in range(len(vals))]
                bind_vars.update({f"f{idx + j}": v for j, v in enumerate(vals)})
                idx += len(vals)
                conditions.append(f"{col} IN ({', '.join(phs)})")
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        cur.execute(
            f"SELECT kd.ID, kd.FILEPATH, kd.PROCESS, kd.MODULE, kd.EQ_TYPE, kd.PACKAGE, "
            f"kp.DESCRIPTION, kp.KEY_TOPICS, kp.KEY_ENTITIES, kp.UNIQUE_KEYWORDS, kp.SHARED_KEYWORDS, "
            f"kp.TITLE, kp.PURPOSE, kp.DOMAIN, kp.DOCUMENT_TYPE, kp.DIFFERENTIATORS "
            f"FROM KNOWLEDGE_DOCS kd "
            f"LEFT JOIN KNOWLEDGE_DOC_PROFILES kp ON kd.ID = kp.DOC_ID "
            f"{where} ORDER BY kd.CREATED_AT DESC",
            bind_vars,
        )
        col_names = ["id", "filepath", "process", "module", "eq_type", "package",
                     "description", "key_topics", "key_entities", "unique_keywords", "shared_keywords",
                     "title", "purpose", "domain", "document_type", "differentiators"]
        rows = []
        for row in cur.fetchall():
            d = dict(zip(col_names, row))
            for cn in ("description", "key_topics", "key_entities", "unique_keywords", "shared_keywords", "differentiators"):
                v = d.get(cn)
                if v is not None and hasattr(v, "read"):
                    d[cn] = v.read()
            rows.append(d)
        print(f"[DB] fetch_doc_profiles_for_retrieval: {len(rows)} profile(s)")
        return rows
    except Exception as exc:
        print(f"[DB] fetch_doc_profiles_for_retrieval failed: {exc}")
        return []
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def fetch_chunks_extended_by_ids(chunk_ids: list) -> dict:
    """Fetch full chunk metadata for neighbor expansion and context building.

    Returns {chunk_id: {...}} with keys:
      doc_id, chunk_sequence, content, description, img_url_path,
      page_start, page_end, prev_chunk_id, next_chunk_id, topic_group_id,
      is_continuation, key_topics, section_path, is_table, table_summary, title.
    """
    if not chunk_ids:
        return {}
    conn = None
    cur = None
    try:
        conn = get_conn()
        cur = conn.cursor()
        placeholders = ", ".join(f":id{i}" for i in range(len(chunk_ids)))
        bind_vars = {f"id{i}": cid for i, cid in enumerate(chunk_ids)}
        cur.execute(
            f"SELECT ID, DOC_ID, CHUNK_SEQUENCE, CONTENT, DESCRIPTION, IMG_URL_PATH, "
            f"PAGE_START, PAGE_END, PREV_CHUNK_ID, NEXT_CHUNK_ID, TOPIC_GROUP_ID, "
            f"IS_CONTINUATION, KEY_TOPICS, SECTION_PATH, IS_TABLE, TABLE_SUMMARY, TITLE, "
            f"SEQUENTIAL_ORDER, RELATED_CHUNK_IDS, TOPIC_COMPLETE, CONTINUITY_TYPE, CONTINUITY_NOTES "
            f"FROM KNOWLEDGE_CHUNKS_FROM_DOCS WHERE ID IN ({placeholders})",
            bind_vars,
        )
        result = {}
        for row in cur.fetchall():
            (cid, doc_id, chunk_seq, content, description, img_url_path,
             page_start, page_end, prev_chunk_id, next_chunk_id, topic_group_id,
             is_continuation, key_topics, section_path, is_table, table_summary, title,
             sequential_order, related_chunk_ids, topic_complete, continuity_type, continuity_notes) = row
            if content is not None and hasattr(content, "read"):
                content = content.read()
            if description is not None and hasattr(description, "read"):
                description = description.read()
            if key_topics is not None and hasattr(key_topics, "read"):
                key_topics = key_topics.read()
            if section_path is not None and hasattr(section_path, "read"):
                section_path = section_path.read()
            result[cid] = {
                "doc_id":            doc_id,
                "chunk_sequence":    chunk_seq,
                "content":           content,
                "description":       description,
                "img_url_path":      img_url_path,
                "page_start":        page_start,
                "page_end":          page_end,
                "prev_chunk_id":     prev_chunk_id,
                "next_chunk_id":     next_chunk_id,
                "topic_group_id":    topic_group_id,
                "is_continuation":   bool(is_continuation),
                "key_topics":        key_topics,
                "section_path":      section_path,
                "is_table":          bool(is_table),
                "table_summary":     table_summary,
                "title":             title,
                "sequential_order":  sequential_order,
                "related_chunk_ids": related_chunk_ids,
                "topic_complete":    bool(topic_complete) if topic_complete is not None else True,
                "continuity_type":   continuity_type,
                "continuity_notes":  continuity_notes,
            }
        print(f"[DB] fetch_chunks_extended_by_ids: {len(result)} row(s)")
        return result
    except Exception as exc:
        print(f"[DB] fetch_chunks_extended_by_ids failed: {exc}")
        return {}
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


def fetch_chunks_by_topic_groups(topic_group_ids: list, doc_ids: list) -> list[dict]:
    """Fetch all chunks belonging to given topic_group_ids within the given doc_ids.

    Returns list of dicts ordered by (DOC_ID, CHUNK_SEQUENCE), with keys:
      id, doc_id, chunk_sequence, page_start, page_end,
      prev_chunk_id, next_chunk_id, topic_group_id, is_continuation,
      title, content, description, img_url_path, is_table, table_summary.
    """
    if not topic_group_ids or not doc_ids:
        return []
    conn = None
    cur = None
    try:
        conn = get_conn()
        cur = conn.cursor()
        tg_ph = ", ".join(f":tg{i}" for i in range(len(topic_group_ids)))
        dc_ph = ", ".join(f":dc{i}" for i in range(len(doc_ids)))
        bind_vars = {f"tg{i}": tgid for i, tgid in enumerate(topic_group_ids)}
        bind_vars.update({f"dc{i}": did for i, did in enumerate(doc_ids)})
        cur.execute(
            f"SELECT ID, DOC_ID, CHUNK_SEQUENCE, PAGE_START, PAGE_END, "
            f"PREV_CHUNK_ID, NEXT_CHUNK_ID, TOPIC_GROUP_ID, IS_CONTINUATION, "
            f"TITLE, CONTENT, DESCRIPTION, IMG_URL_PATH, IS_TABLE, TABLE_SUMMARY, "
            f"SEQUENTIAL_ORDER, CONTINUITY_TYPE, CONTINUITY_NOTES, KEY_TOPICS, SECTION_PATH "
            f"FROM KNOWLEDGE_CHUNKS_FROM_DOCS "
            f"WHERE TOPIC_GROUP_ID IN ({tg_ph}) AND DOC_ID IN ({dc_ph}) "
            f"ORDER BY DOC_ID, CHUNK_SEQUENCE",
            bind_vars,
        )
        rows = []
        for row in cur.fetchall():
            (cid, doc_id, chunk_seq, page_start, page_end,
             prev_chunk_id, next_chunk_id, topic_group_id, is_continuation,
             title, content, description, img_url_path, is_table, table_summary,
             sequential_order, continuity_type, continuity_notes, key_topics, section_path) = row
            if content is not None and hasattr(content, "read"):
                content = content.read()
            if description is not None and hasattr(description, "read"):
                description = description.read()
            if key_topics is not None and hasattr(key_topics, "read"):
                key_topics = key_topics.read()
            if section_path is not None and hasattr(section_path, "read"):
                section_path = section_path.read()
            rows.append({
                "id":               cid,
                "doc_id":           doc_id,
                "chunk_sequence":   chunk_seq,
                "page_start":       page_start,
                "page_end":         page_end,
                "prev_chunk_id":    prev_chunk_id,
                "next_chunk_id":    next_chunk_id,
                "topic_group_id":   topic_group_id,
                "is_continuation":  bool(is_continuation),
                "title":            title,
                "content":          content,
                "description":      description,
                "img_url_path":     img_url_path,
                "is_table":         bool(is_table),
                "table_summary":    table_summary,
                "sequential_order": sequential_order,
                "continuity_type":  continuity_type,
                "continuity_notes": continuity_notes,
                "key_topics":       key_topics,
                "section_path":     section_path,
            })
        print(f"[DB] fetch_chunks_by_topic_groups: {len(rows)} row(s)")
        return rows
    except Exception as exc:
        print(f"[DB] fetch_chunks_by_topic_groups failed: {exc}")
        return []
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()
