### Oracle

| Variable | Description |
| --- | --- |
| `DB_USER` | Oracle username |
| `DB_PASSWORD` | Oracle password |
| `DB_HOST` | Oracle host |
| `DB_PORT` | Oracle port |
| `DB_SID` | Oracle SID |
| `DB_SERVICE_NAME` | Oracle service name, if used instead of SID |

### LLM and Embeddings

| Variable | Description |
| --- | --- |
| `LLM_BASE_URL` | OpenAI-compatible API base URL |
| `LLM_TOKEN` | API token |
| `CERT_PATH` | Optional CA bundle path |

Model constants are defined in `config/llm.py`:

- `CHAT_MODEL`
- `EMBED_MODEL`
- retrieval thresholds
- chunk sizing
- retry and rate-limit settings
- page-image conversion settings

### Qdrant

| Variable | Description |
| --- | --- |
| `QDRANT_URL` | Qdrant endpoint |
| `QDRANT_KEY` | Qdrant API key |

The collection name is configured in `config/llm.py`.

### SMB/CIFS Share

| Variable | Description |
| --- | --- |
| `SMB_USER` | Share username |
| `SMB_PASSWORD` | Share password |
| `SMB_SERVER` | Share server name |
| `SMB_SHARE` | UNC share path |
| `SMB_DOMAIN` | Share domain, if required |

## Database Tables

The application expects three main Oracle tables. Names can be changed, but related SQL in `services/db_service.py` must be updated.

### `KNOWLEDGE_DOCS`

Stores uploaded document records and metadata.

| Column | Purpose |
| --- | --- |
| `ID` | Primary key |
| `FILEPATH` | Stored path on the SMB share |
| `FILE_TYPE` | File extension |
| `TITLE` | User-provided document title |

### `KNOWLEDGE_DOC_PROFILES`

Stores LLM-generated document-level profiles used for retrieval scoring.

| Column | Purpose |
| --- | --- |
| `DOC_ID` | Related document ID |
| `TITLE` | Document title |
| `DESCRIPTION` | Generated summary |
| `PURPOSE` | Generated document purpose |
| `DOMAIN` | Generated domain/category |
| `DOCUMENT_TYPE` | Generated type classification |
| `KEY_TOPICS` | Important topics |
| `KEY_ENTITIES` | Important entities |
| `UNIQUE_KEYWORDS` | Distinguishing keywords |
| `SHARED_KEYWORDS` | Common keywords |
| `DIFFERENTIATORS` | Generated notes explaining what makes the document distinct |

### `KNOWLEDGE_CHUNKS_FROM_DOCS`

Stores chunk-level content, page references, image paths, and relationships.

| Column | Purpose |
| --- | --- |
| `ID` | Primary key |
| `DOC_ID` | Related document ID |
| `CHUNK_SEQUENCE` | Chunk order within the document |
| `PAGE_START` / `PAGE_END` | Page range |
| `CONTENT` | Extracted or generated chunk text |
| `DESCRIPTION` | Generated chunk description |
| `KEY_TOPICS` | Chunk-level topics |
| `PREV_CHUNK_ID` / `NEXT_CHUNK_ID` | Neighbor links |
| `SEQUENTIAL_ORDER` | Global ordering value |
| `RELATED_CHUNK_IDS` | Related chunk references |
| `TOPIC_COMPLETE` | Whether the chunk fully covers its topic |
| `TOPIC_GROUP_ID` | Group ID for multi-chunk topics |
| `CONTINUITY_TYPE` | Continuity relationship label |
| `CONTINUITY_NOTES` | Notes about continuation |
| `IMG_URL_PATH` | Stored page image path |
