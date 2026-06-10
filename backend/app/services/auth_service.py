"""Auth orchestration (ORM port of the NestJS AuthService).

NOTE: completeMicrosoftLogin originally kicked off background Outlook + document
ingestion syncs. Those modules are not migrated yet, so the hooks are stubbed
(_schedule_post_login_sync) and clearly marked. They are fire-and-forget and never
block login, so behavior is preserved for the authenticated user.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app import logger
from app.core.redis import RedisHelper
from app.core.settings import settings
from app.models.organization import MemberRole, Organization, OrganizationMember
from app.models.user import OAuthToken
from app.schema.auth import AuthContext
from app.services.encryption import EncryptionService
from app.services.jwt_session_service import JwtSessionService
from app.services.oauth_service import OAuthService
from app.services.user_service import UserService


@dataclass
class LoginResult:
    token: str
    expires_at: datetime
    popup: bool
    user: dict


class AuthService:
    def __init__(self, db: AsyncSession, cache: RedisHelper | None = None) -> None:
        self.db = db
        self.users = UserService(db)
        self.sessions = JwtSessionService(db)
        self.encryption = EncryptionService()
        self.oauth = OAuthService(cache)

    def session_cookie_name(self) -> str:
        return settings.session_cookie_name

    async def complete_microsoft_login(self, code: str, state: str) -> LoginResult:
        popup = await self.oauth.validate_state(state)
        tokens = await self.oauth.exchange_code(code)
        profile = await self.oauth.fetch_graph_profile(tokens.access_token)
        user = await self.users.upsert_from_graph(profile)

        expires_at = tokens.expires_at or self.oauth.decode_access_token_expiry(
            tokens.access_token
        )

        await self._store_oauth_tokens(
            user_id=user.id,
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
            expires_at=expires_at,
        )

        await self.sessions.revoke_all_user_sessions(user.id)
        session = await self.sessions.create_session(user.id)
        auth_ctx = await self.ensure_organization(str(user.id), user.email)
        self._schedule_post_login_sync(auth_ctx)

        logger.info(f"[auth] login.complete userId={user.id} email={user.email}")
        return LoginResult(
            token=session.token,
            expires_at=session.expires_at,
            popup=popup,
            user={
                "id": str(user.id),
                "email": user.email,
                "name": user.name,
                "avatar": user.avatar,
            },
        )

    async def _store_oauth_tokens(
        self,
        user_id: uuid.UUID,
        access_token: str,
        refresh_token: str | None,
        expires_at: datetime | None,
    ) -> None:
        encrypted_refresh = (
            self.encryption.encrypt(refresh_token) if refresh_token else None
        )
        stmt = pg_insert(OAuthToken).values(
            user_id=user_id,
            access_token=self.encryption.encrypt(access_token),
            refresh_token=encrypted_refresh,
            expires_at=expires_at,
        )
        from sqlalchemy import func

        stmt = stmt.on_conflict_do_update(
            index_elements=[OAuthToken.user_id],
            set_={
                "access_token": stmt.excluded.access_token,
                # COALESCE(EXCLUDED.refresh_token, oauth_tokens.refresh_token)
                "refresh_token": func.coalesce(
                    stmt.excluded.refresh_token, OAuthToken.refresh_token
                ),
                "expires_at": stmt.excluded.expires_at,
                "updated_at": func.now(),
            },
        )
        await self.db.execute(stmt)
        await self.db.commit()

    async def get_session_from_token(
        self, access_token: str, org_id: str | None = None
    ) -> AuthContext:
        payload = await self.sessions.verify_token(access_token)
        user = await self.users.find_by_id(payload.sub)
        if user is None:
            msg = "User not found"
            raise ValueError(msg)
        ctx = await self.ensure_organization(str(user.id), user.email, org_id)
        ctx.email = user.email
        return ctx

    async def get_me(self, access_token: str) -> dict:
        ctx = await self.get_session_from_token(access_token)
        user = await self.users.find_by_id(ctx.user_id)
        if user is None:
            msg = "User not found"
            raise ValueError(msg)
        return {
            "id": str(user.id),
            "email": user.email,
            "name": user.name,
            "avatar": user.avatar,
            "organizationId": ctx.organization_id,
            "role": ctx.role,
        }

    async def logout(self, access_token: str) -> None:
        try:
            payload = await self.sessions.verify_token(access_token)
            await self.sessions.revoke_session(payload.sid, access_token)
        except Exception:
            return

    async def resolve_auth_context(
        self, user_id: str, organization_id: str | None = None
    ) -> AuthContext:
        query = select(
            OrganizationMember.organization_id, OrganizationMember.role
        ).where(OrganizationMember.user_id == user_id)
        if organization_id:
            query = query.where(OrganizationMember.organization_id == organization_id)
        query = query.limit(1)

        row = (await self.db.execute(query)).first()
        if row is None:
            msg = "User is not a member of any organization"
            raise ValueError(msg)
        return AuthContext(
            user_id=user_id,
            organization_id=str(row.organization_id),
            role=row.role,
        )

    async def ensure_organization(
        self,
        user_id: str,
        email: str | None = None,
        organization_id: str | None = None,
    ) -> AuthContext:
        try:
            return await self.resolve_auth_context(user_id, organization_id)
        except ValueError:
            if organization_id:
                msg = "User is not a member of the requested organization"
                raise ValueError(msg) from None

        slug = f"personal-{user_id.replace('-', '')[:12]}"
        name = (
            f"{email.split('@')[0]}'s Organization" if email else "My Organization"
        )

        existing = (
            await self.db.execute(
                select(Organization.id).where(Organization.slug == slug)
            )
        ).scalar_one_or_none()

        if existing is not None:
            org_id = existing
        else:
            inserted = (
                await self.db.execute(
                    pg_insert(Organization)
                    .values(name=name, slug=slug)
                    .returning(Organization.id)
                )
            ).scalar_one()
            org_id = inserted
            logger.info(f"[auth] provisioned organization {slug} for user {user_id}")

        await self.db.execute(
            pg_insert(OrganizationMember)
            .values(organization_id=org_id, user_id=user_id, role=MemberRole.owner)
            .on_conflict_do_nothing(index_elements=["organization_id", "user_id"])
        )
        await self.db.commit()

        return AuthContext(
            user_id=user_id, organization_id=str(org_id), role=MemberRole.owner
        )

    def _schedule_post_login_sync(self, ctx: AuthContext) -> None:
        # TODO(migration): wire to the ingestion module once it is ported (Outlook +
        # documents background sync). Fire-and-forget; must never block login.
        logger.info(
            f"[auth] post-login sync pending ingestion migration userId={ctx.user_id}"
        )
