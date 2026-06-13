"""LLM, embedding, and Qdrant configuration constants."""
import os

# ── OpenAI-compatible API ─────────────────────────────────────────────────────
CERT_PATH    = os.getenv("CERT_PATH",    r"")
BASE_URL     = os.getenv("LLM_BASE_URL", "")
TOKEN        = os.getenv("LLM_TOKEN",    "")
EMBED_MODEL  = "sfr-embedding-mistral"
CHAT_MODEL   = "gpt-4.1"

# ── Qdrant ────────────────────────────────────────────────────────────────────
QDRANT_URL  = os.getenv("QDRANT_URL", "")
QDRANT_KEY  = os.getenv("QDRANT_KEY", "")
COLLECTION  = ""

# ── Chat / retrieval tuning ───────────────────────────────────────────────────
TOP_K              = 3
WINDOW_HARD        = 3
SIM_THRESHOLD      = 0.25
EXPAND_MAX         = 4
MAX_CONTEXT_PAGES  = 10

# ── Embedding pipeline tuning ─────────────────────────────────────────────────
MAX_CHARS            = 6000
OVERLAP              = 400
MAX_RETRIES          = 8
BASE_RETRY_SECONDS   = 2.0
MAX_RETRY_SECONDS    = 45.0
MIN_REQUEST_INTERVAL = 4.2  # seconds between embedding API calls

# ── Preprocessing ─────────────────────────────────────────────────────────────
MAX_WORKERS = 4   # parallel image-analysis threads

# ── LLM Temperature Control ───────────────────────────────────────────────────
# Lower temps (0.0) = deterministic; higher temps allow more variation
TEMP_PAGE_DESCRIBE  = 0.0   # Vision: describe current page
TEMP_CONTINUITY     = 0.0   # Analyze continuity between pages
TEMP_CHUNKING       = 0.0   # Analyze & enrich chunks
TEMP_PROFILING      = 0.0   # Generate document profile
TEMP_DOC_SCORING    = 0.0   # Score documents for query relevance
TEMP_ANSWERING      = 0.3   # Final answer generation (slight creativity)
TEMP_QUERY_REWRITE  = 0.0   # Rewrite query for retrieval

# ── Image Conversion Config ───────────────────────────────────────────────────
IMAGE_DPI    = 150         # DPI for PDF → PNG conversion
IMAGE_FORMAT = "PNG"       # Output image format

# ── Rate Limiting Config ──────────────────────────────────────────────────────
EMBED_RPM_LIMIT  = 15                      # embedding requests per minute
CHAT_RPM_LIMIT   = 15                      # chat requests per minute
MIN_EMBED_DELAY  = 60.0 / EMBED_RPM_LIMIT  # seconds between embedding calls
MIN_CHAT_DELAY   = 60.0 / CHAT_RPM_LIMIT   # seconds between chat calls
RETRY_BASE_DELAY = 10                      # seconds; exponential backoff multiplier
