# Document Knowledge Base RAG System

A FastAPI-based Retrieval-Augmented Generation (RAG) application for uploading documents, preprocessing them into searchable chunks, and asking grounded questions over the resulting knowledge base.

The project combines:

- A browser-based chat and document-management UI
- Document upload and metadata capture
- PDF/DOC/DOCX preprocessing
- LLM-assisted page and chunk analysis
- Vector search with Qdrant
- Oracle-backed document and chunk metadata
- SMB/CIFS-backed document storage
- Source-aware answer generation with an OpenAI-compatible API

> Public repo note: the original project used organization-specific naming and metadata values. Those values have been intentionally omitted from this README. Replace the generic metadata categories with your own domain vocabulary before deploying. 

## Features

- Upload PDF, DOC, and DOCX documents through the web UI.
- Store document metadata in Oracle.
- Save source documents and generated page assets on an SMB/CIFS share.
- Convert documents into page images and semantic chunks.
- Use an LLM to describe pages, profile documents, and enrich chunk metadata.
- Embed chunks and index them in Qdrant for semantic retrieval.
- Ask natural-language questions through the chat UI.
- Retrieve relevant chunks, expand context, and generate grounded answers.
- Filter document search using configurable metadata fields.
- Generate follow-up question suggestions from the active knowledge base.

## Architecture

```text
Browser SPA
    |
    v
FastAPI application
    |
    +-- Oracle DB: document records, chunk metadata, generated profiles
    |
    +-- Qdrant: vector index for semantic chunk search
    |
    +-- SMB/CIFS share: uploaded files and page image assets
    |
    +-- OpenAI-compatible API: chat, page analysis, profiling, embeddings
```

## Metadata Customization

This project includes metadata fields named `process`, `module`, `equipment_type`, and `package`.

These are project-specific labels from the original use case. For a public or reusable version, treat them as placeholders.

To customize these fields:

1. Update the option lists in `choices.py`.
2. Rename UI labels in `static/index.html` and `static/app.js` if needed.
3. Update validation and form field names in `routers/dms.py`.
4. Update database column names and related queries in `services/db_service.py` if you change the schema.
5. Update Qdrant payload filter construction in `services/chat_service.py`.

If you do not need metadata filtering, you can remove these fields from the upload form, request payloads, validation logic, database schema, and retrieval filters.

## Prerequisites

- Python 3.11+ or 3.12+
- Oracle Database
- Qdrant instance
- SMB/CIFS share
- OpenAI-compatible API endpoint for chat and embeddings
- Windows with Microsoft Word installed, only if DOC/DOCX conversion relies on Word COM automation

Install the Python dependencies from `requirements.txt`:

```bash
pip install -r requirements.txt
```

Depending on your preprocessing path, you may also need document-conversion or OCR-related packages used by your local environment.

## Setup

1. Clone the repository:

```bash
git clone https://github.com/<your-username>/<your-repo>.git
cd <your-repo>
```

2. Create and activate a virtual environment:

```bash
python -m venv rag_env
rag_env\Scripts\activate
```

For macOS or Linux:

```bash
source rag_env/bin/activate
```

3. Install dependencies:

```bash
pip install -r requirements.txt
```

4. Set environment variables.

PowerShell example:

```powershell
$env:DB_USER = "your_db_user"
$env:DB_PASSWORD = "your_db_password"
$env:DB_HOST = "your_db_host"
$env:DB_PORT = "1521"
$env:DB_SID = "your_sid"
$env:LLM_BASE_URL = "https://your-llm-endpoint.example.com"
$env:LLM_TOKEN = "your_token"
$env:QDRANT_URL = "https://your-qdrant-endpoint.example.com"
$env:QDRANT_KEY = "your_qdrant_key"
$env:SMB_USER = "your_share_user"
$env:SMB_PASSWORD = "your_share_password"
$env:SMB_SERVER = "your_share_server"
$env:SMB_SHARE = "\\your-share-server\your-share"
$env:SMB_DOMAIN = "your_domain"
```

5. Run the app:

```bash
python app.py
```

The app runs at:

```text
http://localhost:8001
```

You can also run with Uvicorn:

```bash
uvicorn app:app --host 0.0.0.0 --port 8001 --reload
```

## Configuration

The app reads configuration from environment variables. Do not commit real credentials, internal hostnames, API tokens, or private share paths. For detail configuration such as database setup and expected relational database tables, kindly refer to `Configuration.md`