#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#
import asyncio
import os
from contextlib import asynccontextmanager
from typing import Any, Dict

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware

# Load environment variables
load_dotenv(override=True)

from agent import run_bot
from embedding_service import OpenAIEmbeddingClient, load_rag_config
from knowledge_api import router as knowledge_router
from knowledge_store import SHARED_KNOWLEDGE_STORE
from lesson_protocol import build_ws_url
from moderation_service import OpenAIModerationClient, load_safety_config


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handles FastAPI startup and shutdown."""
    safety_config = load_safety_config()
    rag_config = load_rag_config()
    api_key = os.getenv("OPENAI_API_KEY")
    moderation = OpenAIModerationClient(
        api_key=api_key,
        model=safety_config.moderation_model,
        timeout_seconds=safety_config.timeout_seconds,
    )
    embedding = OpenAIEmbeddingClient(
        api_key=api_key,
        model=rag_config.embedding_model,
        timeout_seconds=rag_config.embedding_timeout_seconds,
        batch_size=rag_config.embedding_batch_size,
    )
    app.state.knowledge = {
        "config": rag_config,
        "store": SHARED_KNOWLEDGE_STORE,
        "moderation_client": moderation,
        "embedding_client": embedding,
        "upload_enabled": True,
    }
    yield


# Initialize FastAPI app with lifespan manager
app = FastAPI(lifespan=lifespan)

# Configure CORS to allow requests from any origin
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(knowledge_router)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    print("WebSocket connection accepted")
    try:
        await run_bot(websocket)
    except Exception as e:
        print(f"Exception in run_bot: {e}")


@app.post("/connect")
async def bot_connect(request: Request) -> Dict[Any, Any]:
    host = request.headers.get("host") or request.url.hostname or "localhost:7860"
    scheme = request.url.scheme or "http"
    # Prefer forwarded proto when present (reverse proxies).
    forwarded = request.headers.get("x-forwarded-proto")
    if forwarded:
        scheme = forwarded.split(",")[0].strip()
    return {"ws_url": build_ws_url(scheme=scheme, host=host, path="/ws")}


async def main():
    tasks = []
    try:
        config = uvicorn.Config(app, host="0.0.0.0", port=7860)
        server = uvicorn.Server(config)
        tasks.append(server.serve())

        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        print("Tasks cancelled (probably due to shutdown).")


if __name__ == "__main__":
    asyncio.run(main())
