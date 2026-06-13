"""Knowledge-base chat router — browse ShareDrive, load docs, chat with GPT-4.1."""
from __future__ import annotations

import json
import traceback
import uuid
from datetime import datetime
from pathlib import Path

import httpx
import openai
import smbclient
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from config.llm import BASE_URL, CERT_PATH, CHAT_MODEL, TOKEN
from config.share_drive import HICP_SHARE_DRIVE_CONFIG
from services.chat_service import run_chat
from services.share_service import connect_to_share
from services.db_service import query_matching_filenames
from .documents import (
    knowledge_base,
    extract_text_from_pdf,
    extract_text_from_docx,
    extract_text_from_pptx,
    extract_text_from_txt,
    process_image_bytes,
)

router = APIRouter()

VISION_MODEL = "gpt-image-1"

# Extensions shown in the ShareDrive browser
SUPPORTED_EXTENSIONS = {
    ".pdf", ".docx", ".doc", ".pptx", ".ppt",
    ".txt", ".md", ".csv",
    ".png", ".jpg", ".jpeg", ".gif", ".webp",
}
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

def _build_client() -> openai.OpenAI:
    return openai.OpenAI(
        api_key=TOKEN,
        base_url=BASE_URL,
        default_headers={"Authorization": f"Basic {TOKEN}"},
        http_client=httpx.Client(verify=CERT_PATH),
    )


def _extract_text(file_bytes: bytes, ext: str) -> tuple[str, dict | None]:
    """Return (extracted_text, image_data_or_None)."""
    ext = ext.lower()
    if ext == ".pdf":
        return extract_text_from_pdf(file_bytes), None
    if ext in (".docx", ".doc"):
        return extract_text_from_docx(file_bytes), None
    if ext in (".pptx", ".ppt"):
        return extract_text_from_pptx(file_bytes), None
    if ext in (".txt", ".md", ".csv", ".log", ".json", ".xml", ".yaml", ".yml"):
        return extract_text_from_txt(file_bytes), None
    if ext in _IMAGE_EXTENSIONS:
        mime = "image/png" if ext == ".png" else "image/jpeg"
        return "[Image file]", process_image_bytes(file_bytes, mime)
    raise ValueError(f"Unsupported file type: {ext}")


# ── ShareDrive browsing ───────────────────────────────────────────────────────

@router.get("/api/share/list")
async def list_share_files(path: str = "File"):
    """List files on the ShareDrive at the given relative path."""
    if not connect_to_share():
        raise HTTPException(status_code=503, detail="Cannot connect to ShareDrive")
    share_root = HICP_SHARE_DRIVE_CONFIG["shareName"]
    rel = path.strip("\\/ ").replace("/", "\\")  # normalise to \ for UNC only here
    unc = f"{share_root}\\{rel}" if rel else share_root
    try:
        entries = []
        for entry in smbclient.scandir(unc):
            name = entry.name
            if name.startswith("."):
                continue
            stat = entry.stat()
            is_dir = entry.is_dir()
            ext = Path(name).suffix.lower()
            if is_dir or ext in SUPPORTED_EXTENSIONS:
                entries.append({
                    "name": name,
                    "path": f"{rel}/{name}" if rel else name,  # forward slashes — safe in HTML attrs
                    "is_dir": is_dir,
                    "size": stat.st_size if not is_dir else None,
                    "modified": stat.st_mtime,
                })
        entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
        return JSONResponse({"entries": entries, "current_path": rel or "\\"})
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to list ShareDrive: {exc}")


@router.post("/api/share/filter-files")
async def filter_files_by_db(request: Request):
    """Query KNOWLEDGE_DOCS and return matching basenames for the given filters.

    Returns {"filenames": [...]} — a list of file basenames that have DB records
    matching any of the selected filter values (OR logic).
    Returns {"filenames": null} when no filter values are selected (show all).
    """
    filters = (await request.json()) or {}
    try:
        filenames = query_matching_filenames(filters)
        return JSONResponse({"filenames": filenames})
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"DB filter query failed: {exc}")


# ── Knowledge base management ─────────────────────────────────────────────────

@router.post("/api/kb/load")
async def load_from_share(request: Request):
    """Load a file from the ShareDrive into the in-memory knowledge base."""
    body = await request.json()
    share_path = (body.get("path") or "").strip()
    if not share_path:
        raise HTTPException(status_code=400, detail="path is required")

    # Prevent duplicates
    for s in knowledge_base["sources"]:
        if s.get("share_path") == share_path:
            raise HTTPException(status_code=400, detail="This file is already loaded")

    if not connect_to_share():
        raise HTTPException(status_code=503, detail="Cannot connect to ShareDrive")

    share_root = HICP_SHARE_DRIVE_CONFIG["shareName"]
    # Normalise: accept both / and \ separators from the client
    rel = share_path.strip("\\/").replace("/", "\\")
    unc = f"{share_root}\\{rel}"

    try:
        with smbclient.open_file(unc, mode="rb") as fh:
            file_bytes = fh.read()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read file from ShareDrive: {exc}")

    filename = Path(share_path).name
    ext = Path(filename).suffix.lower()

    _TYPE_MAP = {
        ".pdf": "pdf",
        ".docx": "document", ".doc": "document",
        ".pptx": "presentation", ".ppt": "presentation",
        ".txt": "text", ".md": "text", ".csv": "text",
    }
    source_type = _TYPE_MAP.get(ext, "image" if ext in _IMAGE_EXTENSIONS else None)
    if source_type is None:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {ext}")

    try:
        extracted_text, image_data = _extract_text(file_bytes, ext)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Error extracting text: {exc}")

    source_id = str(uuid.uuid4())
    source_meta = {
        "id": source_id,
        "name": filename,
        "share_path": share_path,
        "type": source_type,
        "size": len(file_bytes),
        "added_at": datetime.now().isoformat(),
        "has_image": image_data is not None,
        "text_preview": extracted_text[:200] + "..." if len(extracted_text) > 200 else extracted_text,
        "text_length": len(extracted_text),
    }
    knowledge_base["sources"].append(source_meta)
    knowledge_base["extracted_texts"][source_id] = {
        "text": extracted_text,
        "image_data": image_data,
    }
    return JSONResponse({
        "success": True,
        "source": source_meta,
        "message": f"Successfully loaded {filename} from ShareDrive",
    })


@router.get("/api/sources")
async def get_sources():
    """List all sources currently loaded in the knowledge base."""
    return JSONResponse({
        "sources": knowledge_base["sources"],
        "total": len(knowledge_base["sources"]),
    })


@router.delete("/api/source/{source_id}")
async def remove_source(source_id: str):
    """Remove a source from the knowledge base."""
    knowledge_base["sources"] = [s for s in knowledge_base["sources"] if s["id"] != source_id]
    knowledge_base["extracted_texts"].pop(source_id, None)
    return JSONResponse({"success": True, "message": "Source removed"})


# ── Chat ──────────────────────────────────────────────────────────────────────

@router.post("/api/chat")
async def chat(request: Request):
    body = await request.json()
    user_query = body.get("message", "").strip()
    if not user_query:
        raise HTTPException(status_code=400, detail="Message is required")

    # Filters from frontend: { process: [...], module: [...], eq_type: [...], package: [...] }
    filters: dict = body.get("filters") or {}

    result = run_chat(
        question=user_query,
        process=filters.get("process"),
        module=filters.get("module"),
        equipment_type=filters.get("eq_type"),   # frontend key is eq_type
        package=filters.get("package"),
    )
    return JSONResponse(result)

@router.post("/api/suggestions")
async def get_suggestions():
    if not knowledge_base["sources"]:
        return JSONResponse({
            "suggestions": ["Load documents from ShareDrive to get started!"]
        })
    client = _build_client()
    source_summaries = []
    for source in knowledge_base["sources"]:
        sid = source["id"]
        data = knowledge_base["extracted_texts"].get(sid, {})
        preview = (data.get("text", "")[:300]) if data.get("text") else "Image content"
        source_summaries.append(f"- {source['name']} ({source['type']}): {preview}")
    prompt = (
        "Based on the following knowledge base sources, provide 4 concise and actionable questions "
        "a user might ask to explore these documents. Return ONLY a JSON object with a key "
        '"suggestions" containing an array of question strings.\n\n'
        f"Sources:\n{chr(10).join(source_summaries[:10])}"
    )
    try:
        response = client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )
        text = (response.choices[0].message.content or "{}").strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1]) if len(lines) > 2 else text
        suggestions = json.loads(text).get("suggestions", [])[:4]
        return JSONResponse({"suggestions": suggestions})
    except Exception as e:
        traceback.print_exc()
        return JSONResponse({
            "suggestions": [
                "What is this document about?",
                "Summarize the key points",
                "What are the main topics covered?",
                "Explain the most important findings",
            ]
        })
