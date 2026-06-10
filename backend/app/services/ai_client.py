"""AI client service (port of the NestJS ai/ai-client.service.ts).

The original service was a thin HTTP client to the internal ``ai-service``
microservice (``AI_SERVICE_URL``) for embeddings / rerank / intent
classification. Those methods are preserved verbatim (httpx instead of
``fetch``) so the RAG / ingestion / projects modules keep the same contract:

    embed(texts)            -> {"embeddings", "model", "dimensions"}
    rerank(query, chunks, k)-> [{"id", "score"}, ...]
    classify_intent(query)  -> {"intent", "suggestedSources"} | None
    is_reachable()          -> bool

In addition, per the migration plan this module now wraps the OpenAI python
SDK (``AsyncOpenAI``) directly, exposing ``embeddings`` and ``chat`` helpers so
downstream modules can call OpenAI without a second client. ``OPENAI_API_KEY``
is read from :mod:`app.core.settings`.
"""

from typing import Any

import httpx

from app import logger
from app.core.settings import settings

_DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
_DEFAULT_CHAT_MODEL = "gpt-4o-mini"


class AiClientService:
    """HTTP client for the internal AI service + an OpenAI SDK wrapper."""

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or settings.ai_service_url).rstrip("/")
        self._openai: Any | None = None

    # --- internal ai-service (embed / rerank / classify) -----------------

    async def is_reachable(self) -> bool:
        """Best-effort health probe of the internal AI service (never throws)."""
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                res = await client.get(f"{self.base_url}/health")
                return res.is_success
        except Exception:
            return False

    async def embed(self, texts: list[str]) -> dict[str, Any]:
        """POST /embed -> ``{"embeddings", "model", "dimensions"}``.

        Raises on non-2xx, mirroring the NestJS ``embed`` contract.
        """
        async with httpx.AsyncClient(timeout=60.0) as client:
            res = await client.post(f"{self.base_url}/embed", json={"texts": texts})
            if not res.is_success:
                msg = f"Embed failed: {res.reason_phrase}"
                raise RuntimeError(msg)
            return res.json()

    async def rerank(
        self,
        query: str,
        chunks: list[dict[str, Any]],
        top_k: int,
    ) -> list[dict[str, Any]]:
        """POST /rerank -> the ``results`` list of ``{"id", "score"}``."""
        async with httpx.AsyncClient(timeout=60.0) as client:
            res = await client.post(
                f"{self.base_url}/rerank",
                json={"query": query, "chunks": chunks, "topK": top_k},
            )
            if not res.is_success:
                msg = f"Rerank failed: {res.reason_phrase}"
                raise RuntimeError(msg)
            data = res.json()
            return data.get("results", [])

    async def classify_intent(self, query: str) -> dict[str, Any] | None:
        """POST /agent/classify -> ``{"intent", "suggestedSources"}`` or ``None``.

        Swallows all errors (returns ``None``), matching the NestJS behaviour
        where intent classification is optional/best-effort.
        """
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                res = await client.post(
                    f"{self.base_url}/agent/classify", json={"query": query}
                )
                if not res.is_success:
                    return None
                return res.json()
        except Exception:
            return None

    # --- OpenAI SDK wrapper (embeddings / chat) --------------------------

    def _client(self) -> Any:
        """Lazily construct an ``AsyncOpenAI`` client.

        Imported lazily so the backend still boots when the ``openai`` package
        or ``OPENAI_API_KEY`` is absent (only the OpenAI-backed helpers fail).
        """
        if self._openai is None:
            if not settings.openai_api_key:
                msg = "OPENAI_API_KEY is not configured"
                raise RuntimeError(msg)
            try:
                from openai import AsyncOpenAI
            except ImportError as err:  # pragma: no cover - dependency guard
                msg = "openai package is not installed"
                raise RuntimeError(msg) from err
            self._openai = AsyncOpenAI(api_key=settings.openai_api_key)
        return self._openai

    async def embeddings(
        self,
        texts: list[str],
        model: str = _DEFAULT_EMBEDDING_MODEL,
    ) -> list[list[float]]:
        """Create embeddings via the OpenAI SDK -> list of vectors."""
        client = self._client()
        res = await client.embeddings.create(model=model, input=texts)
        return [item.embedding for item in res.data]

    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str = _DEFAULT_CHAT_MODEL,
        temperature: float = 0.0,
        **kwargs: Any,
    ) -> str:
        """Chat completion via the OpenAI SDK -> assistant message content."""
        client = self._client()
        res = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            **kwargs,
        )
        content = res.choices[0].message.content
        if content is None:
            logger.warning("[ai] chat completion returned empty content")
            return ""
        return content
