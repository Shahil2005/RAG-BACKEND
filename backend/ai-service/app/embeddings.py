import logging

from sentence_transformers import SentenceTransformer

logger = logging.getLogger("starbot.ai-service.embeddings")

MODEL_NAME = "all-MiniLM-L6-v2"
DIMENSIONS = 384


class EmbeddingService:
    def __init__(self):
        logger.info("[embeddings] loading SentenceTransformer model=%s", MODEL_NAME)
        self._model = SentenceTransformer(MODEL_NAME)
        logger.info("[embeddings] model loaded model=%s dimensions=%s", MODEL_NAME, DIMENSIONS)

    @property
    def model_name(self) -> str:
        return MODEL_NAME

    @property
    def dimensions(self) -> int:
        return DIMENSIONS

    def encode(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            logger.warning("[embeddings] encode called with empty texts list")
            return []
        logger.debug("[embeddings] encoding batch size=%s", len(texts))
        embeddings = self._model.encode(texts, normalize_embeddings=True)
        return embeddings.tolist()
