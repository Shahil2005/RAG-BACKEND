"""Lightweight re-ranking via token overlap scoring (production may use cross-encoder)."""

import logging

logger = logging.getLogger("starbot.ai-service.rerank")


def _tokenize(text: str) -> set[str]:
    return {t.lower() for t in text.split() if len(t) > 2}


def rerank_chunks(query: str, chunks: list[dict], top_k: int) -> list[dict]:
    if not chunks:
        logger.warning("[rerank] no chunks to rerank")
        return []

    q_tokens = _tokenize(query)
    scored = []

    for chunk in chunks:
        content = chunk.get("content", "")
        c_tokens = _tokenize(content)
        overlap = len(q_tokens & c_tokens) / max(len(q_tokens), 1)
        base = float(chunk.get("score", 0))
        scored.append({
            "id": chunk["id"],
            "score": base * 0.6 + overlap * 0.4,
        })

    scored.sort(key=lambda x: x["score"], reverse=True)
    results = scored[:top_k]

    if results:
        logger.debug(
            "[rerank] top score=%.4f id=%s",
            results[0]["score"],
            str(results[0]["id"])[:48],
        )

    return results
