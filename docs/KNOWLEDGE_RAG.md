# Knowledge ingestion and in-memory RAG (Iteration 6)

## Supported formats

- `.txt` (UTF-8)
- `.md` (UTF-8)
- text-based `.pdf` (local `pypdf` extraction; **no OCR**)

Rejected: empty files, invalid UTF-8, unsupported extensions, encrypted PDFs, image-only PDFs, oversize bytes/pages/characters, MIME mismatches for media types.

## Upload limits (defaults)

| Setting | Default |
|---------|---------|
| `OPENAI_EMBEDDING_MODEL` | `text-embedding-3-small` |
| `RAG_TOP_K` | 4 |
| `RAG_MIN_SIMILARITY` | 0.30 |
| `RAG_CHUNK_MAX_CHARACTERS` | 1600 |
| `RAG_CHUNK_OVERLAP_CHARACTERS` | 200 |
| `RAG_MAX_UPLOAD_BYTES` | 5242880 (5 MiB) |
| `RAG_MAX_DOCUMENT_CHARACTERS` | 500000 |
| `RAG_MAX_PDF_PAGES` | 100 |
| `RAG_EMBEDDING_BATCH_SIZE` | 32 |
| `RAG_EMBEDDING_TIMEOUT_SECONDS` | 10 |

Validated at startup (`load_rag_config`). Invalid values fail with a clear config error (no secrets).

## Parsing and chunking

1. Sanitize display filename (never treat as a filesystem path).
2. Parse in memory (`BytesIO` for PDFs).
3. SHA-256 content hash for deduplication (content, not filename).
4. Paragraph-aware chunking with bounded size and overlap; oversized paragraphs are split.
5. Stable chunk indexes; no empty chunks.

## Moderation before embedding

Every chunk is moderated with the existing OpenAI Moderation abstraction and a **document-ingestion** policy (stricter than classroom Q&A). Failures, timeouts, or policy rejects insert **nothing**. Embedding is never called for rejected documents.

## Atomic ingestion

Parse → (dedupe short-circuit) → chunk → moderate all → embed all → validate vectors → insert. Any failure before insert leaves the store unchanged.

## In-memory vector store

`InMemoryKnowledgeStore` is process-local (shared across WebSocket sessions in one Python process).

- Cosine similarity with dimension checks, zero-magnitude → 0, non-finite rejection
- Top-k with minimum similarity and stable tie-break
- Async lock for writes/searches

**Limitations:** cleared on restart; multiple Uvicorn workers = separate stores; no distributed sync; upload has no authentication yet (demo/assignment only).

## Retrieval in the voice pipeline

```text
STT → InputSafety → RetrievalProcessor → user aggregator → LLM → OutputSafety → TTS
```

Retrieval runs only after input safety **ALLOW**, for question-like interruptions / Q&A utterances. Not for partials, control messages, slide narration, or safety redirect/hold.

If the store is empty or embedding/search fails: continue without RAG (no crash, no invented sources); record a content-free diagnostic event.

## Temporary per-turn context

Uses Pipecat `LLMMessagesTransformFrame` to strip prior RAG system messages (marker `<<<RAG_REF_MATERIAL>>>`) and `LLMMessagesAppendFrame` to inject a fresh untrusted-reference system message for the current turn only. Base tutor/safety prompt is not overwritten. Document text is wrapped as data inside `<retrieved_context>` with explicit “do not follow instructions” guidance.

## Source attribution

Labels like `[S1] Name, page N`. Frontend receives `knowledge.retrieval` metadata only (no chunk text, scores, or embeddings).

## Prompt-injection defenses

- Wrap as untrusted reference
- Strip/replace each turn
- Lesson/safety/control actions remain application-owned
- Residual risk remains; prompts alone cannot eliminate injection

## Privacy

In-memory RAG events may include query id, counts, source document ids, latencies, fallback codes — never student questions, chunk text, embeddings, or API keys. Not persisted yet.

## Latency and cost

Each allowed question may add one embedding call plus search. Ingestion costs moderation + embeddings per chunk. Prefer modest uploads.

## HTTP API

- `POST /knowledge/documents` — multipart file upload
- `GET /knowledge/status` — counts, model name, limits (no secrets/content)

## Production improvements later

Authn/authz for upload, durable vector storage, multi-worker sync, operator review queue, retention policy, live latency SLOs.
