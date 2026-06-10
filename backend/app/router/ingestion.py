"""Ingestion routes — port of the NestJS IngestionController + IngestionWorkerController.

Mounted under ``/api/v1`` by the parent router, so the sub-paths match the
NestJS ``@Controller('ingestion')`` exactly:

  GET  /ingestion/status           -> getStatus
  POST /ingestion/outlook/sync     -> syncOutlook
  POST /ingestion/documents/sync   -> syncAllDocuments
  GET  /ingestion/worker/targets   -> listWorkerSyncTargets (worker token only)

The controller used ``JwtOrWorkerAuthGuard`` — authenticate either with a normal
session JWT (cookie / bearer) OR, when the worker service token matches, trust
the ``x-user-id`` / ``x-organization-id`` / ``x-role`` headers. That dual path is
implemented by :func:`jwt_or_worker_user`.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.core.dependencies import DBSessionDep, get_current_user
from app.core.settings import settings
from app.schema.auth import AuthContext, MemberRole
from app.schema.common import DocumentsSyncResult, IngestionStatus, OutlookSyncResult
from app.schema.ingestion import WorkerSyncTarget
from app.services.ingestion_service import IngestionService

router = APIRouter(prefix="/ingestion", tags=["ingestion"])


async def jwt_or_worker_user(request: Request, db: DBSessionDep) -> AuthContext:
    """Port of JwtOrWorkerAuthGuard.

    Accept a worker-service-token request (with identity headers) or fall back to
    the standard session-JWT authentication used everywhere else.
    """
    worker_token = settings.worker_service_token and settings.worker_service_token.strip()
    auth_header = request.headers.get("authorization")
    user_id = request.headers.get("x-user-id")
    organization_id = request.headers.get("x-organization-id")

    if (
        worker_token
        and auth_header == f"Bearer {worker_token}"
        and user_id
        and organization_id
    ):
        role = request.headers.get("x-role") or MemberRole.owner.value
        return AuthContext(
            user_id=str(user_id),
            organization_id=str(organization_id),
            role=MemberRole(role),
        )

    return await get_current_user(request, db)


JwtOrWorkerUserDep = Annotated[AuthContext, Depends(jwt_or_worker_user)]


@router.get("/status")
async def status_endpoint(user: JwtOrWorkerUserDep, db: DBSessionDep, request: Request) -> IngestionStatus:
    cache = getattr(request.app.state, "cache", None)
    return await IngestionService(db, cache).get_status(user)


@router.post("/outlook/sync")
async def sync_outlook(
    user: JwtOrWorkerUserDep, db: DBSessionDep, request: Request
) -> OutlookSyncResult:
    cache = getattr(request.app.state, "cache", None)
    return await IngestionService(db, cache).sync_outlook(user)


@router.post("/documents/sync")
async def sync_documents(
    user: JwtOrWorkerUserDep, db: DBSessionDep, request: Request
) -> DocumentsSyncResult:
    cache = getattr(request.app.state, "cache", None)
    return await IngestionService(db, cache).sync_all_documents(user)


@router.get("/worker/targets")
async def worker_targets(db: DBSessionDep, request: Request) -> list[WorkerSyncTarget]:
    """Lists users with Microsoft tokens for scheduled sync (worker token only)."""
    expected = settings.worker_service_token and settings.worker_service_token.strip()
    authorization = request.headers.get("authorization")
    if not expected or authorization != f"Bearer {expected}":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Worker service token required"
        )
    return await IngestionService(db).list_worker_sync_targets()
