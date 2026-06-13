"""Document preprocessing service — PDF/DOC → images → LLM analysis → DB + Qdrant."""
from __future__ import annotations

import base64
import importlib
import json
import re
import tempfile
import pythoncom
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import httpx
import openai
import win32com.client

from config.llm import (
    BASE_URL, CERT_PATH, MAX_WORKERS, TOKEN,
    TEMP_PAGE_DESCRIBE, TEMP_CONTINUITY, TEMP_CHUNKING,
    TEMP_PROFILING, IMAGE_DPI, IMAGE_FORMAT,
    MIN_EMBED_DELAY, MIN_CHAT_DELAY, MAX_RETRIES, RETRY_BASE_DELAY,
)
from config.share_drive import HICP_SHARE_DRIVE_CONFIG
from services.db_service import insert_knowledge_chunk, insert_knowledge_doc_profile, update_chunk_links
from services.embed_service import embed_doc_chunks
from services.qdrant_service import ingest_to_qdrant
from services.share_service import write_file_to_share

# ── LLM client (module-level; preprocess is a batch operation) ────────────────

_CLIENT: openai.OpenAI | None = None


def _get_client() -> openai.OpenAI:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = openai.OpenAI(
            api_key=TOKEN,
            base_url=BASE_URL,
            default_headers={"Authorization": f"Basic {TOKEN}"},
            http_client=httpx.Client(verify=CERT_PATH),
        )
    return _CLIENT


_SYSTEM_PAGE_DESCRIBE = (
    "You are a precise document analysis assistant with vision capability. "
    "You are given up to three consecutive pages from a technical document. "
    "Your task is to DESCRIBE the CURRENT PAGE in full detail, using the "
    "PREVIOUS and NEXT pages ONLY to understand context and continuity.\n"
    "CRITICAL RULES:\n"
    "1. Extract and preserve ALL text, numbers, part numbers, specs exactly.\n"
    "2. For tables: detect if rows/columns continue from the previous page "
    "or continue to the next page.\n"
    "3. For text: detect if a paragraph or section starts on a previous page "
    "or ends on a next page.\n"
    "4. Never paraphrase — preserve exact wording.\n"
    "5. Output ONLY valid JSON as specified."
)

_USER_PAGE_DESCRIBE = (
    "Analyze the CURRENT PAGE (Page {{page_num}} of {{total_pages}}) "
    "from document: {{doc_name}}\n"
    "Return ONLY a valid JSON object with these fields:\n"
    '- "page_num"                    : {{page_num}} (integer)\n'
    '- "raw_text"                    : full extracted text from this page exactly as it appears (string)\n'
    '- "content_type"                : one of: "text", "table", "mixed", "image_heavy",\n'
    '                                  "cover_page", "toc", "index" (string)\n'
    '- "section_path"                : breadcrumb e.g. ["Chapter 1", "Section 1.2"] (array of strings)\n'
    '- "topics"                      : list of main topics on this page (array of strings)\n'
    '- "keywords"                    : technical terms, part numbers, values (array of strings)\n'
    '- "has_table"                   : true if page contains a table (boolean)\n'
    '- "table_headers"               : column headers found (array of strings, empty if none)\n'
    '- "table_continues_from_prev"   : true if table continues FROM previous page (boolean)\n'
    '- "table_continues_to_next"     : true if table continues TO next page (boolean)\n'
    '- "content_continues_from_prev" : true if paragraph/section continues FROM previous page (boolean)\n'
    '- "content_continues_to_next"   : true if content is cut off and continues TO next page (boolean)\n'
    '- "summary"                     : 2-3 sentence summary of this page (string)\n'
    '- "page_role"                   : one of: "cover", "toc", "introduction", "specification",\n'
    '                                  "data_table", "procedure", "appendix", "index",\n'
    '                                  "continuation", "general" (string)\n'
    "Return JSON only. No markdown. No explanation."
)

_DOC_DESCRIPTION_PROMPT = (
    "You are analyzing a document. Below are summaries of each page. "
    "Provide a concise overall description of the document (maximum 900 characters) "
    "covering its purpose, main topics, equipment or processes involved, and any key information. "
    "Output ONLY the description text, no JSON, no labels, no headings."
)

_DOC_PROFILE_PROMPT = (
    "You are analyzing a document. Below are per-chunk descriptions. "
    "Return a JSON object with exactly these keys:\n"
    '  "description": "<overall document description, max 900 characters>",\n'
    '  "purpose": "<what this document is used for>",\n'
    '  "domain": "<subject domain, e.g. electronics, semiconductor, procurement>",\n'
    '  "document_type": "<e.g. datasheet, manual, report, procedure, invoice>",\n'
    '  "key_topics": "<comma-separated list of main topics>",\n'
    '  "key_entities": "<comma-separated list of key entities: equipment, components, systems>",\n'
    '  "unique_keywords": "<comma-separated keywords specific to THIS document that distinguish it from similar docs>",\n'
    '  "differentiators": "<3-5 sentences explaining how this document differs from similar docs>",\n'
    '  "shared_keywords": "<comma-separated generic terms shared with similar docs, e.g. common process names>"\n'
    "Output ONLY the JSON object. No explanation."
)


# ── File conversion helpers ───────────────────────────────────────────────────

def docx_to_pdf(docx_path: Path, output_pdf_path: Path) -> Path:
    """
    Convert DOC/DOCX → PDF using Word COM automation.
    Safe to call from background threads (handles CoInitialize internally).
    """
    # Must be called in EVERY thread that uses COM
    pythoncom.CoInitialize()

    word = None
    doc  = None

    try:
        word = win32com.client.Dispatch("Word.Application")

        # Suppress every possible dialog / alert
        word.Visible         = False   # keep Word hidden
        word.DisplayAlerts   = 0       # wdAlertsNone  → no pop-ups at all
        word.AutomationSecurity = 3    # msoAutomationSecurityForceDisable
                                       #   → macros silently disabled

        doc = word.Documents.Open(
            str(docx_path.resolve()),
            ConfirmConversions = False, # no format-conversion dialog
            ReadOnly           = True,  # safer; avoids "save changes?" dialog
            AddToRecentFiles   = False,
            PasswordDocument   = "",
            PasswordTemplate   = "",
            Revert             = False,
            WritePasswordDocument = "",
            WritePasswordTemplate = "",
            Format             = 0,    # wdOpenFormatAuto
        )

        doc.SaveAs2(
            str(output_pdf_path.resolve()),
            FileFormat = 17,           # wdFormatPDF
        )
        print(f"[INFO] PDF saved: {output_pdf_path}")

    finally:
        if doc is not None:
            try:
                doc.Close(SaveChanges=False)
            except Exception:
                pass
        if word is not None:
            try:
                word.Quit(SaveChanges=False)
            except Exception:
                pass
        # Must match every CoInitialize call
        pythoncom.CoUninitialize()

    return output_pdf_path

def pdf_to_images(pdf_path: Path, output_dir: Path, dpi: int = IMAGE_DPI) -> List[Path]:
    fitz = importlib.import_module("fitz")
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")
    output_dir.mkdir(parents=True, exist_ok=True)
    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)
    created: List[Path] = []
    with fitz.open(pdf_path) as doc:
        if doc.page_count == 0:
            raise ValueError("PDF has no pages.")
        stem = pdf_path.stem
        for idx in range(doc.page_count):
            page = doc.load_page(idx)
            pix  = page.get_pixmap(matrix=matrix, alpha=False)
            out  = output_dir / f"{stem}_page_{idx + 1:03d}.png"
            pix.save(out)
            created.append(out)
    return created


# ── Image utilities ───────────────────────────────────────────────────────────

def _img_to_b64(img_path: Path) -> str:
    return base64.b64encode(img_path.read_bytes()).decode("utf-8")


def _image_url_content(img_path: Path) -> dict:
    return {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{_img_to_b64(img_path)}", "detail": "high"}}


def _clean_json(raw: str) -> str:
    raw = re.sub(r"^```json\s*", "", raw.strip())
    raw = re.sub(r"^```\s*", "", raw)
    return re.sub(r"\s*```$", "", raw)


# ── Step 2: Context-aware page description ────────────────────────────────────

def _describe_page_with_context(
    img_path: Path,
    prev_img: Path | None,
    next_img: Path | None,
    doc_name: str,
    page_num: int,
    total_pages: int,
) -> dict:
    print(f"[INFO] Describing page {page_num}/{total_pages}...")
    content_parts: list = []
    if prev_img:
        content_parts += [
            {"type": "text", "text": f"=== PREVIOUS PAGE (Page {page_num - 1}) — context only ==="},
            _image_url_content(prev_img),
        ]
    content_parts += [
        {"type": "text", "text": f"=== CURRENT PAGE (Page {page_num}) — DESCRIBE THIS ==="},
        _image_url_content(img_path),
    ]
    if next_img:
        content_parts += [
            {"type": "text", "text": f"=== NEXT PAGE (Page {page_num + 1}) — context only ==="},
            _image_url_content(next_img),
        ]
    content_parts.append({"type": "text", "text": _USER_PAGE_DESCRIBE.format(
        page_num=page_num, total_pages=total_pages, doc_name=doc_name,
    )})
    try:
        resp = _get_client().chat.completions.create(
            model="gpt-4.1",
            messages=[
                {"role": "system", "content": _SYSTEM_PAGE_DESCRIBE},
                {"role": "user",   "content": content_parts},
            ],
            temperature=TEMP_PAGE_DESCRIBE,
            max_tokens=1500, n=1, stream=False,
        )
        raw = _clean_json(resp.choices[0].message.content or "")
        result = json.loads(raw)
        result["page_num"] = page_num
        return result
    except Exception as exc:
        print(f"[WARN] Page {page_num} description failed: {exc}")
        return _empty_page_desc(page_num)


def _empty_page_desc(page_num: int) -> dict:
    return {
        "page_num": page_num, "raw_text": "",
        "content_type": "unknown", "section_path": [],
        "topics": [], "keywords": [], "has_table": False, "table_headers": [],
        "table_continues_from_prev": False, "table_continues_to_next": False,
        "content_continues_from_prev": False, "content_continues_to_next": False,
        "summary": "", "page_role": "general",
    }


def _describe_all_pages(
    image_paths: List[Path], doc_name: str, max_workers: int = MAX_WORKERS,
) -> List[dict]:
    total = len(image_paths)
    results: List[dict | None] = [None] * total
    print(f"[INFO] Describing {total} pages with context (workers={max_workers})...")

    def _describe(idx: int) -> tuple:
        return idx, _describe_page_with_context(
            img_path=image_paths[idx],
            prev_img=image_paths[idx - 1] if idx > 0 else None,
            next_img=image_paths[idx + 1] if idx < total - 1 else None,
            doc_name=doc_name, page_num=idx + 1, total_pages=total,
        )

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for future in as_completed({executor.submit(_describe, i): i for i in range(total)}):
            try:
                idx, desc = future.result()
                results[idx] = desc
            except Exception as exc:
                print(f"[ERROR] Page description thread failed: {exc}")
    return [r if r is not None else _empty_page_desc(i + 1) for i, r in enumerate(results)]


# ── Step 3: Continuity verification ──────────────────────────────────────────

def _verify_continuity(
    desc_a: dict, desc_b: dict, img_a: Path, img_b: Path,
) -> dict:
    pa, pb = desc_a["page_num"], desc_b["page_num"]
    print(f"[INFO] Continuity: page {pa} ↔ {pb}")
    content_parts = [
        {"type": "text", "text": (
            f"=== PAGE {pa} ===\n"
            f"Content type : {desc_a.get('content_type')}\n"
            f"Has table    : {desc_a.get('has_table')}\n"
            f"Table headers: {desc_a.get('table_headers')}\n"
            f"Summary      : {desc_a.get('summary')}\n"
            f"Flagged continues_to_next: {desc_a.get('content_continues_to_next')} / "
            f"table_continues_to_next: {desc_a.get('table_continues_to_next')}"
        )},
        _image_url_content(img_a),
        {"type": "text", "text": (
            f"=== PAGE {pb} ===\n"
            f"Content type : {desc_b.get('content_type')}\n"
            f"Has table    : {desc_b.get('has_table')}\n"
            f"Table headers: {desc_b.get('table_headers')}\n"
            f"Summary      : {desc_b.get('summary')}\n"
            f"Flagged continues_from_prev: {desc_b.get('content_continues_from_prev')} / "
            f"table_continues_from_prev: {desc_b.get('table_continues_from_prev')}"
        )},
        _image_url_content(img_b),
        {"type": "text", "text": (
            "Examine BOTH pages. Determine the EXACT continuity relationship.\n"
            "Return ONLY a valid JSON object:\n"
            '- "page_a"              : first page number (integer)\n'
            '- "page_b"              : second page number (integer)\n'
            '- "text_continuous"     : true if text/paragraph from A flows into B (boolean)\n'
            '- "table_continuous"    : true if a table on A continues on B (boolean)\n'
            '- "table_shared_headers": shared column headers (array of strings)\n'
            '- "continuity_type"     : one of: "none", "text_flow", "table_split",\n'
            '                          "section_continues", "mixed_continues" (string)\n'
            '- "confidence"          : 0.0–1.0 (float)\n'
            '- "notes"               : brief explanation (string)\n'
            "Return JSON only. No markdown."
        )},
    ]
    try:
        resp = _get_client().chat.completions.create(
            model="gpt-4.1",
            messages=[
                {"role": "system", "content": "You are a precise document continuity analyst."},
                {"role": "user",   "content": content_parts},
            ],
            temperature=TEMP_CONTINUITY,
            max_tokens=300, n=1, stream=False,
        )
        result = json.loads(_clean_json(resp.choices[0].message.content or ""))
        result.update({"page_a": pa, "page_b": pb})
        print(
            f"  [{result.get('continuity_type', '?'):20s}] "
            f"conf={result.get('confidence', 0):.2f} — {result.get('notes', '')[:60]}"
        )
        return result
    except Exception as exc:
        print(f"[WARN] Continuity {pa}↔{pb} failed: {exc}")
        return {
            "page_a": pa, "page_b": pb,
            "text_continuous": desc_a.get("content_continues_to_next", False),
            "table_continuous": desc_a.get("table_continues_to_next", False),
            "continuity_type": "none", "confidence": 0.5, "notes": str(exc),
        }


def _build_continuity_map(
    descriptions: List[dict], image_paths: List[Path], max_workers: int = MAX_WORKERS,
) -> List[dict]:
    n = len(descriptions)
    if n < 2:
        return []
    results: list[dict | None] = [None] * (n - 1)
    print(f"[INFO] Checking continuity for {n - 1} pairs (workers={max_workers})...")

    def _check(i: int) -> tuple:
        return i, _verify_continuity(
            descriptions[i], descriptions[i + 1], image_paths[i], image_paths[i + 1],
        )

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for future in as_completed({executor.submit(_check, i): i for i in range(n - 1)}):
            try:
                i, result = future.result()
                results[i] = result
            except Exception as exc:
                print(f"[ERROR] Continuity pair failed: {exc}")
    return [r for r in results if r is not None]


# ── Step 4: Group pages into semantic chunks ──────────────────────────────────

def _group_pages_into_chunks(
    descriptions: List[dict], continuity_map: List[dict],
) -> List[dict]:
    """Group pages into chunks following the same logic as test.py:
    Merge if LLM signals any continuity (text OR table OR continuity_type != none)
    AND confidence >= 0.4. Otherwise break.
    """
    desc_by_page = {d["page_num"]: d for d in descriptions}
    cont_by_pair = {(c["page_a"], c["page_b"]): c for c in continuity_map}
    page_nums = sorted(desc_by_page.keys())

    groups: List[List[int]] = []
    current: List[int] = [page_nums[0]]
    for i in range(1, len(page_nums)):
        pa, pb = page_nums[i - 1], page_nums[i]
        cont = cont_by_pair.get((pa, pb), {})

        is_continuous = (
            cont.get("text_continuous",  False) or
            cont.get("table_continuous", False) or
            cont.get("continuity_type", "none") not in ("none",)
        )
        if cont.get("confidence", 0.0) < 0.4:
            is_continuous = False

        if is_continuous:
            current.append(pb)
        else:
            groups.append(current)
            current = [pb]
    groups.append(current)

    def dedup(lst: list) -> list:
        seen: set = set()
        result: list = []
        for x in lst:
            # lists/dicts are unhashable — fall back to repr as the dedup key
            key = x if isinstance(x, (str, int, float, bool)) else repr(x)
            if key not in seen:
                seen.add(key)
                result.append(x)
        return result

    structured: List[dict] = []
    for group in groups:
        gd = [desc_by_page[p] for p in group]
        ctypes = [d.get("content_type", "text") for d in gd]
        cont_types = [
            cont_by_pair.get((group[j], group[j + 1]), {}).get("continuity_type", "none")
            for j in range(len(group) - 1)
        ]
        structured.append({
            "pages":           group,
            "page_start":      min(group),
            "page_end":        max(group),
            "raw_texts":       [d.get("raw_text", "") for d in gd],
            "content_type":    max(set(ctypes), key=ctypes.count),
            "section_path":    dedup([s for d in gd for s in d.get("section_path", [])]),
            "topics":          dedup([t for d in gd for t in d.get("topics", [])]),
            "keywords":        dedup([k for d in gd for k in d.get("keywords", [])]),
            "table_headers":   dedup([h for d in gd for h in d.get("table_headers", [])]),
            "continuity_type": max(set(cont_types), key=cont_types.count) if cont_types else "none",
            "page_summaries":  [d.get("summary", "") for d in gd],
            "page_roles":      [d.get("page_role", "general") for d in gd],
        })

    print(f"[INFO] {len(descriptions)} pages → {len(structured)} semantic chunks")
    return structured


# ── Step 5: Chunk enrichment ──────────────────────────────────────────────────

def _enrich_chunk_with_llm(chunk_group: dict, doc_name: str, chunk_index: int) -> dict:
    merged_text = "\n\n--- Page Break ---\n\n".join(chunk_group["raw_texts"])
    page_range = (
        f"Page {chunk_group['page_start']}"
        if chunk_group["page_start"] == chunk_group["page_end"]
        else f"Pages {chunk_group['page_start']}–{chunk_group['page_end']}"
    )
    prompt = (
        f"You are a document analysis assistant.\n"
        f"Below is extracted content from {page_range} of document: {doc_name}\n"
        f"Content type: {chunk_group['content_type']}\n"
        f"Continuity type: {chunk_group['continuity_type']}\n\n"
        "TASK: Analyze this content and return a structured JSON profile.\n"
        "CRITICAL RULES:\n"
        "1. Preserve all exact numbers, part numbers, and technical terms.\n"
        "2. Do NOT paraphrase or omit any data.\n"
        "3. If this is a table chunk, summarize what the table covers.\n"
        "4. title should be concise but specific (not generic like 'Table 1').\n\n"
        'Return ONLY valid JSON:\n'
        '{\n'
        '  "title": "<short specific title>",\n'
        '  "description": "<2-3 sentence description>",\n'
        '  "key_topics": ["..."],\n'
        '  "keywords": ["..."],\n'
        '  "section_path": ["..."],\n'
        '  "content_type": "<paragraph|table|list|mixed|cover|toc>",\n'
        '  "is_table": false,\n'
        '  "table_summary": ""\n'
        '}\n\n'
        f"[CONTENT]\n{merged_text[:5000]}\n\nReturn JSON only. No markdown."
    )
    try:
        resp = _get_client().chat.completions.create(
            model="gpt-4.1",
            messages=[{"role": "user", "content": prompt}],
            temperature=TEMP_CHUNKING,
            max_tokens=500, n=1, stream=False,
        )
        return json.loads(_clean_json(resp.choices[0].message.content or ""))
    except Exception as exc:
        print(f"[WARN] Chunk {chunk_index} enrichment failed: {exc}")
        return {
            "title":         f"{doc_name} — {page_range}",
            "description":   " ".join(chunk_group.get("page_summaries", []))[:300],
            "key_topics":    chunk_group.get("topics", []),
            "keywords":      chunk_group.get("keywords", []),
            "section_path":  chunk_group.get("section_path", []),
            "content_type":  chunk_group.get("content_type", "text"),
            "is_table":      chunk_group.get("content_type") in ("table", "mixed"),
            "table_summary": "",
        }


# ── Document-level description (quick preview at upload) ─────────────────────

def get_document_description(file_path: Path, dpi: int = 150, max_pages: int = 10) -> str:
    """Analyse a document and return a concise overall description (≤ 1000 chars)."""
    print(f"[INFO] Generating description for: {file_path}")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            suffix = file_path.suffix.lower()
            if suffix == ".pdf":
                local_pdf = tmp_path / file_path.name
                local_pdf.write_bytes(file_path.read_bytes())
            else:
                local_pdf = tmp_path / f"{file_path.stem}.pdf"
                docx_to_pdf(file_path, local_pdf)

            image_paths = pdf_to_images(local_pdf, tmp_path / "desc_imgs", dpi=dpi)[:max_pages]
            if not image_paths:
                return ""

            descriptions = _describe_all_pages(image_paths, file_path.stem)
            page_summaries = [d.get("summary", "") for d in descriptions if d.get("summary")]
            if not page_summaries:
                return ""

            combined = "\n".join(f"Page {i + 1}: {s}" for i, s in enumerate(page_summaries))
            resp = _get_client().chat.completions.create(
                model="gpt-4.1",
                messages=[{"role": "user", "content": f"{_DOC_DESCRIPTION_PROMPT}\n\n{combined}"}],
                temperature=TEMP_PROFILING,
                max_tokens=400, n=1, stream=False,
            )
            description = (resp.choices[0].message.content or "").strip()[:1000]
            print(f"[INFO] Description generated ({len(description)} chars).")
            return description
    except Exception as exc:
        print(f"[ERROR] get_document_description failed: {exc}")
        return ""


# ── Main preprocessing pipeline ──────────────────────────────────────────────

def _generate_doc_profile(doc_name: str, enriched_chunks: list[dict]) -> dict:
    """Generate a document profile from enriched chunk descriptions."""
    descriptions = [c.get("description", "") for c in enriched_chunks if c.get("description")]
    if not descriptions:
        return {"description": None, "key_topics": None, "key_entities": None, "unique_keywords": None}
    all_topics   = [t for c in enriched_chunks for t in (c.get("key_topics") or [])]
    all_keywords = [k for c in enriched_chunks for k in (c.get("keywords") or [])]
    all_titles   = [c.get("title", "") for c in enriched_chunks if c.get("title")]
    combined = "\n".join(f"Chunk {i + 1}: {d}" for i, d in enumerate(descriptions[:10]))
    sample_titles = "\n".join(all_titles[:8])
    prompt = (
        f"You are analyzing document '{doc_name}'. Below are chunk titles and descriptions.\n"
        f"{_DOC_PROFILE_PROMPT}\n\n"
        f"[CHUNK TITLES SAMPLE]\n{sample_titles}\n\n"
        f"[CHUNK DESCRIPTIONS SAMPLE]\n{combined}\n\n"
        f"[ALL TOPICS FOUND]\n{', '.join(dict.fromkeys(all_topics))[:500]}\n\n"
        f"[ALL KEYWORDS FOUND]\n{', '.join(dict.fromkeys(all_keywords))[:500]}"
    )
    try:
        resp = _get_client().chat.completions.create(
            model="gpt-4.1",
            messages=[{"role": "user", "content": prompt}],
            temperature=TEMP_PROFILING,
            max_tokens=700, n=1, stream=False,
        )
        data = json.loads(_clean_json(resp.choices[0].message.content or ""))
        return {
            "description":     (data.get("description") or "")[:1000],
            "purpose":         (data.get("purpose") or "")[:500],
            "domain":          (data.get("domain") or "")[:200],
            "document_type":   (data.get("document_type") or "")[:200],
            "key_topics":      data.get("key_topics"),
            "key_entities":    data.get("key_entities"),
            "unique_keywords": data.get("unique_keywords"),
            "differentiators": data.get("differentiators"),
            "shared_keywords": data.get("shared_keywords"),
        }
    except Exception as exc:
        print(f"[WARN] _generate_doc_profile failed: {exc}")
        return {
            "description": None, "purpose": None,
            "domain": None, "document_type": None,
            "key_topics": None, "key_entities": None,
            "unique_keywords": None, "differentiators": None,
            "shared_keywords": None,
        }


def process_uploaded_documents(
    file_paths: List[Path],
    doc_id: int,
    doc_title: str | None = None,
    metadata: Optional[Dict[str, Optional[str]]] = None,
    dpi: int = IMAGE_DPI,
    max_workers: int = MAX_WORKERS,
) -> Dict:
    """Convert → rasterise → context-aware page describe → continuity check →
    semantic grouping → chunk enrichment → upload images → insert DB →
    wire prev/next links → embed → Qdrant.
    """
    share_root = HICP_SHARE_DRIVE_CONFIG["shareName"]
    print(f"[START] Preprocessing {len(file_paths)} file(s). Images → {share_root}\\Images\\{doc_id}")

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    norm_meta: Dict[str, Optional[str]] = {
        "process": None, "module": None, "equipment_type": None, "package": None
    }
    if metadata:
        for k in norm_meta:
            norm_meta[k] = metadata.get(k) or None

    log: List[Dict] = []
    total_image_chunks = 0

    with tempfile.TemporaryDirectory() as local_tmp:
        tmp_path = Path(local_tmp)

        for source_file in file_paths:
            suffix = source_file.suffix.lower()
            if suffix not in {".pdf", ".doc", ".docx"}:
                raise ValueError(f"Unsupported file type: {source_file.name}")

            # Step 1 ─ Convert to PDF
            if suffix == ".pdf":
                local_pdf = tmp_path / source_file.name
                local_pdf.write_bytes(source_file.read_bytes())
            else:
                local_pdf = tmp_path / f"{source_file.stem}.pdf"
                print(f"[INFO] Converting {source_file.name} → PDF…")
                docx_to_pdf(source_file, local_pdf)

            # Step 2 ─ Rasterise pages
            local_images_dir = tmp_path / "images" / source_file.stem
            image_paths = pdf_to_images(local_pdf, local_images_dir, dpi=dpi)
            total_pages = len(image_paths)
            print(f"[INFO] {total_pages} pages from {source_file.name}")

            # Step 3 ─ Context-aware page descriptions (prev + current + next → LLM)
            descriptions = _describe_all_pages(image_paths, source_file.stem, max_workers=max_workers)

            # Step 4 ─ Continuity verification between consecutive page pairs
            continuity_map = _build_continuity_map(descriptions, image_paths, max_workers=max_workers)

            # Step 5 ─ Group continuous pages into semantic chunks
            chunk_groups = _group_pages_into_chunks(descriptions, continuity_map)

            # Step 6 ─ Enrich each chunk with LLM (title, description, keywords)
            print(f"[INFO] Enriching {len(chunk_groups)} chunks with LLM...")
            enriched_chunks: List[dict] = []
            for ci, group in enumerate(chunk_groups):
                print(f"  [Enrich] {ci + 1}/{len(chunk_groups)} "
                      f"pp.{group['page_start']}–{group['page_end']}...")
                enriched_chunks.append(_enrich_chunk_with_llm(group, source_file.stem, ci))

            # Step 7 ─ Upload ALL page images (per-page naming: page_{N}.png)
            print(f"[INFO] Uploading {total_pages} page images to share...")
            for pg_idx, pg_img_path in enumerate(image_paths, start=1):
                pg_ext = pg_img_path.suffix.lstrip(".")
                pg_share_rel = f"Images/{doc_id}/page_{pg_idx}.{pg_ext}"
                write_file_to_share(pg_share_rel, pg_img_path.read_bytes())
            img_base_url = f"{share_root}\\Images\\{doc_id}"
            print(f"[INFO] All {total_pages} page images uploaded → {img_base_url}")

            # Step 8 ─ Assign topic_group_id from section_path
            # Match test.py: ungrouped chunks each get a unique group ID
            section_to_gid: Dict[str, int] = {}
            gid_counter = 0
            for ci, enriched in enumerate(enriched_chunks):
                sp = enriched.get("section_path") or []
                key = f" > ".join(sp) if sp else f"__ungrouped_{ci}__"
                if key not in section_to_gid:
                    section_to_gid[key] = gid_counter
                    gid_counter += 1

            # Step 9 ─ Insert each chunk into Oracle
            cont_by_pair = {(c["page_a"], c["page_b"]): c for c in continuity_map}
            oracle_ids: List[int | None] = []
            for sequence, (group, enriched) in enumerate(zip(chunk_groups, enriched_chunks), start=1):
                ci = sequence - 1  # 0-based index matching Step 8
                sp = enriched.get("section_path") or []
                section_key    = " > ".join(sp) if sp else f"__ungrouped_{ci}__"
                topic_group_id = section_to_gid.get(section_key, 0)

                # Build continuity_notes from continuity_map notes for page pairs in this chunk
                pages = group["pages"]
                cont_notes = []
                for j in range(len(pages) - 1):
                    c = cont_by_pair.get((pages[j], pages[j + 1]), {})
                    if c:
                        note = c.get("notes", "")
                        if note:
                            cont_notes.append(note)
                continuity_notes_str = "; ".join(cont_notes) if cont_notes else None

                chunk_id = insert_knowledge_chunk(
                    doc_id=doc_id,
                    chunk_sequence=sequence,
                    content="\n\n".join(group.get("raw_texts", [])),
                    description=enriched.get("description", ""),
                    img_url_path=img_base_url,
                    title=enriched.get(
                        "title",
                        f"{source_file.stem} — pp.{group['page_start']}–{group['page_end']}",
                    ),
                    page_start=group["page_start"],
                    page_end=group["page_end"],
                    content_type=enriched.get("content_type", group["content_type"]),
                    section_path=" > ".join(
                        enriched.get("section_path") or group.get("section_path") or []
                    ),
                    key_topics=", ".join(
                        enriched.get("key_topics") or group.get("topics") or []
                    ),
                    keywords=", ".join(
                        enriched.get("keywords") or group.get("keywords") or []
                    ),
                    is_table=1 if enriched.get("is_table") else 0,
                    table_summary=enriched.get("table_summary") or "",
                    continuity_type=group.get("continuity_type", "none"),
                    is_continuation=1 if group.get("continuity_type", "none") not in ("none", "") else 0,
                    prev_chunk_id=None,   # wired in step 10
                    next_chunk_id=None,
                    topic_group_id=topic_group_id,
                    sequential_order=sequence - 1,  # 0-based position
                    continuity_notes=continuity_notes_str,
                    topic_complete=1,  # default, refined in step 10
                )
                oracle_ids.append(chunk_id)
                total_image_chunks += 1

            # Step 10 ─ Wire prev/next, sequential_order, related_chunk_ids, topic_complete
            # Build topic sets per chunk for related_chunk_ids computation
            chunk_topic_sets: List[set] = []
            for enriched, group in zip(enriched_chunks, chunk_groups):
                topics = enriched.get("key_topics") or group.get("topics") or []
                chunk_topic_sets.append({t.strip().lower() for t in topics if t.strip()})

            for i, oid in enumerate(oracle_ids):
                if oid is None:
                    continue
                prev_id = oracle_ids[i - 1] if i > 0 else None
                next_id = oracle_ids[i + 1] if i < len(oracle_ids) - 1 else None

                # topic_complete: 0 if there's a next chunk and continuity continues
                ctype = chunk_groups[i].get("continuity_type", "none")
                tc = 0 if (next_id is not None and ctype not in ("none", "")) else 1

                # related_chunk_ids: same-doc chunks sharing key_topics
                my_topics = chunk_topic_sets[i]
                related = []
                if my_topics:
                    for j, other_oid in enumerate(oracle_ids):
                        if j == i or other_oid is None:
                            continue
                        if my_topics & chunk_topic_sets[j]:
                            related.append(str(other_oid))
                related_str = ",".join(related[:10]) if related else None

                update_chunk_links(
                    oid, prev_id, next_id,
                    sequential_order=i,
                    related_chunk_ids=related_str,
                    topic_complete=tc,
                )

            # Step 11 ─ Generate document profile and insert
            print(f"[INFO] Generating doc profile for doc_id={doc_id}…")
            profile = _generate_doc_profile(source_file.stem, enriched_chunks)
            insert_knowledge_doc_profile(
                doc_id=doc_id,
                doc_name=source_file.stem,
                description=profile["description"],
                key_topics=profile["key_topics"],
                key_entities=profile["key_entities"],
                unique_keywords=profile["unique_keywords"],
                shared_keywords=profile.get("shared_keywords"),
                title=doc_title,          # user-entered title (not LLM)
                purpose=profile.get("purpose"),
                domain=profile.get("domain"),
                document_type=profile.get("document_type"),
                differentiators=profile.get("differentiators"),
            )

            # Step 12 ─ Embed + Qdrant ingest
            print(f"[INFO] Embedding {len(chunk_groups)} chunks for doc_id={doc_id}…")
            embedded_records = embed_doc_chunks(doc_id)
            ingest_to_qdrant(embedded_records)
            print(f"[SUCCESS] doc_id={doc_id}: {len(embedded_records)} embeddings → Qdrant.")

            log.append({
                "source_file": source_file.name,
                "pages":       total_pages,
                "chunks":      len(chunk_groups),
                **norm_meta,
                "status": "success",
            })

    print(f"[COMPLETE] {total_image_chunks} semantic chunks processed.")
    return {
        "run_id":               run_id,
        "total_uploaded_files": len(file_paths),
        "total_image_chunks":   total_image_chunks,
        "metadata":             norm_meta,
        "details":              log,
    }
