"""Mail agent orchestration (ORM port of the NestJS MailAgentService).

Faithful translation of apps/api/src/mail/mail-agent.service.ts:
  - classify_inbox       -> LLM-classifies inbox mail, groups sent mail as 'sent'
  - get_classifications  -> stored classifications for the user/org
  - bulk_dismiss_drafts  -> count of spam classifications (audited)
  - delete_classifications -> soft-delete in Outlook + drop the organiser rows
  - generate_replies     -> per-email AI reply drafts (not written to mailbox)
  - save_reply_drafts    -> persist reviewed replies as Outlook draft replies

Raw ``pg`` queries are translated to SQLAlchemy ORM. OpenAI chat completions go
through app.services.ai_client.AiClientService (AsyncOpenAI under the hood).

CROSS-SERVICE: GraphService and AuditService are imported LAZILY inside methods
to avoid circular imports at boot (the graph module is not yet ported; see the
TODO(migration) guard in ``_graph``).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app import logger
from app.core.constants import LLM_MODEL
from app.models.mail import EmailCategory, EmailClassification, EmailMetadata
from app.prompts import MAIL_CLASSIFIER_PROMPT
from app.schema.auth import AuthContext
from app.schema.common import MailClassificationResult
from app.schema.mail import GeneratedReply

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    from app.services.ai_client import AiClientService


# Local prompt (was a module constant inside mail-agent.service.ts).
MAIL_REPLY_PROMPT = (
    "You draft professional, concise email replies on behalf of the user.\n"
    'Write ONLY the reply body — no subject line, no "Subject:" prefix, no quoted '
    "original.\n"
    "Use a polite, clear, business-appropriate tone and keep it brief and actionable.\n"
    'End with a generic sign-off such as "Best regards". Do not invent facts, prices, '
    "dates,\n"
    "or commitments that aren't supported by the original email; if key information is "
    "missing,\n"
    "keep the reply general or ask a short clarifying question."
)


class MailAgentService:
    """Inbox classification + reply drafting for a single authenticated user."""

    def __init__(self, db: AsyncSession, ai: AiClientService | None = None) -> None:
        self.db = db
        if ai is None:
            # Lazy import keeps the OpenAI wrapper optional at construction time.
            from app.services.ai_client import AiClientService

            ai = AiClientService()
        self.ai = ai

    # --- lazy sibling-service accessors ------------------------------------

    def _graph(self) -> Any:
        """Construct GraphService lazily (avoids circular import at boot).

        TODO(migration): the graph module is ported under
        app.services.graph_service.GraphService. If it is not yet available this
        raises a clear error rather than failing import — every mail route that
        touches Outlook depends on it.
        """
        try:
            from app.services.graph_service import GraphService
        except ImportError as err:  # pragma: no cover - until graph is ported
            msg = (
                "GraphService is not available yet (graph module not ported). "
                "Mail Outlook operations require app.services.graph_service."
            )
            raise RuntimeError(msg) from err
        return GraphService(self.db)

    def _audit(self) -> Any:
        from app.services.audit_service import AuditService

        return AuditService(self.db)

    # --- persistence -------------------------------------------------------

    async def _store_classification(
        self,
        ctx: AuthContext,
        email: Any,
        category: EmailCategory | str,
        confidence: float,
        reasoning: str | None,
    ) -> str | None:
        """Upsert an email's metadata + classification, returning the metadata id."""
        meta_stmt = (
            pg_insert(EmailMetadata)
            .values(
                organization_id=ctx.organization_id,
                user_id=ctx.user_id,
                graph_message_id=email.id,
                subject=email.subject,
                sender=email.sender,
                received_at=email.received_at,
            )
            .on_conflict_do_update(
                index_elements=[
                    EmailMetadata.organization_id,
                    EmailMetadata.user_id,
                    EmailMetadata.graph_message_id,
                ],
                set_={
                    "subject": email.subject,
                    "sender": email.sender,
                    "received_at": email.received_at,
                },
            )
            .returning(EmailMetadata.id)
        )
        meta_id = (await self.db.execute(meta_stmt)).scalar_one_or_none()
        if meta_id is None:
            await self.db.commit()
            return None

        cls_stmt = pg_insert(EmailClassification).values(
            organization_id=ctx.organization_id,
            user_id=ctx.user_id,
            email_metadata_id=meta_id,
            category=EmailCategory(category),
            confidence=confidence,
            reasoning=reasoning,
        )
        cls_stmt = cls_stmt.on_conflict_do_update(
            index_elements=[EmailClassification.email_metadata_id],
            set_={
                "category": cls_stmt.excluded.category,
                "confidence": cls_stmt.excluded.confidence,
                "reasoning": cls_stmt.excluded.reasoning,
            },
        )
        await self.db.execute(cls_stmt)
        await self.db.commit()

        return str(meta_id)

    # --- public API --------------------------------------------------------

    async def classify_inbox(self, ctx: AuthContext) -> list[MailClassificationResult]:
        """Classify recent inbox mail via the LLM; group sent mail as 'sent'."""
        graph = self._graph()
        results: list[MailClassificationResult] = []

        # Received mail (Inbox only) is classified by priority via the LLM.
        inbox = await graph.fetch_inbox_emails(ctx, 30)
        for email in inbox:
            raw = await self.ai.chat(
                messages=[
                    {"role": "system", "content": MAIL_CLASSIFIER_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"Subject: {email.subject}\n"
                            f"From: {email.sender}\n"
                            f"Read: {email.is_read}\n"
                            f"Body preview: {email.body[:500]}"
                        ),
                    },
                ],
                model=LLM_MODEL,
                response_format={"type": "json_object"},
            )
            parsed = json.loads(raw or "{}")
            category = parsed.get("category")
            confidence = parsed.get("confidence")
            reasoning = parsed.get("reasoning")

            meta_id = await self._store_classification(
                ctx, email, category, confidence, reasoning
            )

            results.append(
                MailClassificationResult(
                    email_id=meta_id or email.id,
                    graph_message_id=email.id,
                    subject=email.subject,
                    category=EmailCategory(category),
                    confidence=confidence,
                    reasoning=reasoning,
                )
            )

        # Sent mail is grouped under its own 'sent' category — no LLM needed.
        sent = await graph.fetch_sent_emails(ctx, 30)
        for email in sent:
            meta_id = await self._store_classification(
                ctx, email, EmailCategory.sent, 1, None
            )
            results.append(
                MailClassificationResult(
                    email_id=meta_id or email.id,
                    graph_message_id=email.id,
                    subject=email.subject,
                    category=EmailCategory.sent,
                    confidence=1,
                    reasoning=None,
                )
            )

        return results

    async def bulk_dismiss_drafts(self, ctx: AuthContext) -> dict[str, int]:
        """Return how many spam classifications exist (audited)."""
        result = await self.db.execute(
            select(EmailClassification.id).where(
                EmailClassification.user_id == ctx.user_id,
                EmailClassification.category == EmailCategory.spam,
            )
        )
        count = len(result.all())
        await self._audit().log_audit(
            ctx, "mail.bulk_dismiss", "mail", None, {"count": count}
        )
        return {"dismissed": count}

    async def delete_classifications(
        self, ctx: AuthContext, classification_ids: list[str]
    ) -> dict[str, Any]:
        """Soft-delete the selected emails in Outlook and drop the organiser rows.

        The Outlook delete moves messages to Deleted Items (recoverable). Returns
        how many were deleted and the ids that failed (e.g. already gone).
        """
        ids = [i for i in dict.fromkeys(classification_ids) if i]
        if not ids:
            return {"requested": 0, "deleted": 0, "failed": []}

        graph = self._graph()

        # Only operate on classifications that belong to this user/org.
        rows = (
            await self.db.execute(
                select(
                    EmailClassification.id, EmailMetadata.graph_message_id
                )
                .join(
                    EmailMetadata,
                    EmailMetadata.id == EmailClassification.email_metadata_id,
                )
                .where(
                    EmailClassification.user_id == ctx.user_id,
                    EmailClassification.organization_id == ctx.organization_id,
                    EmailClassification.id.in_(ids),
                )
            )
        ).all()

        failed: list[str] = []
        deleted = 0

        for row in rows:
            try:
                await graph.delete_message(ctx, row.graph_message_id)
                # Remove from the organiser only after the Outlook delete succeeds.
                await self.db.execute(
                    EmailClassification.__table__.delete().where(
                        EmailClassification.id == row.id
                    )
                )
                await self.db.commit()
                deleted += 1
            except Exception as err:
                logger.warning(f"[mail] delete failed classification={row.id}: {err}")
                failed.append(str(row.id))

        # Ids the caller asked for that didn't match a row this user owns.
        found_ids = {str(r.id) for r in rows}
        for id_ in ids:
            if id_ not in found_ids:
                failed.append(id_)

        await self._audit().log_audit(
            ctx,
            "mail.delete",
            "mail",
            None,
            {"requested": len(ids), "deleted": deleted, "failed": len(failed)},
        )

        return {"requested": len(ids), "deleted": deleted, "failed": failed}

    async def generate_replies(
        self, ctx: AuthContext, classification_ids: list[str]
    ) -> dict[str, Any]:
        """Generate an editable AI reply for each selected email (no mailbox write)."""
        ids = [i for i in dict.fromkeys(classification_ids) if i]
        if not ids:
            return {"replies": [], "failed": []}

        graph = self._graph()

        rows = (
            await self.db.execute(
                select(
                    EmailClassification.id,
                    EmailMetadata.graph_message_id,
                    EmailMetadata.subject,
                    EmailMetadata.sender,
                )
                .join(
                    EmailMetadata,
                    EmailMetadata.id == EmailClassification.email_metadata_id,
                )
                .where(
                    EmailClassification.user_id == ctx.user_id,
                    EmailClassification.organization_id == ctx.organization_id,
                    EmailClassification.id.in_(ids),
                )
            )
        ).all()

        replies: list[GeneratedReply] = []
        failed: list[str] = []

        for row in rows:
            try:
                email = await graph.fetch_message_by_id(ctx, row.graph_message_id)
                body = ((email.body if email else "") or "")[:2000]
                reply = (
                    await self.ai.chat(
                        messages=[
                            {"role": "system", "content": MAIL_REPLY_PROMPT},
                            {
                                "role": "user",
                                "content": (
                                    f"Draft a reply to this email.\n\n"
                                    f"Subject: {row.subject}\n"
                                    f"From: {row.sender}\n\n"
                                    f"{body or '(no body available)'}"
                                ),
                            },
                        ],
                        model=LLM_MODEL,
                    )
                ).strip()
                if not reply:
                    failed.append(str(row.id))
                    continue
                replies.append(
                    GeneratedReply(
                        id=str(row.id),
                        subject=row.subject,
                        sender=row.sender,
                        reply=reply,
                    )
                )
            except Exception as err:
                logger.warning(
                    f"[mail] reply generation failed classification={row.id}: {err}"
                )
                failed.append(str(row.id))

        found_ids = {str(r.id) for r in rows}
        for id_ in ids:
            if id_ not in found_ids:
                failed.append(id_)

        await self._audit().log_audit(
            ctx,
            "mail.reply_generated",
            "mail",
            None,
            {"requested": len(ids), "generated": len(replies), "failed": len(failed)},
        )

        return {"replies": replies, "failed": failed}

    async def save_reply_drafts(
        self, ctx: AuthContext, items: list[dict[str, str]]
    ) -> dict[str, Any]:
        """Persist the reviewed replies as Outlook draft replies in the Drafts folder."""
        valid = [
            i
            for i in items
            if i and i.get("id") and (i.get("reply") or "").strip()
        ]
        if not valid:
            return {
                "created": 0,
                "failed": [i.get("id") for i in items if i and i.get("id")],
            }

        graph = self._graph()

        ids = list(dict.fromkeys(i["id"] for i in valid))
        rows = (
            await self.db.execute(
                select(
                    EmailClassification.id, EmailMetadata.graph_message_id
                )
                .join(
                    EmailMetadata,
                    EmailMetadata.id == EmailClassification.email_metadata_id,
                )
                .where(
                    EmailClassification.user_id == ctx.user_id,
                    EmailClassification.organization_id == ctx.organization_id,
                    EmailClassification.id.in_(ids),
                )
            )
        ).all()
        graph_id_by_id = {str(r.id): r.graph_message_id for r in rows}

        failed: list[str] = []
        created = 0

        for item in valid:
            graph_id = graph_id_by_id.get(item["id"])
            if not graph_id:
                failed.append(item["id"])
                continue
            try:
                await graph.create_reply_draft(ctx, graph_id, item["reply"].strip())
                created += 1
            except Exception as err:
                logger.warning(
                    f"[mail] save draft failed classification={item['id']}: {err}"
                )
                failed.append(item["id"])

        await self._audit().log_audit(
            ctx,
            "mail.reply_drafted",
            "mail",
            None,
            {"requested": len(valid), "created": created, "failed": len(failed)},
        )

        return {"created": created, "failed": failed}

    async def get_classifications(self, ctx: AuthContext) -> list[dict[str, Any]]:
        """Stored classifications for the user/org (port of getClassifications).

        Preserves the EXACT JSON shape the NestJS query returned: every
        ``email_classifications`` column (snake_case) plus a nested
        ``email_metadata`` object ``{subject, sender, received_at}``, ordered by
        ``created_at`` descending.
        """
        rows = (
            await self.db.execute(
                select(EmailClassification, EmailMetadata)
                .join(
                    EmailMetadata,
                    EmailMetadata.id == EmailClassification.email_metadata_id,
                )
                .where(
                    EmailClassification.user_id == ctx.user_id,
                    EmailClassification.organization_id == ctx.organization_id,
                )
                .order_by(EmailClassification.created_at.desc())
            )
        ).all()

        out: list[dict[str, Any]] = []
        for ec, em in rows:
            out.append(
                {
                    "id": str(ec.id),
                    "organization_id": str(ec.organization_id),
                    "user_id": str(ec.user_id),
                    "email_metadata_id": str(ec.email_metadata_id),
                    "category": ec.category.value,
                    "confidence": ec.confidence,
                    "reasoning": ec.reasoning,
                    "created_at": (
                        ec.created_at.isoformat() if ec.created_at else None
                    ),
                    "email_metadata": {
                        "subject": em.subject,
                        "sender": em.sender,
                        "received_at": (
                            em.received_at.isoformat() if em.received_at else None
                        ),
                    },
                }
            )
        return out
