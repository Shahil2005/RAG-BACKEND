"""Compatibility shim.

SharePointDocumentsService is implemented inside app.services.rag_service.
Consumers (chat, ingestion) import it from this dedicated path, so re-export it here.
"""

from app.services.rag_service import SharePointDocumentsService

__all__ = ["SharePointDocumentsService"]
