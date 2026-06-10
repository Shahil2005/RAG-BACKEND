"""Compatibility shim.

OutlookMailService is implemented inside app.services.rag_service (the RAG module
ported all five of its NestJS providers into one file). Consumers (chat, ingestion)
import it from this dedicated path, so re-export it here.
"""

from app.services.rag_service import OutlookMailService

__all__ = ["OutlookMailService"]
