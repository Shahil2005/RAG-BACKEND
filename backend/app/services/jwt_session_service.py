"""JWT session management (ORM port of the NestJS JwtSessionService)."""

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_token, sign_session_jwt, verify_session_jwt
from app.models.user import Session


@dataclass
class SessionPayload:
    sub: str
    sid: str


@dataclass
class CreatedSession:
    token: str
    session_id: str
    expires_at: datetime


class JwtSessionService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create_session(self, user_id: str) -> CreatedSession:
        session_id = str(uuid.uuid4())
        token, expires_at = sign_session_jwt(sub=str(user_id), sid=session_id)

        self.db.add(
            Session(
                id=uuid.UUID(session_id),
                user_id=user_id,
                jwt_token=hash_token(token),
                expires_at=expires_at,
            )
        )
        await self.db.commit()
        return CreatedSession(token=token, session_id=session_id, expires_at=expires_at)

    async def verify_token(self, token: str) -> SessionPayload:
        decoded = verify_session_jwt(token)
        payload = SessionPayload(sub=decoded["sub"], sid=decoded["sid"])

        result = await self.db.execute(
            select(Session.expires_at).where(
                Session.id == payload.sid,
                Session.jwt_token == hash_token(token),
            )
        )
        expires_at = result.scalar_one_or_none()
        if expires_at is None or _as_aware(expires_at) <= datetime.now(timezone.utc):
            msg = "Session expired or revoked"
            raise ValueError(msg)
        return payload

    async def revoke_session(self, session_id: str, token: str | None = None) -> None:
        stmt = delete(Session).where(Session.id == session_id)
        if token:
            stmt = stmt.where(Session.jwt_token == hash_token(token))
        await self.db.execute(stmt)
        await self.db.commit()

    async def revoke_all_user_sessions(self, user_id: str) -> None:
        await self.db.execute(delete(Session).where(Session.user_id == user_id))
        await self.db.commit()


def _as_aware(value: datetime) -> datetime:
    """Treat naive timestamps (rare) as timezone.utc for safe comparison."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value
