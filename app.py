"""
Document Knowledge Base RAG System (FastAPI)

Combines Phase-1 document management (upload → preprocess → embed → Qdrant)
with the Qdrant-Chat interface (semantic search + GPT-4.1 answers).

Run:
    cd Phase1
    uvicorn app:app --host 0.0.0.0 --port 8001 --reload

Speed notes:
  • Oracle connection pool is created once at module import time (config/db.py).
  • OpenAI and Qdrant clients are lazy singletons — created on first use.
  • Long-running preprocess jobs run in FastAPI BackgroundTasks (non-blocking).
  • Embedding retries use exponential back-off with jitter.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from routers import chat, dms

# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="Document Knowledge Base RAG System", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────

app.include_router(chat.router)
app.include_router(dms.router)

# ── Static files ──────────────────────────────────────────────────────────────

_STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


# ── Root ──────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def serve_index():
    return HTMLResponse((_STATIC_DIR / "index.html").read_text(encoding="utf-8"))


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8001, reload=True)
