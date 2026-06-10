"""Top-level API router.

Everything is mounted under /api/v1 to preserve the global prefix the NestJS API
used (app.setGlobalPrefix('api/v1')), so existing frontend calls keep working.
As modules are migrated, include their routers here.
"""

from fastapi import APIRouter

from app.router.audit import router as audit_router
from app.router.auth import router as auth_router
from app.router.base import router as base_router
from app.router.chat import router as chat_router
from app.router.documents import router as documents_router
from app.router.graph import router as graph_router
from app.router.ingestion import router as ingestion_router
from app.router.mail import router as mail_router
from app.router.projects import router as projects_router
from app.router.search import router as search_router
from app.router.teams import router as teams_router
from app.router.workspaces import router as workspaces_router

api_router = APIRouter(prefix="/api/v1")

# Foundation
api_router.include_router(base_router)
api_router.include_router(auth_router)

# Migrated domain modules (query and rag expose no controller — service-only).
api_router.include_router(audit_router)
api_router.include_router(chat_router)
api_router.include_router(documents_router)
api_router.include_router(graph_router)
api_router.include_router(ingestion_router)
api_router.include_router(mail_router)
api_router.include_router(projects_router)
api_router.include_router(search_router)
api_router.include_router(teams_router)
api_router.include_router(workspaces_router)
