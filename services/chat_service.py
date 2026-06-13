"""Chat service — query embedding, answer generation, and suggestion generation."""
from __future__ import annotations

import json
import os
import re
import traceback
from collections import defaultdict
from pathlib import Path

import httpx
import numpy as np
import openai
from qdrant_client.http import models as qm

from config.llm import (
    BASE_URL, CERT_PATH, CHAT_MODEL, EMBED_MODEL, TOKEN,
    TEMP_ANSWERING, TEMP_DOC_SCORING, TEMP_QUERY_REWRITE, TOP_K, MAX_CONTEXT_PAGES,
)
from services.db_service import (
    fetch_chunk_details_by_ids,
    fetch_chunks_by_topic_groups,
    fetch_chunks_extended_by_ids,
    fetch_doc_filepaths,
    fetch_doc_profiles_for_retrieval,
)
from services.qdrant_service import build_context_chunks
from services.share_service import read_image_from_share

# ── Debug flags ────────────────────────────────────────────────────────────────
DEBUG_RELATED = os.getenv("DEBUG_RELATED", "false").lower() in ("1", "true", "yes")

_openai_client: openai.OpenAI | None = None


def get_openai_client() -> openai.OpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = openai.OpenAI(
            api_key=TOKEN,
            base_url=BASE_URL,
            default_headers={"Authorization": f"Basic {TOKEN}"},
            http_client=httpx.Client(verify=CERT_PATH),
        )
    return _openai_client


# ── Embedding ─────────────────────────────────────────────────────────────────

def l2_normalize(v: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(v)
    return v if norm == 0.0 else v / norm


def embed_query(text: str) -> np.ndarray:
    """Embed a user query and return an L2-normalised float32 array."""
    print(f"[EMBED] Query ({len(text)} chars) with {EMBED_MODEL}…")
    client = get_openai_client()
    resp = client.embeddings.create(model=EMBED_MODEL, input=text.strip())
    vec = l2_normalize(np.asarray(resp.data[0].embedding, dtype=np.float32))
    print(f"[EMBED] Done — dim={len(vec)}")
    return vec


# ── Message building ──────────────────────────────────────────────────────────

def _mime_from_path(path: str) -> str:
    ext = Path(path).suffix.lower()
    return {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".bmp": "image/bmp",  ".gif":  "image/gif",  ".webp": "image/webp",
    }.get(ext, "image/png")


def build_chat_messages(
    question: str,
    context_chunks: list[dict],
    differentiator_block: str = "",
) -> list[dict]:
    system_msg = {
        "role": "system",
        "content": (
            "You are a knowledgeable process engineering assistant for semiconductor manufacturing. "
            "Answer questions accurately and specifically based on the provided document pages. "
            "If the context does not contain enough information, say so clearly. "
            "Use markdown for structured answers. When referencing parameters or steps, be precise."
        ),
    }

    header_parts: list[str] = []
    if differentiator_block:
        header_parts.append(differentiator_block)
    header_parts.append(
        "ANSWERING RULES:\n"
        "1. Base your answer ONLY on the provided context chunks.\n"
        "2. The chunks are in SEQUENTIAL (page) ORDER — read them in the order given.\n"
        "3. Give a complete, detailed answer — include all specs, numbers, conditions.\n"
        "4. Cite document name + page range for every piece of information.\n"
        "5. Distinguish clearly between different documents if both are referenced.\n"
        "6. For table chunks, explicitly state what the table contains.\n"
        '7. If "Is continuation: true" — treat the chunk as continuation of the previous content.\n'
        '8. The "Seq#" tag shows reading order — respect it when synthesising the answer.\n'
        "9. If the answer is not in context: \"I could not find the answer in the provided documents.\""
    )
    header_parts.append("\nHere are the relevant document pages:\n")

    user_content: list[dict] = [{"type": "text", "text": "\n\n".join(header_parts)}]
    has_images = False

    for i, chunk in enumerate(context_chunks, start=1):
        seq       = chunk.get("chunk_sequence", i)
        seq_pos   = chunk.get("_seq_position", i - 1)
        reason    = chunk.get("_reason", "direct_match")
        score     = chunk.get("_score", 0.0)
        doc_name  = chunk.get("_doc_name", f"doc_id={chunk.get('doc_id', '?')}")
        section   = chunk.get("section_path") or ""
        is_cont   = chunk.get("is_continuation", False)
        is_table  = chunk.get("is_table", False)
        tbl_summ  = chunk.get("table_summary") or ""
        topics    = chunk.get("key_topics") or ""
        cont_type = chunk.get("continuity_type") or "none"
        cont_notes = chunk.get("continuity_notes") or ""
        header = (
            f"[Seq#{seq_pos} | Chunk {chunk.get('_id', '?')} | {reason} | Score: {score:.3f}]\n"
            f"Doc: {doc_name} | "
            f"Pages: {chunk.get('page_start', '?')}–{chunk.get('page_end', '?')} | "
            f"SeqOrder: {seq}\n"
            f"Title: {chunk.get('title') or ''}\n"
            f"Section: {section}\n"
            f"Topics: {topics}\n"
            f"Table: {is_table} | Table Summary: {tbl_summ}\n"
            f"Continuity: {cont_type}\n"
            f"Continuity Notes: {cont_notes}\n"
            f"Is continuation: {is_cont}\n"
            f"Content:\n{chunk.get('content', '')}\n\n"
            f"[Description]\n{chunk.get('description', '')}"
        )
        user_content.append({"type": "text", "text": header})

        # Attach page images (page_start → page_end)
        img_base   = chunk.get("img_url_path") or ""
        page_start = chunk.get("page_start")
        page_end   = chunk.get("page_end")
        if img_base and page_start and page_end:
            for pg in range(int(page_start), int(page_end) + 1):
                pg_path = f"{img_base}\\page_{pg}.png"
                pg_b64  = read_image_from_share(pg_path)
                if pg_b64:
                    user_content.append(
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{pg_b64}"}}
                    )
                    has_images = True
                    print(f"[GPT] Chunk {i}: attached image for page {pg}")
                else:
                    print(f"[GPT] Chunk {i}: could not read image for page {pg}")
        else:
            img_path = chunk.get("img_url_path")
            img_b64  = read_image_from_share(img_path)
            if img_b64:
                mime = _mime_from_path(img_path)
                user_content.append(
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{img_b64}"}}
                )
                has_images = True
                print(f"[GPT] Chunk {i}: attached {mime} from {img_path}")
            else:
                print(f"[GPT] Chunk {i}: no image (path={img_path!r})")

    user_content.append({
        "type": "text",
        "text": (
            f"\n---\nQuestion: {question}\n\n"
            "Please answer the question based only on the provided pages. "
            "Be specific and actionable. Include relevant steps or parameters if present."
        ),
    })

    if not has_images:
        plain = "\n".join(b["text"] for b in user_content if b.get("type") == "text")
        user_msg = {"role": "user", "content": plain}
    else:
        user_msg = {"role": "user", "content": user_content}

    return [system_msg, user_msg]


# ── Answer generation ─────────────────────────────────────────────────────────

def generate_answer(
    question: str,
    context_chunks: list[dict],
    differentiator_block: str = "",
) -> str:
    print(f"[GPT] Generating answer — question={len(question)} chars, context={len(context_chunks)} chunk(s)…")
    client   = get_openai_client()
    messages = build_chat_messages(question, context_chunks, differentiator_block)
    resp     = client.chat.completions.create(
        model=CHAT_MODEL,
        messages=messages,
        max_tokens=2048,
        temperature=TEMP_ANSWERING,
    )
    answer = resp.choices[0].message.content or ""
    print(f"[GPT] Answer generated ({len(answer)} chars)")
    return answer


# ── Suggestion generation ─────────────────────────────────────────────────────

def generate_suggestions(docs: list[dict]) -> list[str]:
    """Generate 4 context-aware question suggestions from knowledge-base doc descriptions."""
    if not docs:
        return [
            "What documents are available in the knowledge base?",
            "Describe the wire bonding process.",
            "What are common alarms and how to resolve them?",
            "What process parameters are critical for die bonding?",
        ]

    summaries: list[str] = []
    for d in docs[:20]:
        name = Path(d.get("FILEPATH", "unknown")).name if d.get("FILEPATH") else "unknown"
        desc = (d.get("DESCRIPTION") or "")[:400]
        tags = ", ".join(filter(None, [d.get("PROCESS"), d.get("MODULE"), d.get("EQ_TYPE"), d.get("PACKAGE")]))
        summaries.append(f"- {name} [{tags}]: {desc}")

    prompt = (
        "You are a process engineering assistant. Based on the following knowledge base documents, "
        "suggest 4 specific, actionable questions that a process engineer might ask to troubleshoot "
        "issues or learn about these processes. Focus on alarms, parameters, troubleshooting, and steps.\n\n"
        "Documents:\n" + "\n".join(summaries) + "\n\n"
        'Return ONLY a JSON object: {"suggestions": ["question1", "question2", "question3", "question4"]}'
    )

    try:
        client = get_openai_client()
        resp = client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            max_tokens=512,
        )
        text = (resp.choices[0].message.content or "{}").strip()
        if text.startswith("```"):
            text = "\n".join(text.split("\n")[1:-1])
        return json.loads(text).get("suggestions", [])[:4]
    except Exception:
        traceback.print_exc()
        return [
            "How do I resolve a wire bonding deformation alarm?",
            "What are the critical parameters for the die bonding process?",
            "How to troubleshoot low confidence score issues?",
            "What steps are involved in the FAV process?",
        ]


# ── Full chat pipeline ────────────────────────────────────────────────────────

# ── Document scoring & query rewriting ───────────────────────────────────────

def _clean_json(raw: str) -> str:
    raw = re.sub(r"^```json\s*", "", raw)
    raw = re.sub(r"^```\s*",     "", raw)
    raw = re.sub(r"\s*```$",     "", raw)
    return raw.strip()


def score_documents_for_query(
    question: str,
    profiles: list[dict],
) -> list[tuple[float, dict, str]]:
    """LLM-score each document profile 0.0–1.0 for the query.

    Returns list of (score, profile_dict, reason) sorted highest-first.
    """
    if not profiles:
        return []
    doc_summaries = []
    for p in profiles:
        doc_name = Path(p.get("filepath", "")).name or p.get("filepath", "unknown")
        doc_summaries.append(
            f"- Doc: {doc_name}\n"
            f"  Title           : {p.get('title') or ''}\n"
            f"  Description     : {(p.get('description') or '')[:300]}\n"
            f"  Key Topics      : {p.get('key_topics') or ''}\n"
            f"  Key Entities    : {p.get('key_entities') or ''}\n"
            f"  Unique Keywords : {p.get('unique_keywords') or ''}\n"
            f"  Differentiators : {(p.get('differentiators') or '')[:200]}\n"
        )
    prompt = (
        "You are a document routing assistant.\n"
        "Score each document 0.0–1.0 on how likely it contains the answer.\n"
        "Return ONLY a valid JSON array, each item:\n"
        '- "doc_name": exact filename (basename)\n'
        '- "score"   : float 0.0–1.0\n'
        '- "reason"  : one sentence mentioning specific unique terms matched\n'
        f"[DOCUMENTS]\n{''.join(doc_summaries)}\n"
        f"[QUESTION]\n{question}\n"
        "Return JSON array only. No markdown."
    )
    try:
        client = get_openai_client()
        resp = client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=TEMP_DOC_SCORING,
            max_tokens=512,
        )
        raw = _clean_json(resp.choices[0].message.content or "[]")
        scored_raw = json.loads(raw)
        # Map filename back to profile
        name_to_profile = {
            Path(p.get("filepath", "")).name: p for p in profiles
        }
        result = []
        for item in scored_raw:
            matched = name_to_profile.get(item.get("doc_name", ""))
            if matched:
                result.append((float(item.get("score", 0.0)), matched, item.get("reason", "")))
        result.sort(key=lambda x: x[0], reverse=True)
        return result
    except Exception:
        traceback.print_exc()
        return [(1.0, p, "") for p in profiles]


def rewrite_query(
    question: str,
    relevant_profiles: list[dict],
) -> str:
    """LLM-rewrite query using unique keywords from the relevant doc profiles."""
    if not relevant_profiles:
        return question
    hints = []
    for p in relevant_profiles:
        doc_name = Path(p.get("filepath", "")).name
        hints.append(
            f"- {doc_name}: unique={p.get('unique_keywords', '')[:200]}, "
            f"entities={p.get('key_entities', '')[:100]}"
        )
    prompt = (
        "Rewrite the query to improve retrieval. Add relevant technical terms "
        "from the document hints ONLY if truly relevant. Keep it concise (1-2 sentences).\n"
        f"[DOCUMENT HINTS]\n{''.join(hints)}\n"
        f"[ORIGINAL QUERY]\n{question}\n"
        "Return ONLY the rewritten query string."
    )
    try:
        client = get_openai_client()
        resp = client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=TEMP_QUERY_REWRITE,
            max_tokens=256,
        )
        rewritten = (resp.choices[0].message.content or "").strip()
        if rewritten and len(rewritten) > 5:
            print(f"[QueryRewrite] {question!r} → {rewritten!r}")
            return rewritten
    except Exception:
        traceback.print_exc()
    return question


def build_differentiator_block(profiles: list[dict]) -> str:
    """Build a disambiguation block shown to the LLM before answering."""
    if not profiles:
        return ""
    lines = ["[DOCUMENT DIFFERENTIATORS — READ CAREFULLY BEFORE ANSWERING]"]
    lines.append(
        "These documents may share similar keywords. "
        "Use the unique identifiers below to avoid confusion.\n"
    )
    for p in profiles:
        doc_name = Path(p.get("filepath", "")).name or "unknown"
        doc_title = p.get("title") or doc_name
        doc_type = p.get("document_type") or "N/A"
        domain   = p.get("domain") or "N/A"
        lines.append(f"▶ {doc_name}")
        lines.append(f"  Title           : {doc_title}")
        lines.append(f"  Type / Domain   : {doc_type} / {domain}")
        lines.append(f"  Description     : {(p.get('description') or 'N/A')[:300]}")
        lines.append(f"  Key Topics      : {p.get('key_topics') or 'N/A'}")
        lines.append(f"  Key Entities    : {p.get('key_entities') or 'N/A'}")
        lines.append(f"  Unique keywords : {p.get('unique_keywords') or 'N/A'}")
        lines.append(f"  Differentiators : {(p.get('differentiators') or 'N/A')[:400]}")
        lines.append(
            f"  Shared keywords : {p.get('shared_keywords') or 'N/A'}"
            " ← these alone are NOT enough to identify this document"
        )
        lines.append("")
    return "\n".join(lines)


# ── Context expansion helpers ─────────────────────────────────────────────────

def _expand_neighbors(
    direct_hits: list[dict],
    extended: dict,
    n_neighbors: int = 1,
) -> dict[int, dict]:
    """Walk prev/next links n_neighbors deep for each direct hit.

    Returns {chunk_id: chunk_data_dict} for all neighbor chunks found.
    Walks Oracle prev_chunk_id / next_chunk_id chains.
    """
    neighbor_ids: set[int] = set()
    direct_ids   = {h.get("_id") for h in direct_hits if h.get("_id")}

    for hit in direct_hits:
        cid  = hit.get("_id")
        data = extended.get(cid, {})

        # walk prev
        cur = data.get("prev_chunk_id")
        for _ in range(n_neighbors):
            if cur and cur not in direct_ids:
                neighbor_ids.add(cur)
            cur = extended.get(cur, {}).get("prev_chunk_id") if cur else None

        # walk next
        cur = data.get("next_chunk_id")
        for _ in range(n_neighbors):
            if cur and cur not in direct_ids:
                neighbor_ids.add(cur)
            cur = extended.get(cur, {}).get("next_chunk_id") if cur else None

    # Fetch any neighbors not yet in extended
    missing = [nid for nid in neighbor_ids if nid not in extended]
    if missing:
        extra = fetch_chunks_extended_by_ids(missing)
        extended.update(extra)

    return {nid: extended[nid] for nid in neighbor_ids if nid in extended}


def _expand_topic_groups(
    direct_hits: list[dict],
    extended: dict,
    direct_ids: set[int],
    neighbor_ids: set[int],
) -> list[dict]:
    """Fetch all chunks sharing a topic_group_id with any direct hit.

    Only expands when topic_complete=False for that hit.
    Uses the DB column directly when available, otherwise approximates:
      topic_complete = (next_chunk_id is None) or (continuity_type in ('none', ''))
    """
    topic_group_ids = []
    doc_ids         = []
    for hit in direct_hits:
        cid  = hit.get("_id")
        data = extended.get(cid, {})
        tgid = data.get("topic_group_id")
        did  = data.get("doc_id") or hit.get("doc_id")
        if tgid is None or did is None:
            continue
        # Use DB topic_complete if available, else approximate
        if "topic_complete" in data:
            topic_complete = data["topic_complete"]
        else:
            next_cid     = data.get("next_chunk_id")
            cont_type    = data.get("continuity_type") or "none"
            topic_complete = (next_cid is None) or (cont_type in ("none", ""))
        if not topic_complete:
            topic_group_ids.append(tgid)
            doc_ids.append(did)

    if not topic_group_ids:
        return []

    exclude = direct_ids | neighbor_ids
    rows    = fetch_chunks_by_topic_groups(
        list(set(topic_group_ids)), list(set(doc_ids))
    )
    return [r for r in rows if r["id"] not in exclude]


def enforce_sequential_order(
    candidates: list[dict],
) -> list[dict]:
    """Within each document, drop any chunk whose page_start goes backwards.

    Candidates are dicts that MUST have:
      doc_id, page_start, chunk_sequence, _id, _score, _reason
    Returns the filtered list with _seq_position set.
    """
    by_doc: dict[int, list[dict]] = defaultdict(list)
    for c in candidates:
        by_doc[c.get("doc_id")].append(c)

    ordered: list[dict] = []
    for _doc_id, items in by_doc.items():
        items_sorted = sorted(
            items,
            key=lambda x: (
                x.get("sequential_order") if x.get("sequential_order") is not None else x.get("page_start", 0),
                x.get("chunk_sequence", 0),
                x.get("_id", 0),
            ),
        )
        last_page = -1
        for item in items_sorted:
            cp = item.get("page_start", 0)
            if cp >= last_page:
                ordered.append(item)
                last_page = cp
            else:
                print(
                    f"  [SeqEnforce] DROPPED chunk {item.get('_id')} "
                    f"(page {cp} < last_page {last_page}, doc_id={_doc_id})"
                )

    for pos, item in enumerate(ordered):
        item["_seq_position"] = pos

    return ordered

def run_chat(
    question: str,
    process: list[str] | str | None = None,
    module: list[str] | str | None = None,
    equipment_type: list[str] | str | None = None,
    package: list[str] | str | None = None,
) -> dict:
    """End-to-end: doc-score → rewrite query → Qdrant search → expand neighbors/topic-groups
    → sequential enforcement → Oracle enrich → differentiator block → GPT answer.
    """
    def _vals(v: list[str] | str | None) -> list[str]:
        if not v:
            return []
        return v if isinstance(v, list) else [v]

    pv  = _vals(process)
    mv  = _vals(module)
    ev  = _vals(equipment_type)
    pkv = _vals(package)

    # ── Step 1: Fetch doc profiles (filtered to user's process/module/etc.) ─
    profiles = fetch_doc_profiles_for_retrieval(
        process=pv or None,
        module=mv or None,
        eq_type=ev or None,
        package=pkv or None,
    )

    # ── Step 2: Score documents for query relevance ──────────────────────────
    doc_scores    = score_documents_for_query(question, profiles)
    relevant_ids  = {p["id"] for s, p, _ in doc_scores if s >= 0.3}
    if not relevant_ids and profiles:
        relevant_ids = {p["id"] for p in profiles}  # fallback: use all
    relevant_profiles = [p for s, p, _ in doc_scores if p["id"] in relevant_ids]

    print(f"[DocScore] {len(doc_scores)} docs scored; {len(relevant_ids)} relevant.")
    for score, p, reason in doc_scores:
        marker = "✓" if score >= 0.3 else "✗"
        print(f"  {marker} [{score:.2f}] {Path(p.get('filepath','') or '').name} — {reason}")

    # ── Step 3: Rewrite query with relevant doc terminology ──────────────────
    rewritten = rewrite_query(question, relevant_profiles)
    query_vec  = embed_query(rewritten)

    # ── Step 4: Build Qdrant filter ──────────────────────────────────────────
    must: list[qm.FieldCondition] = []
    if pv:
        must.append(qm.FieldCondition(key="process",        match=qm.MatchAny(any=pv)))
    if mv:
        must.append(qm.FieldCondition(key="module",         match=qm.MatchAny(any=mv)))
    if ev:
        must.append(qm.FieldCondition(key="equipment_type", match=qm.MatchAny(any=ev)))
    if pkv:
        must.append(qm.FieldCondition(key="package",        match=qm.MatchAny(any=pkv)))
    # Restrict to doc-scored relevant documents
    if relevant_ids:
        must.append(qm.FieldCondition(key="doc_id", match=qm.MatchAny(any=list(relevant_ids))))
    qdrant_filter = qm.Filter(must=must) if must else None

    # ── Step 5: Qdrant vector search (top_k = 5 as in test.py) ──────────────
    direct_hits = build_context_chunks(query_vec, filters=qdrant_filter, top_k=5)

    if not direct_hits:
        return {
            "success": True,
            "response": (
                "I could not find relevant information in the knowledge base. "
                "Please try rephrasing or broadening your filters."
            ),
            "sources_used": [],
            "context_pages": 0,
        }

    # ── Step 6: Fetch extended metadata for direct hits ──────────────────────
    direct_ids = [h["_id"] for h in direct_hits if h.get("_id") is not None]
    extended   = fetch_chunks_extended_by_ids(direct_ids)

    # Back-fill extended data into direct_hits for subsequent steps
    for hit in direct_hits:
        cid = hit.get("_id")
        if cid in extended:
            hit.update({k: v for k, v in extended[cid].items() if k not in hit})

    # ── Step 7: Expand prev/next neighbors (n=1) ────────────────────────────
    neighbor_map = _expand_neighbors(direct_hits, extended, n_neighbors=1)
    print(f"[Expand] {len(neighbor_map)} neighbor chunk(s) found.")

    # ── Step 8: Expand topic groups ──────────────────────────────────────────
    topic_rows = _expand_topic_groups(
        direct_hits, extended,
        direct_ids=set(direct_ids),
        neighbor_ids=set(neighbor_map.keys()),
    )
    print(f"[Expand] {len(topic_rows)} topic-group chunk(s) found.")

    # ── Step 9: Assemble all candidates (deduplicated) ────────────────────────
    all_candidates: list[dict] = []
    seen_ids: set[int] = set()

    # Find the best score among direct hits (for inheriting to neighbors)
    best_score_by_hit: dict[int, float] = {}
    for hit in direct_hits:
        cid = hit.get("_id")
        if cid is not None:
            best_score_by_hit[cid] = hit.get("_score", 0.0)

    for hit in direct_hits:
        cid = hit.get("_id")
        if cid is not None and cid not in seen_ids:
            seen_ids.add(cid)
            all_candidates.append({**hit, "_reason": "direct_match"})

    for cid, data in neighbor_map.items():
        if cid not in seen_ids:
            seen_ids.add(cid)
            # Inherit parent score — find which direct hit this neighbor belongs to
            parent_score = 0.0
            for hit in direct_hits:
                ext = extended.get(hit.get("_id"), {})
                if ext.get("prev_chunk_id") == cid or ext.get("next_chunk_id") == cid:
                    parent_score = hit.get("_score", 0.0)
                    break
            all_candidates.append({
                "_id":     cid,
                "_score":  parent_score,
                "_reason": "neighbor",
                **data,
            })

    for row in topic_rows:
        rid = row["id"]
        if rid not in seen_ids:
            seen_ids.add(rid)
            # Topic group chunks get 90% of their parent hit's score
            parent_score = 0.0
            for hit in direct_hits:
                if extended.get(hit.get("_id"), {}).get("topic_group_id") == row.get("topic_group_id"):
                    parent_score = hit.get("_score", 0.0) * 0.9
                    break
            all_candidates.append({
                "_id":     rid,
                "_score":  parent_score,
                "_reason": "topic_group",
                **row,
            })

    # ── Step 9b: Expand related chunks (shared topics, same doc) ─────────────
    for hit in direct_hits:
        cid  = hit.get("_id")
        data = extended.get(cid, {})
        related_str = data.get("related_chunk_ids") or ""
        if not related_str:
            continue
        related_ids = [int(x) for x in related_str.split(",") if x.strip().isdigit()][:3]
        if DEBUG_RELATED and related_ids:
            print(f"[RelExpand] direct_hit _id={cid} doc_id={data.get('doc_id')} -> related_ids={related_ids}")
        missing_related = [rid for rid in related_ids if rid not in seen_ids and rid not in extended]
        if missing_related:
            if DEBUG_RELATED:
                print(f"[RelExpand] fetching missing related ids: {missing_related}")
            extra_related = fetch_chunks_extended_by_ids(missing_related)
            extended.update(extra_related)
        for rid in related_ids:
            if rid not in seen_ids and rid in extended:
                seen_ids.add(rid)
                if DEBUG_RELATED:
                    rel_score = hit.get("_score", 0.0) * 0.8
                    print(f"[RelExpand] added related id={rid} parent={cid} rel_score={rel_score:.3f} doc={extended[rid].get('doc_id')} page={extended[rid].get('page_start')}")
                all_candidates.append({
                    "_id":     rid,
                    "_score":  hit.get("_score", 0.0) * 0.8,
                    "_reason": "topic_related",
                    **extended[rid],
                })

    # ── Step 10: Sequential enforcement ──────────────────────────────────────
    final_chunks = enforce_sequential_order(all_candidates)
    final_chunks = final_chunks[:MAX_CONTEXT_PAGES]
    print(f"[Retrieval] {len(all_candidates)} candidates → {len(final_chunks)} after enforcement.")

    # ── Step 11: Enrich any chunks still missing content/img from Oracle ─────
    need_enrich = [c["_id"] for c in final_chunks
                   if c.get("_id") is not None and not c.get("content")]
    if need_enrich:
        details_map = fetch_chunk_details_by_ids(need_enrich)
        for chunk in final_chunks:
            pid = chunk.get("_id")
            if pid in details_map:
                chunk.update({k: v for k, v in details_map[pid].items() if not chunk.get(k)})

    # ── Step 12: Attach doc names for display ────────────────────────────────
    doc_ids_used  = list({c.get("doc_id") for c in final_chunks if c.get("doc_id") is not None})
    filepath_map  = fetch_doc_filepaths(doc_ids_used)
    profile_by_id = {p["id"]: p for p in profiles}
    for chunk in final_chunks:
        did = chunk.get("doc_id")
        chunk["_doc_name"] = Path(filepath_map.get(did, "")).name if did else f"doc_id={did}"

    # ── Step 13: Build differentiator block ──────────────────────────────────
    docs_in_context   = {c.get("doc_id") for c in final_chunks if c.get("doc_id")}
    mentioned_profiles = [profile_by_id[did] for did in docs_in_context if did in profile_by_id]
    diff_block        = build_differentiator_block(mentioned_profiles)

    # ── Step 14: Generate answer ──────────────────────────────────────────────
    answer = generate_answer(question, final_chunks, diff_block)

    sources_used = [
        {
            "name":           chunk.get("_doc_name", f"Doc {chunk.get('doc_id')}"),
            "type":           "chunk",
            "point_id":       chunk.get("_id"),
            "score":          round(chunk.get("_score", 0.0), 4),
            "reason":         chunk.get("_reason", ""),
            "seq_position":   chunk.get("_seq_position", 0),
            "process":        chunk.get("process"),
            "module":         chunk.get("module"),
            "equipment_type": chunk.get("equipment_type"),
            "package":        chunk.get("package"),
        }
        for chunk in final_chunks
    ]

    return {
        "success":       True,
        "response":      answer,
        "sources_used":  sources_used,
        "context_pages": len(final_chunks),
    }
