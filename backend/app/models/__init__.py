from datetime import datetime

from sqlalchemy import DateTime
from sqlalchemy.orm import DeclarativeBase


# Modern approach (SQLAlchemy 2.0+).
class Base(DeclarativeBase):
    # The existing Postgres schema uses TIMESTAMPTZ everywhere, and the app inserts
    # timezone-aware datetimes (datetime.now(timezone.utc)). Map every Mapped[datetime]
    # column to TIMESTAMP WITH TIME ZONE so asyncpg uses the tz-aware codec — otherwise
    # inserting an aware datetime raises asyncpg DataError
    # "can't subtract offset-naive and offset-aware datetimes".
    type_annotation_map = {
        datetime: DateTime(timezone=True),
    }


# Register the models for Migration / metadata discovery.
# Import every OWNER model module here so Base.metadata is fully populated for Alembic.
# Order: base tenancy/users first, then table-owning domain modules. Modules that own
# no new tables (graph, rag, search, teams, query) are intentionally NOT imported here —
# they re-export the owner classes and registering them would double-define tables.
from . import (
    audit,  # noqa: F401
    chat,  # noqa: F401
    documents,  # noqa: F401
    ingestion,  # noqa: F401  (owns email_metadata, file_metadata, ...)
    mail,  # noqa: F401  (owns email_classifications; re-exports EmailMetadata)
    organization,  # noqa: F401
    projects,  # noqa: F401
    user,  # noqa: F401
    workspaces,  # noqa: F401
)
