"""Teams module Pydantic schemas.

Preserves the exact JSON shape returned by the NestJS TeamsController. The original
TeamsSyncResult interface is `{ indexed, skipped, message }` — all single-word keys,
so no camelCase aliasing is required.
"""

from pydantic import BaseModel, ConfigDict

__all__ = ("TeamsSyncResult",)


class TeamsSyncResult(BaseModel):
    """Result of POST /teams/sync (was the NestJS TeamsSyncResult interface)."""

    model_config = ConfigDict(populate_by_name=True)

    indexed: int
    skipped: int
    message: str
