"""HTTP knowledge upload/status API (in-memory RAG)."""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import JSONResponse

from embedding_service import EmbeddingClient, RagConfig
from knowledge_ingestion import SUPPORTED_EXTENSIONS, IngestionError, ingest_document
from knowledge_store import InMemoryKnowledgeStore, SHARED_KNOWLEDGE_STORE
from moderation_service import ModerationClient


router = APIRouter(prefix="/knowledge", tags=["knowledge"])


def _deps(request: Request) -> Dict[str, Any]:
    return getattr(request.app.state, "knowledge", {})


@router.get("/status")
async def knowledge_status(request: Request) -> Dict[str, Any]:
    deps = _deps(request)
    config: RagConfig = deps.get("config") or RagConfig()
    store: InMemoryKnowledgeStore = deps.get("store") or SHARED_KNOWLEDGE_STORE
    return {
        "storage_type": "in_memory",
        "document_count": store.document_count,
        "chunk_count": store.chunk_count,
        "embedding_model": config.embedding_model,
        "upload_enabled": bool(config.upload_enabled and deps.get("upload_enabled", True)),
        "supported_file_types": sorted(SUPPORTED_EXTENSIONS),
        "limits": {
            "max_upload_bytes": config.max_upload_bytes,
            "max_document_characters": config.max_document_characters,
            "max_pdf_pages": config.max_pdf_pages,
            "chunk_max_characters": config.chunk_max_characters,
            "chunk_overlap_characters": config.chunk_overlap_characters,
            "top_k": config.top_k,
            "min_similarity": config.min_similarity,
        },
    }


@router.post("/documents")
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
) -> JSONResponse:
    deps = _deps(request)
    config: RagConfig = deps.get("config") or RagConfig()
    if not config.upload_enabled or not deps.get("upload_enabled", True):
        return JSONResponse(
            status_code=503,
            content={"error": {"code": "UPLOAD_DISABLED", "message": "Knowledge upload is disabled"}},
        )

    store: InMemoryKnowledgeStore = deps.get("store") or SHARED_KNOWLEDGE_STORE
    moderation: Optional[ModerationClient] = deps.get("moderation_client")
    embedding: Optional[EmbeddingClient] = deps.get("embedding_client")
    if moderation is None or embedding is None:
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "code": "SERVICE_UNAVAILABLE",
                    "message": "Knowledge ingestion is temporarily unavailable",
                }
            },
        )

    data = await file.read()
    try:
        result = await ingest_document(
            filename=file.filename,
            data=data,
            content_type=file.content_type,
            config=config,
            store=store,
            moderation_client=moderation,
            embedding_client=embedding,
        )
    except IngestionError as exc:
        return JSONResponse(
            status_code=exc.http_status,
            content={"error": {"code": exc.code, "message": exc.message}},
        )
    except Exception:  # noqa: BLE001
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "Unexpected knowledge ingestion failure",
                }
            },
        )

    return JSONResponse(
        status_code=200,
        content={
            "document_id": result.document_id,
            "name": result.name,
            "chunk_count": result.chunk_count,
            "duplicate": result.duplicate,
            "status": result.status,
        },
    )
