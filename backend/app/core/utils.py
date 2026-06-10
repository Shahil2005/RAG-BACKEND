"""Pure helper functions ported from `@starbot/config` and `@starbot/utils`.

These are side-effect-free string/scoring helpers shared across the RAG,
ingestion and query modules. The `GRAPH_SCOPES` / `GRAPH_OAUTH_SCOPE` config
constants already live in :mod:`app.core.constants`.
"""

import re

from app.core.constants import CHUNK_OVERLAP, CHUNK_SIZE

__all__ = (
    "camel_to_snake",
    "chunk_text",
    "clean_email_body",
    "normalize_keys",
    "reciprocal_rank_fusion",
    "vector_id",
    "workspace_partition",
)

_CAMEL_RE = re.compile(r"(?<!^)(?=[A-Z])")


def camel_to_snake(name: str) -> str:
    """``workspaceId`` -> ``workspace_id``; already-snake names pass through unchanged."""
    return _CAMEL_RE.sub("_", name).lower()


def normalize_keys(data: dict) -> dict:
    """Normalize a dict's keys to snake_case.

    Cross-service callers ported from the NestJS codebase pass option dicts with
    inconsistent key casing (``workspaceId`` vs ``workspace_id``); this lets the
    receiving service coerce either form into its options model/dataclass.
    """
    return {camel_to_snake(k): v for k, v in data.items()}


def workspace_partition(slug: str) -> str:
    """Return the vector-namespace partition name for a workspace slug."""
    return f"{slug}-workspace"


def vector_id(source: str, content_id: str, chunk_id: str) -> str:
    """Build a deterministic Pinecone vector id from its parts."""
    return f"{source}:{content_id}:{chunk_id}"


_WHITESPACE_RE = re.compile(r"\s+")
_STYLE_RE = re.compile(r"<style[^>]*>.*?</style>", re.IGNORECASE | re.DOTALL)
_SCRIPT_RE = re.compile(r"<script[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


def chunk_text(
    text: str,
    size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
) -> list[str]:
    """Split text into overlapping fixed-size chunks (whitespace-normalized)."""
    normalized = _WHITESPACE_RE.sub(" ", text).strip()
    if not normalized:
        return []

    chunks: list[str] = []
    start = 0
    length = len(normalized)

    while start < length:
        end = min(start + size, length)
        chunks.append(normalized[start:end])
        if end >= length:
            break
        start = end - overlap

    return chunks


def clean_email_body(html_or_text: str) -> str:
    """Strip HTML markup/styles/scripts and collapse whitespace from an email body."""
    text = _STYLE_RE.sub("", html_or_text)
    text = _SCRIPT_RE.sub("", text)
    text = _TAG_RE.sub(" ", text)
    text = text.replace("&nbsp;", " ")
    text = _WHITESPACE_RE.sub(" ", text)
    return text.strip()


def reciprocal_rank_fusion(
    result_lists: list[list[dict[str, object]]],
    k: int = 60,
) -> list[dict[str, object]]:
    """Fuse ranked result lists via Reciprocal Rank Fusion.

    Each item is a mapping with ``"id"`` and ``"score"`` keys; the input scores
    are ignored and replaced by the fused RRF score. Returns items sorted by
    descending fused score.
    """
    scores: dict[str, float] = {}

    for result_list in result_lists:
        for rank, item in enumerate(result_list):
            item_id = str(item["id"])
            prev = scores.get(item_id, 0.0)
            scores[item_id] = prev + 1.0 / (k + rank + 1)

    fused = [{"id": item_id, "score": score} for item_id, score in scores.items()]
    fused.sort(key=lambda entry: entry["score"], reverse=True)
    return fused
