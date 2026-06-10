"""Compatibility shim.

DocumentTextService is implemented inside app.services.ingestion_service.
Consumers (projects) import it from this dedicated path, so re-export it here.
"""

from app.services.ingestion_service import DocumentTextService

__all__ = ["DocumentTextService"]
