"""DMS API router — document upload, listing, preprocessing, and metadata options."""
from __future__ import annotations

import shutil
import tempfile
import traceback
import pythoncom
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from choices import PROCESS_LIST, MODULE_LIST, EQUIPMENT_TYPE_LIST, PACKAGE_MAP
from config.share_drive import HICP_SHARE_DRIVE_CONFIG
from services.db_service import (
    fetch_knowledge_documents,
    filepath_exists_in_db,
    get_doc_title,
    get_knowledge_documents,
    insert_knowledge_document,
)
from services.preprocess_service import get_document_description, process_uploaded_documents
from services.share_service import connect_to_share, path_exists_on_share, write_file_to_share

router = APIRouter(prefix="/api/dms")

# ── In-memory task tracking ───────────────────────────────────────────────────
# Maps task_id → {"status": "pending"|"running"|"done"|"error", "detail": ...}
_tasks: dict[str, dict] = {}

ALLOWED_EXTENSIONS = {"PDF", "DOC", "DOCX"}


# ── Metadata options ──────────────────────────────────────────────────────────

@router.get("/metadata-options", include_in_schema=True)
async def metadata_options():
    """Return available process / module / equipment / package filter values."""
    all_packages = sorted({p for pkgs in PACKAGE_MAP.values() for p in pkgs})
    return JSONResponse({
        "process":    sorted(PROCESS_LIST),
        "module":     sorted(MODULE_LIST),
        "eq_type":    sorted(EQUIPMENT_TYPE_LIST),
        "package":    all_packages,
    })


# ── Upload ────────────────────────────────────────────────────────────────────

@router.post("/upload")
async def dms_upload(
    background_tasks: BackgroundTasks,
    file:           UploadFile = File(...),
    title:          str = Form(...),
    process:        str = Form(...),
    module:         list[str] = Form(default=[]),
    equipment_type: list[str] = Form(default=[]),
    package:        list[str] = Form(default=[]),
):
    """Upload a document to the SMB share and register it in Oracle DB.

    Always triggers preprocessing immediately after upload.
    """
    # ── Validate title ────────────────────────────────────────────────────────
    title_val = title.strip()
    if not title_val:
        raise HTTPException(status_code=422, detail="Document title is required.")

    # ── Validate process ──────────────────────────────────────────────────────
    process_val = process.strip().upper()
    if not process_val:
        raise HTTPException(status_code=422, detail="Process selection is required.")
    valid_processes = {p.upper() for p in PROCESS_LIST}
    if process_val not in valid_processes:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid process '{process_val}'. Valid values: {', '.join(sorted(valid_processes))}.",
        )

    # ── Validate module (required, ≥1) ───────────────────────────────────────
    module_vals = [m.strip().upper() for m in module if m.strip()]
    if not module_vals:
        raise HTTPException(status_code=422, detail="At least one module must be selected.")
    valid_modules = {m.upper() for m in MODULE_LIST}
    invalid_mods = [m for m in module_vals if m not in valid_modules]
    if invalid_mods:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid module(s): {', '.join(invalid_mods)}. Valid values: {', '.join(sorted(valid_modules))}.",
        )

    # ── Validate equipment type (optional) ───────────────────────────────────
    eq_type_vals = [e.strip().upper() for e in equipment_type if e.strip()]
    if eq_type_vals:
        valid_eq = {e.upper() for e in EQUIPMENT_TYPE_LIST}
        invalid_eq = [e for e in eq_type_vals if e not in valid_eq]
        if invalid_eq:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid equipment type(s): {', '.join(invalid_eq)}. Valid values: {', '.join(sorted(valid_eq))}.",
            )

    # ── Validate package (optional) ───────────────────────────────────────────
    package_vals = [p.strip().upper() for p in package if p.strip()]
    if package_vals:
        all_packages = {p.upper() for pkgs in PACKAGE_MAP.values() for p in pkgs}
        invalid_pkg = [p for p in package_vals if p not in all_packages]
        if invalid_pkg:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid package(s): {', '.join(invalid_pkg)}. Valid values: {', '.join(sorted(all_packages))}.",
            )

    # ── File extension check ──────────────────────────────────────────────────
    filename = file.filename or "upload"
    ext = Path(filename).suffix[1:].upper()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {ext}")

    file_bytes = await file.read()

    # ── Check for duplicate name on share & auto-increment ────────────────────
    share_root = HICP_SHARE_DRIVE_CONFIG["shareName"]
    share_subdir = "Files"
    connect_to_share()
    orig_stem   = Path(filename).stem
    orig_suffix = Path(filename).suffix
    final_name  = filename
    counter     = 1
    while True:
        rel_path = f"{share_subdir}/{final_name}"
        unc_path = f"{share_root}\\{rel_path}".replace("/", "\\")
        if not path_exists_on_share(unc_path) and not filepath_exists_in_db(rel_path):
            break
        final_name = f"{orig_stem} ({counter}){orig_suffix}"
        counter += 1

    renamed = final_name != filename

    # ── Generate document description ─────────────────────────────────────────
    doc_description: str | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=Path(final_name).suffix, delete=False) as tmp:
            tmp_path = Path(tmp.name)
            tmp.write(file_bytes)
        doc_description = get_document_description(tmp_path)
        tmp_path.unlink(missing_ok=True)
    except Exception as exc:
        print(f"[DMS] Description generation failed: {exc}")

    # ── Write to share ────────────────────────────────────────────────────────
    share_filename = f"{share_subdir}/{final_name}"
    if not write_file_to_share(share_filename, file_bytes):
        raise HTTPException(status_code=500, detail="Failed to save file to shared drive.")

    # ── Insert into Oracle DB ─────────────────────────────────────────────────
    module_val    = ",".join(module_vals)
    eq_type_val   = ",".join(eq_type_vals) or None
    package_val   = ",".join(package_vals) or None

    doc_id = insert_knowledge_document(
        filepath=share_filename,
        file_type=ext,
        process=process_val,
        module=module_val,
        eq_type=eq_type_val,
        package=package_val,
        title=title_val,
    )
    if doc_id is None:
        raise HTTPException(status_code=500, detail="Failed to save file record to Oracle DB.")

    # ── Always trigger preprocessing ──────────────────────────────────────────
    task_id = str(uuid.uuid4())
    _tasks[task_id] = {"status": "pending", "doc_id": doc_id, "detail": None}
    background_tasks.add_task(_run_preprocess_task, task_id, doc_id, file_bytes, final_name, doc_title=title_val)

    return JSONResponse({
        "success":          True,
        "filename":         final_name,
        "renamed":          renamed,
        "doc_id":           doc_id,
        "doc_description":  doc_description,
        "task_id":          task_id,
        "message":          f"File '{final_name}' uploaded successfully. Preprocessing started (task_id={task_id}).",
    })


# ── Document listing ──────────────────────────────────────────────────────────

@router.get("/documents")
async def dms_documents():
    """Return all documents from KNOWLEDGE_DOCS as list[dict]."""
    print("[DMS] GET /api/dms/documents")
    docs = fetch_knowledge_documents()
    return JSONResponse({"documents": docs, "total": len(docs)})


# ── Preprocess ────────────────────────────────────────────────────────────────

@router.post("/preprocess/{doc_id}")
async def dms_preprocess(doc_id: int, background_tasks: BackgroundTasks):
    """Trigger preprocessing for an existing document (background task)."""
    from services.db_service import get_knowledge_documents
    df = get_knowledge_documents()
    if df.empty:
        raise HTTPException(status_code=404, detail="No documents found.")
    row = df[df["ID"] == doc_id]
    if row.empty:
        raise HTTPException(status_code=404, detail=f"Document {doc_id} not found.")

    filepath = str(row.iloc[0]["FILEPATH"])
    task_id = str(uuid.uuid4())
    _tasks[task_id] = {"status": "pending", "doc_id": doc_id, "detail": None}
    background_tasks.add_task(_run_preprocess_from_share, task_id, doc_id, filepath)
    return JSONResponse({
        "success": True,
        "task_id": task_id,
        "doc_id":  doc_id,
        "message": f"Preprocessing started for doc_id={doc_id} (task_id={task_id}).",
    })


@router.get("/preprocess/status/{task_id}")
async def dms_preprocess_status(task_id: str):
    """Poll preprocessing task status."""
    task = _tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id!r} not found.")
    return JSONResponse(task)


# ── Background task helpers ───────────────────────────────────────────────────

def _run_preprocess_task(task_id: str, doc_id: int, file_bytes: bytes, filename: str, doc_title: str | None = None) -> None:
    """Background: write file to temp, then run the full preprocessing pipeline."""
    _tasks[task_id]["status"] = "running"
    pythoncom.CoInitialize()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            local_file = Path(tmp) / filename
            local_file.write_bytes(file_bytes)
            result = process_uploaded_documents([local_file], doc_id, doc_title=doc_title)
        _tasks[task_id]["status"] = "done"
        _tasks[task_id]["detail"] = result
    except Exception as exc:
        traceback.print_exc()
        _tasks[task_id]["status"] = "error"
        _tasks[task_id]["detail"] = str(exc)
    finally:
        pythoncom.CoUninitialize()


def _run_preprocess_from_share(task_id: str, doc_id: int, share_filepath: str) -> None:
    """Background: download file from share, then run the full preprocessing pipeline."""
    import smbclient

    _tasks[task_id]["status"] = "running"
    pythoncom.CoInitialize()
    try:
        share_root = HICP_SHARE_DRIVE_CONFIG["shareName"]
        rel_path   = str(share_filepath).lstrip("\\/")
        unc_path   = f"{share_root}\\{rel_path}".replace("/", "\\")
        filename   = Path(share_filepath).name
        doc_title  = get_doc_title(doc_id)

        with tempfile.TemporaryDirectory() as tmp:
            local_file = Path(tmp) / filename
            with smbclient.open_file(unc_path, mode="rb") as remote:
                local_file.write_bytes(remote.read())
            result = process_uploaded_documents([local_file], doc_id, doc_title=doc_title)

        _tasks[task_id]["status"] = "done"
        _tasks[task_id]["detail"] = result
    except Exception as exc:
        traceback.print_exc()
        _tasks[task_id]["status"] = "error"
        _tasks[task_id]["detail"] = str(exc)
