"""Teams module ORM models.

The teams module (Microsoft Teams channel ingestion) is a v1 placeholder and owns
no tables of its own — it writes only to the shared `audit_logs` table, which is
owned by the (not-yet-ported) audit/common module. This module is intentionally
empty so the model registry has nothing extra to map for teams.

TODO(migration): when Teams channel ingestion is implemented (after
ChannelMessage.Read.All admin consent), any teams-owned tables go here.
"""
