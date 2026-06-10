import time
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

from app.embeddings import EmbeddingService
from app.logging_config import setup_logging
from app.agent import classify_intent, suggest_sources
from app.rerank import rerank_chunks

load_dotenv()

logger = setup_logging()
embedding_service: EmbeddingService | None = None


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/health":
            return await call_next(request)

        started = time.perf_counter()
        logger.info("[http] %s %s started", request.method, request.url.path)
        try:
            response = await call_next(request)
            elapsed_ms = (time.perf_counter() - started) * 1000
            logger.info(
                "[http] %s %s completed status=%s durationMs=%.1f",
                request.method,
                request.url.path,
                response.status_code,
                elapsed_ms,
            )
            return response
        except Exception:
            elapsed_ms = (time.perf_counter() - started) * 1000
            logger.exception(
                "[http] %s %s failed durationMs=%.1f",
                request.method,
                request.url.path,
                elapsed_ms,
            )
            raise


@asynccontextmanager
async def lifespan(app: FastAPI):
    global embedding_service
    logger.info("[startup] loading embedding model...")
    started = time.perf_counter()
    embedding_service = EmbeddingService()
    elapsed_ms = (time.perf_counter() - started) * 1000
    logger.info(
        "[startup] ready model=%s dimensions=%s loadMs=%.1f",
        embedding_service.model_name,
        embedding_service.dimensions,
        elapsed_ms,
    )
    yield
    logger.info("[shutdown] ai-service stopping")


app = FastAPI(title="Starbot AI Service", version="1.0.0", lifespan=lifespan)
app.add_middleware(RequestLoggingMiddleware)


class EmbedRequest(BaseModel):
    texts: list[str]


class EmbedResponse(BaseModel):
    embeddings: list[list[float]]
    model: str
    dimensions: int


class RerankRequest(BaseModel):
    query: str
    chunks: list[dict]
    topK: int = 5


class AgentClassifyRequest(BaseModel):
    query: str


class AgentClassifyResponse(BaseModel):
    intent: str
    suggestedSources: list[str]


@app.get("/health")
def health():
    logger.debug("[health] ok")
    return {"status": "ok", "service": "starbot-ai-service"}


@app.get("/")
def root():
    return {
        "message": "Starbot AI Service is running",
        "health": "/health",
        "docs": "/docs",
    }


@app.post("/embed", response_model=EmbedResponse)
def embed(req: EmbedRequest):
    assert embedding_service is not None

    text_count = len(req.texts)
    total_chars = sum(len(t) for t in req.texts)
    preview = req.texts[0][:80].replace("\n", " ") if req.texts else ""

    logger.info(
        "[embed] start textCount=%s totalChars=%s preview=%r",
        text_count,
        total_chars,
        preview,
    )

    started = time.perf_counter()
    vectors = embedding_service.encode(req.texts)
    elapsed_ms = (time.perf_counter() - started) * 1000

    logger.info(
        "[embed] done textCount=%s vectors=%s dimensions=%s durationMs=%.1f",
        text_count,
        len(vectors),
        embedding_service.dimensions,
        elapsed_ms,
    )

    return EmbedResponse(
        embeddings=vectors,
        model=embedding_service.model_name,
        dimensions=embedding_service.dimensions,
    )


@app.post("/agent/classify", response_model=AgentClassifyResponse)
def agent_classify(req: AgentClassifyRequest):
    intent = classify_intent(req.query)
    return AgentClassifyResponse(
        intent=intent,
        suggestedSources=suggest_sources(intent),
    )


@app.post("/rerank")
def rerank(req: RerankRequest):
    query_preview = req.query[:120].replace("\n", " ")
    chunk_count = len(req.chunks)

    logger.info(
        "[rerank] start queryPreview=%r chunkCount=%s topK=%s",
        query_preview,
        chunk_count,
        req.topK,
    )

    started = time.perf_counter()
    results = rerank_chunks(req.query, req.chunks, req.topK)
    elapsed_ms = (time.perf_counter() - started) * 1000

    top_ids = [r.get("id", "")[:48] for r in results[:3]]
    logger.info(
        "[rerank] done returned=%s durationMs=%.1f topIds=%s",
        len(results),
        elapsed_ms,
        top_ids,
    )

    return {"results": results}
