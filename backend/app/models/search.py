"""Search module models.

The search module owns no tables of its own. It orchestrates the query pipeline
and writes to ``audit_logs`` (owned by the common/audit module). This module is
intentionally empty so there is nothing to register with ``Base.metadata`` here.
"""
