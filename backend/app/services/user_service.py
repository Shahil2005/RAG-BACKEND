"""User persistence (ORM port of the NestJS UserService)."""

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.schema.auth import GraphProfile


class UserService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def find_by_microsoft_id(self, microsoft_id: str) -> User | None:
        result = await self.db.execute(
            select(User).where(User.microsoft_id == microsoft_id)
        )
        return result.scalar_one_or_none()

    async def find_by_id(self, user_id: str) -> User | None:
        result = await self.db.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def upsert_from_graph(self, profile: GraphProfile) -> User:
        email = (profile.mail or profile.userPrincipalName or "").lower()
        if not email:
            msg = "Microsoft profile missing email"
            raise ValueError(msg)

        stmt = pg_insert(User).values(
            microsoft_id=profile.id,
            email=email,
            name=profile.displayName,
            avatar=None,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[User.microsoft_id],
            set_={
                "email": email,
                # COALESCE(EXCLUDED.name, users.name)
                "name": func.coalesce(stmt.excluded.name, User.name),
                "updated_at": func.now(),
            },
        ).returning(User.id)

        result = await self.db.execute(stmt)
        await self.db.commit()
        user_id = result.scalar_one()

        user = await self.find_by_id(user_id)
        if user is None:
            msg = "Failed to upsert user"
            raise RuntimeError(msg)
        return user
