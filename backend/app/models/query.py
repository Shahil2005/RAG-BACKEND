"""SQLAlchemy models owned by the query module.

The NestJS ``query`` module owns no database tables of its own. It orchestrates
the ``rag``, ``documents`` and ``search`` modules and writes audit rows through
the shared ``common`` AuditService (the ``audit_logs`` table is owned by the
common/enterprise schema, not by this module).

This module is intentionally empty of table definitions; it exists only so the
package layout mirrors the other ported modules. Do not add tables here unless
the query module starts owning one.
"""
