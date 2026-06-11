"""Pinecone vector store service (port of apps/api/src/pinecone/pinecone.service.ts).

Uses the `pinecone` Python SDK. The client is created lazily so the backend boots
without PINECONE_API_KEY; only the methods that touch the index raise if it is unset.
Stored vector metadata keys are camelCase (matching the original TS VectorMetadata
JSON), so build_upsert/upsert pass metadata dicts through verbatim and query() maps the
camelCase keys back onto the snake_case VectorMetadata Pydantic model.
"""

from dataclasses import dataclass
from typing import Any

from app import logger
from app.core.constants import EMBEDDING_DIMENSIONS
from app.core.settings import settings
from app.core.utils import vector_id
from app.schema.common import RetrievedChunk, VectorMetadata

_UPSERT_BATCH_SIZE = 100


@dataclass
class UpsertVector:
    id: str
    values: list[float]
    metadata: dict[str, Any]  # camelCase VectorMetadata keys + "text"


class PineconeService:
    def __init__(self) -> None:
        self._pinecone: Any | None = None
        self._index: Any | None = None
        self._index_name: str = settings.pinecone_index_name or "starbot-dev"

    # --- client / index (lazy) ---
    def _client(self) -> Any:
        if self._pinecone is None:
            if not settings.pinecone_api_key:
                msg = "PINECONE_API_KEY is not configured"
                raise RuntimeError(msg)
            from pinecone import Pinecone  # lazy import

            self._pinecone = Pinecone(api_key=settings.pinecone_api_key)
        return self._pinecone

    def _idx(self) -> Any:
        if self._index is None:
            self._index = self._client().Index(self._index_name)
        return self._index

    def get_index_name(self) -> str:
        return self._index_name

    @staticmethod
    def dimensions() -> int:
        return EMBEDDING_DIMENSIONS

    async def get_namespace_vector_count(self, organization_id: str) -> int | None:
        try:
            stats = self._idx().describe_index_stats()
            namespaces = getattr(stats, "namespaces", None) or {}
            summary = namespaces.get(organization_id)
            if summary is None:
                return 0
            # SDK returns an object or dict depending on version.
            return int(
                getattr(summary, "vector_count", None)
                or (summary.get("vector_count") if isinstance(summary, dict) else 0)
                or 0
            )
        except Exception as err:
            logger.warning(f"[pinecone] describe_index_stats failed: {err!r}")
            return None

    async def delete_by_email_id(self, organization_id: str, email_id: str) -> None:
        # No-op: serverless indexes reject metadata-filter deletes for emailId.
        logger.debug(
            f"[pinecone] delete_by_email_id skipped org={organization_id} emailId={email_id}"
        )

    async def delete_by_vector_ids(self, organization_id: str, ids: list[str]) -> None:
        if not ids:
            return
        try:
            self._idx().delete(ids=ids, namespace=organization_id)
        except Exception as err:
            if self._is_delete_skippable(err):
                logger.debug(
                    f"[pinecone] skip delete_by_vector_ids org={organization_id} count={len(ids)}"
                )
                return
            raise

    @staticmethod
    def _is_delete_skippable(err: Exception) -> bool:
        name = type(err).__name__
        if name in ("PineconeNotFoundError", "PineconeBadRequestError"):
            return True
        status = getattr(err, "status", None)
        if status in (404, 400, "404", "400"):
            return True
        err_str = str(err).lower()
        if "404" in err_str or "400" in err_str or "not found" in err_str or "bad request" in err_str:
            return True
        return False

    async def upsert(self, organization_id: str, vectors: list[UpsertVector]) -> int:
        if not vectors:
            return 0
        idx = self._idx()
        upserted = 0
        for i in range(0, len(vectors), _UPSERT_BATCH_SIZE):
            batch = vectors[i : i + _UPSERT_BATCH_SIZE]
            payload = [
                {
                    "id": v.id,
                    "values": v.values,
                    "metadata": {**v.metadata, "text": v.metadata.get("text", "")},
                }
                for v in batch
            ]
            idx.upsert(vectors=payload, namespace=organization_id)
            upserted += len(payload)
        logger.info(
            f"[pinecone] upserted {upserted} vectors namespace={organization_id} index={self._index_name}"
        )
        return upserted

    async def query(
        self,
        organization_id: str,
        embedding: list[float],
        top_k: int,
        filter: dict[str, Any] | None = None,
    ) -> list[RetrievedChunk]:
        result = self._idx().query(
            namespace=organization_id,
            vector=embedding,
            top_k=top_k,
            include_metadata=True,
            filter=filter,
        )
        matches = (
            result.get("matches", [])
            if isinstance(result, dict)
            else getattr(result, "matches", []) or []
        )
        chunks: list[RetrievedChunk] = []
        for m in matches:
            md = (m.get("metadata") if isinstance(m, dict) else getattr(m, "metadata", None)) or {}
            mid = m.get("id") if isinstance(m, dict) else getattr(m, "id", "")
            score = m.get("score") if isinstance(m, dict) else getattr(m, "score", 0.0)
            chunks.append(
                RetrievedChunk(
                    id=mid or "",
                    content=md.get("text") or "",
                    score=score or 0.0,
                    metadata=VectorMetadata(
                        source=md.get("source"),
                        organization_id=md.get("organizationId"),
                        workspace_id=md.get("workspaceId"),
                        project_id=md.get("projectId"),
                        sector_id=md.get("sectorId"),
                        email_id=md.get("emailId"),
                        file_id=md.get("fileId"),
                        file_name=md.get("fileName"),
                        sender=md.get("sender"),
                        timestamp=md.get("timestamp"),
                        chunk_id=md.get("chunkId") or "",
                        subject=md.get("subject"),
                        web_url=md.get("webUrl"),
                    ),
                )
            )
        return chunks

    def build_upsert(
        self,
        source: str,
        content_id: str,
        chunk_id: str,
        embedding: list[float],
        metadata: dict[str, Any],
    ) -> UpsertVector:
        """Metadata is a camelCase VectorMetadata dict (with a 'text' key), minus chunkId."""
        return UpsertVector(
            id=vector_id(source, content_id, chunk_id),
            values=embedding,
            metadata={**metadata, "chunkId": chunk_id},
        )
