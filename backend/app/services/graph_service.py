"""Microsoft Graph service (ORM + httpx port of the NestJS GraphService).

The original used the fluent `@microsoft/microsoft-graph-client`; every Graph call
here is translated to a direct `httpx.AsyncClient` request against
`https://graph.microsoft.com/v1.0`, preserving the same paths, query options
(`$top`, `$select`, `$orderby`, `$search`) and response shaping. OAuth token rows
are read/written via SQLAlchemy ORM against `oauth_tokens` (the `OAuthToken` model),
and tokens are AES-256-GCM encrypted at rest by `EncryptionService`.

This service is consumed by the mail, ingestion and documents modules; those
sibling services are imported lazily inside methods to avoid circular imports.
"""

import base64
import json
import os
from datetime import datetime, timezone

import httpx
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app import logger
from app.core.constants import GRAPH_OAUTH_SCOPE
from app.core.settings import settings
from app.core.utils import clean_email_body
from app.models.user import OAuthToken
from app.schema.auth import AuthContext
from app.schema.graph import (
    CrawlLimits,
    DeltaCursorStore,
    DocumentSource,
    DownloadedFile,
    GraphDrive,
    GraphDriveItem,
    GraphEmail,
    GraphSite,
    WalkDriveOptions,
)
from app.services.encryption import EncryptionService

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
TOKEN_EXPIRY_SKEW_MS = 5 * 60 * 1000
DEFAULT_ACCESS_TOKEN_TTL_MS = 3600 * 1000


class GraphConfigError(Exception):
    """Raised when Microsoft Graph is not configured (was ServiceUnavailableException)."""


class GraphRequestError(Exception):
    """Raised on a bad Graph request / token failure (was BadRequestException)."""


class GraphService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.encryption = EncryptionService()

    # -- config / limits ----------------------------------------------------

    def get_crawl_limits(self) -> CrawlLimits:
        """SharePoint/OneDrive crawl limits, read from env like the NestJS config."""
        return CrawlLimits(
            max_sites=int(os.environ.get("SHAREPOINT_MAX_SITES") or "25"),
            max_files=int(os.environ.get("SHAREPOINT_MAX_FILES_PER_SYNC") or "200"),
            max_depth=int(os.environ.get("DOCUMENT_SYNC_RECURSION_DEPTH") or "5"),
            max_file_bytes=int(os.environ.get("DOCUMENT_MAX_BYTES") or "5242880"),
        )

    def _get_azure_credentials(self) -> tuple[str, str, str]:
        client_id = (settings.azure_client_id or "").strip()
        client_secret = (settings.azure_client_secret or "").strip()
        if not client_id or not client_secret:
            raise GraphConfigError(
                "Microsoft Graph is not configured. Set AZURE_CLIENT_ID and "
                "AZURE_CLIENT_SECRET."
            )
        tenant = (settings.azure_tenant_id or "").strip() or "common"
        return client_id, client_secret, tenant

    def _token_endpoint(self) -> str:
        _, _, tenant = self._get_azure_credentials()
        return f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"

    def _decode_access_token_expiry(self, access_token: str) -> datetime:
        try:
            parts = access_token.split(".")
            payload_b64 = parts[1] if len(parts) > 1 else ""
            padded = payload_b64 + "=" * (-len(payload_b64) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
            exp = payload.get("exp")
            if exp:
                return datetime.fromtimestamp(exp, tz=timezone.utc)
        except Exception:
            pass
        return datetime.fromtimestamp(
            (datetime.now(timezone.utc).timestamp() * 1000 + DEFAULT_ACCESS_TOKEN_TTL_MS)
            / 1000,
            tz=timezone.utc,
        )

    # -- token persistence --------------------------------------------------

    async def sync_from_provider_tokens(
        self,
        user_id: str,
        provider_access_token: str,
        provider_refresh_token: str | None = None,
    ) -> None:
        """Upsert the user's encrypted Microsoft tokens (POST /graph/sync)."""
        expires_at = self._decode_access_token_expiry(provider_access_token)
        encrypted_refresh = (
            self.encryption.encrypt(provider_refresh_token)
            if provider_refresh_token
            else None
        )

        stmt = pg_insert(OAuthToken).values(
            user_id=user_id,
            access_token=self.encryption.encrypt(provider_access_token),
            refresh_token=encrypted_refresh,
            expires_at=expires_at,
        )
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

    async def _refresh_access_token(
        self, refresh_token: str
    ) -> tuple[str, str | None, datetime]:
        """Returns (access_token, refresh_token, expires_at)."""
        client_id, client_secret, _ = self._get_azure_credentials()

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                self._token_endpoint(),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "scope": GRAPH_OAUTH_SCOPE,
                },
            )

        data = response.json()
        if response.status_code >= 400 or not data.get("access_token"):
            raise GraphRequestError(
                data.get("error_description")
                or data.get("error")
                or "Failed to refresh Microsoft token"
            )

        expires_in = data.get("expires_in") or 3600
        return (
            data["access_token"],
            data.get("refresh_token") or refresh_token,
            datetime.fromtimestamp(
                datetime.now(timezone.utc).timestamp() + expires_in, tz=timezone.utc
            ),
        )

    async def get_access_token(self, ctx: AuthContext) -> str:
        """Return a valid (refreshing if needed) decrypted Microsoft access token."""
        row = (
            await self.db.execute(
                select(
                    OAuthToken.access_token,
                    OAuthToken.refresh_token,
                    OAuthToken.expires_at,
                ).where(OAuthToken.user_id == ctx.user_id)
            )
        ).first()

        if row is None:
            raise GraphRequestError(
                "Microsoft account not connected. Sign in again with Microsoft."
            )

        now_ms = datetime.now(timezone.utc).timestamp() * 1000
        expires_ms = row.expires_at.timestamp() * 1000 if row.expires_at else 0
        needs_refresh = not expires_ms or (expires_ms - now_ms) < TOKEN_EXPIRY_SKEW_MS

        if not needs_refresh:
            return self.encryption.decrypt(row.access_token)

        if not row.refresh_token:
            raise GraphRequestError(
                "Microsoft session expired. Sign in again with Microsoft."
            )

        refresh_token = self.encryption.decrypt(row.refresh_token)
        access_token, new_refresh, expires_at = await self._refresh_access_token(
            refresh_token
        )

        await self.db.execute(
            pg_insert(OAuthToken)
            .values(
                user_id=ctx.user_id,
                access_token=self.encryption.encrypt(access_token),
                refresh_token=(
                    self.encryption.encrypt(new_refresh) if new_refresh else None
                ),
                expires_at=expires_at,
            )
            .on_conflict_do_update(
                index_elements=[OAuthToken.user_id],
                set_={
                    "access_token": self.encryption.encrypt(access_token),
                    "refresh_token": (
                        self.encryption.encrypt(new_refresh) if new_refresh else None
                    ),
                    "expires_at": expires_at,
                    "updated_at": func.now(),
                },
            )
        )
        await self.db.commit()

        return access_token

    # -- low-level Graph HTTP helpers --------------------------------------

    async def _graph_get(
        self, token: str, path_or_url: str, params: dict | None = None
    ) -> dict:
        url = path_or_url if path_or_url.startswith("http") else f"{GRAPH_BASE}{path_or_url}"
        async with httpx.AsyncClient(timeout=60.0) as client:
            res = await client.get(
                url, headers={"Authorization": f"Bearer {token}"}, params=params
            )
        if res.status_code >= 400:
            raise GraphRequestError(f"Graph GET {path_or_url} failed: {res.status_code}")
        return res.json()

    async def _graph_post(
        self, token: str, path: str, body: dict | None = None
    ) -> dict:
        async with httpx.AsyncClient(timeout=60.0) as client:
            res = await client.post(
                f"{GRAPH_BASE}{path}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json=body or {},
            )
        if res.status_code >= 400:
            raise GraphRequestError(f"Graph POST {path} failed: {res.status_code}")
        return res.json() if res.content else {}

    async def _graph_patch(self, token: str, path: str, body: dict) -> None:
        async with httpx.AsyncClient(timeout=60.0) as client:
            res = await client.patch(
                f"{GRAPH_BASE}{path}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
        if res.status_code >= 400:
            raise GraphRequestError(f"Graph PATCH {path} failed: {res.status_code}")

    @staticmethod
    def _map_email(m: dict) -> GraphEmail:
        return GraphEmail(
            id=m.get("id"),
            subject=m.get("subject") or "(no subject)",
            sender=((m.get("from") or {}).get("emailAddress") or {}).get("address")
            or "unknown",
            received_at=m.get("receivedDateTime"),
            body=clean_email_body((m.get("body") or {}).get("content") or ""),
            conversation_id=m.get("conversationId"),
            is_read=bool(m.get("isRead")),
        )

    # -- mail ---------------------------------------------------------------

    async def fetch_recent_emails(self, ctx: AuthContext, top: int = 50) -> list[GraphEmail]:
        token = await self.get_access_token(ctx)
        response = await self._graph_get(
            token,
            "/me/messages",
            params={
                "$top": top,
                "$select": "id,subject,from,receivedDateTime,body,conversationId,isRead",
                "$orderby": "receivedDateTime desc",
            },
        )
        return [self._map_email(m) for m in (response.get("value") or [])]

    async def delete_message(self, ctx: AuthContext, message_id: str) -> None:
        """Move a message to Deleted Items (matches the Outlook "Delete" button).

        We intentionally do NOT use Graph's DELETE /me/messages/{id} (which soft
        deletes into the hidden Recoverable Items dumpster); moving to the
        `deleteditems` well-known folder reproduces the Outlook UI behavior.
        """
        token = await self.get_access_token(ctx)
        await self._graph_post(
            token, f"/me/messages/{message_id}/move", {"destinationId": "deleteditems"}
        )

    async def fetch_inbox_emails(self, ctx: AuthContext, top: int = 30) -> list[GraphEmail]:
        """Recent messages from the Inbox folder only (excludes Sent Items / Drafts)."""
        token = await self.get_access_token(ctx)
        response = await self._graph_get(
            token,
            "/me/mailFolders/inbox/messages",
            params={
                "$top": top,
                "$select": "id,subject,from,receivedDateTime,body,conversationId,isRead",
                "$orderby": "receivedDateTime desc",
            },
        )
        return [self._map_email(m) for m in (response.get("value") or [])]

    async def fetch_sent_emails(self, ctx: AuthContext, top: int = 30) -> list[GraphEmail]:
        """Recent Sent Items; `sender` carries the To recipients (UI labels it "To:")."""
        token = await self.get_access_token(ctx)
        response = await self._graph_get(
            token,
            "/me/mailFolders/sentitems/messages",
            params={
                "$top": top,
                "$select": (
                    "id,subject,toRecipients,receivedDateTime,sentDateTime,body,"
                    "conversationId"
                ),
                "$orderby": "receivedDateTime desc",
            },
        )
        results: list[GraphEmail] = []
        for m in response.get("value") or []:
            recipients = m.get("toRecipients") or []
            to = [
                (r.get("emailAddress") or {}).get("address")
                for r in recipients
                if (r.get("emailAddress") or {}).get("address")
            ]
            results.append(
                GraphEmail(
                    id=m.get("id"),
                    subject=m.get("subject") or "(no subject)",
                    sender=", ".join(to) if to else "unknown",
                    received_at=m.get("sentDateTime") or m.get("receivedDateTime"),
                    body=clean_email_body((m.get("body") or {}).get("content") or ""),
                    conversation_id=m.get("conversationId"),
                    is_read=True,
                )
            )
        return results

    async def fetch_message_by_id(
        self, ctx: AuthContext, message_id: str
    ) -> GraphEmail | None:
        """Fetch a single message (with body) by Graph id; None if missing."""
        token = await self.get_access_token(ctx)
        try:
            m = await self._graph_get(
                token,
                f"/me/messages/{message_id}",
                params={
                    "$select": (
                        "id,subject,from,receivedDateTime,body,conversationId,isRead"
                    )
                },
            )
            return self._map_email(m)
        except Exception as err:
            logger.warning(f"[graph] fetchMessageById {message_id}: {err}")
            return None

    @staticmethod
    def _escape_html(text: str) -> str:
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("\n", "<br>")
        )

    async def create_reply_draft(
        self, ctx: AuthContext, message_id: str, reply_text: str
    ) -> None:
        """Create a draft reply in the user's Drafts folder, with replyText above
        the quoted original. The user reviews and sends it from Outlook."""
        token = await self.get_access_token(ctx)
        draft = await self._graph_post(token, f"/me/messages/{message_id}/createReply", {})
        original = (draft.get("body") or {}).get("content") or ""
        escaped = self._escape_html(reply_text)
        content = f"<div>{escaped}</div><br>{original}"
        await self._graph_patch(
            token,
            f"/me/messages/{draft.get('id')}",
            {"body": {"contentType": "html", "content": content}},
        )

    async def create_draft(
        self, ctx: AuthContext, draft: dict
    ) -> dict:
        """Create a standalone draft message (not a reply) in Drafts.

        `draft` has keys subject, body and optional to. Returns {"id": <id>}.
        """
        token = await self.get_access_token(ctx)
        escaped = self._escape_html(draft.get("body") or "")
        message: dict = {
            "subject": draft.get("subject"),
            "body": {"contentType": "html", "content": f"<div>{escaped}</div>"},
        }
        to = (draft.get("to") or "").strip()
        if to:
            message["toRecipients"] = [{"emailAddress": {"address": to}}]
        created = await self._graph_post(token, "/me/messages", message)
        return {"id": created.get("id") or ""}

    async def search_emails(
        self, ctx: AuthContext, query: str, top: int = 25
    ) -> list[GraphEmail]:
        token = await self.get_access_token(ctx)
        cleaned = query.replace('"', "")
        response = await self._graph_get(
            token,
            "/me/messages",
            params={
                "$search": f'"{cleaned}"',
                "$top": top,
                "$select": "id,subject,from,receivedDateTime,body,conversationId,isRead",
            },
        )
        return [self._map_email(m) for m in (response.get("value") or [])]

    # -- drives / sites -----------------------------------------------------

    async def list_sharepoint_sites(
        self, ctx: AuthContext, limit: int
    ) -> list[GraphSite]:
        token = await self.get_access_token(ctx)
        sites: list[GraphSite] = []
        try:
            response = await self._graph_get(
                token,
                "/sites",
                params={
                    "search": "*",
                    "$top": min(limit, 50),
                    "$select": "id,displayName,webUrl",
                },
            )
            while response:
                for s in response.get("value") or []:
                    sites.append(
                        GraphSite(
                            id=s.get("id"),
                            display_name=s.get("displayName") or "SharePoint site",
                            web_url=s.get("webUrl"),
                        )
                    )
                    if len(sites) >= limit:
                        return sites
                next_link = response.get("@odata.nextLink")
                if not next_link or len(sites) >= limit:
                    break
                response = await self._graph_get(token, next_link)
        except Exception as err:
            logger.warning(f"[graph] listSharePointSites failed: {err}")
            raise
        return sites

    async def list_site_drives(self, ctx: AuthContext, site_id: str) -> list[GraphDrive]:
        token = await self.get_access_token(ctx)
        drives: list[GraphDrive] = []

        try:
            response = await self._graph_get(
                token,
                f"/sites/{site_id}/drives",
                params={"$select": "id,name", "$top": 50},
            )
            for d in response.get("value") or []:
                drives.append(GraphDrive(id=d.get("id"), name=d.get("name") or "Documents"))
        except Exception as err:
            logger.warning(f"[graph] listSiteDrives siteId={site_id}: {err}")

        if not drives:
            try:
                default_drive = await self._graph_get(
                    token, f"/sites/{site_id}/drive", params={"$select": "id,name"}
                )
                if default_drive.get("id"):
                    drives.append(
                        GraphDrive(
                            id=default_drive.get("id"),
                            name=default_drive.get("name") or "Documents",
                        )
                    )
            except Exception:
                pass

        return drives

    async def list_drive_children(
        self, ctx: AuthContext, drive_id: str, item_id: str | None = None
    ) -> list[dict]:
        token = await self.get_access_token(ctx)
        path = (
            f"/drives/{drive_id}/items/{item_id}/children"
            if item_id
            else f"/drives/{drive_id}/root/children"
        )

        items: list[dict] = []
        response = await self._graph_get(
            token,
            path,
            params={
                "$select": "id,name,webUrl,lastModifiedDateTime,size,folder,file",
                "$top": 200,
            },
        )
        while response:
            items.extend(response.get("value") or [])
            next_link = response.get("@odata.nextLink")
            if not next_link:
                break
            response = await self._graph_get(token, next_link)
        return items

    async def walk_drive_files(
        self,
        ctx: AuthContext,
        drive_id: str,
        opts: WalkDriveOptions,
        context: dict,
    ) -> list[GraphDriveItem]:
        """Breadth-first walk of a drive collecting files (not folders).

        `context` carries siteId, siteName and the required `source` literal.
        """
        source: DocumentSource = context["source"]
        files: list[GraphDriveItem] = []
        queue: list[dict] = [{"item_id": None, "depth": 0, "parent_path": ""}]

        while queue and len(files) < opts.max_files:
            current = queue.pop(0)
            if current["depth"] > opts.max_depth:
                continue

            try:
                children = await self.list_drive_children(
                    ctx, drive_id, current["item_id"]
                )
            except Exception as err:
                logger.warning(
                    f"[graph] listDriveChildren drive={drive_id} "
                    f"item={current['item_id'] or 'root'}: {err}"
                )
                continue

            for child in children:
                path = (
                    f"{current['parent_path']}/{child.get('name')}"
                    if current["parent_path"]
                    else child.get("name")
                )

                if child.get("folder"):
                    if current["depth"] < opts.max_depth:
                        queue.append(
                            {
                                "item_id": child.get("id"),
                                "depth": current["depth"] + 1,
                                "parent_path": path,
                            }
                        )
                    continue

                files.append(
                    GraphDriveItem(
                        id=child.get("id"),
                        name=child.get("name"),
                        web_url=child.get("webUrl"),
                        last_modified=child.get("lastModifiedDateTime"),
                        drive_id=drive_id,
                        site_id=context.get("site_id"),
                        site_name=context.get("site_name"),
                        mime_type=(child.get("file") or {}).get("mimeType"),
                        size=child.get("size"),
                        is_folder=False,
                        parent_path=path,
                        source=source,
                    )
                )

                if len(files) >= opts.max_files:
                    break

        return files

    async def download_drive_item(
        self, ctx: AuthContext, drive_id: str, item_id: str
    ) -> DownloadedFile:
        limits = self.get_crawl_limits()
        token = await self.get_access_token(ctx)

        meta = await self._graph_get(
            token,
            f"/drives/{drive_id}/items/{item_id}",
            params={"$select": "size,file,@microsoft.graph.downloadUrl"},
        )

        size = meta.get("size") or 0
        if size > limits.max_file_bytes:
            raise GraphRequestError(
                f"File exceeds max size ({limits.max_file_bytes} bytes)"
            )

        download_url = meta.get(
            "@microsoft.graph.downloadUrl"
        ) or f"{GRAPH_BASE}/drives/{drive_id}/items/{item_id}/content"

        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
            res = await client.get(
                download_url, headers={"Authorization": f"Bearer {token}"}
            )

        if res.status_code >= 400:
            raise GraphRequestError(f"Failed to download file: {res.status_code}")

        content_type = (
            res.headers.get("content-type")
            or (meta.get("file") or {}).get("mimeType")
            or "application/octet-stream"
        )
        return DownloadedFile(buffer=res.content, content_type=content_type)

    async def collect_sharepoint_files(self, ctx: AuthContext) -> list[GraphDriveItem]:
        limits = self.get_crawl_limits()
        sites = await self.list_sharepoint_sites(ctx, limits.max_sites)
        all_files: list[GraphDriveItem] = []

        logger.info(f"[graph] SharePoint crawl sites={len(sites)}")

        for site in sites:
            if len(all_files) >= limits.max_files:
                break

            drives = await self.list_site_drives(ctx, site.id)
            for drive in drives:
                if len(all_files) >= limits.max_files:
                    break

                walked = await self.walk_drive_files(
                    ctx,
                    drive.id,
                    WalkDriveOptions(
                        max_depth=limits.max_depth,
                        max_files=limits.max_files - len(all_files),
                    ),
                    {
                        "site_id": site.id,
                        "site_name": site.display_name,
                        "source": "sharepoint",
                    },
                )
                all_files.extend(walked)

        return all_files

    async def collect_onedrive_files(self, ctx: AuthContext) -> list[GraphDriveItem]:
        limits = self.get_crawl_limits()
        token = await self.get_access_token(ctx)
        drive = await self._graph_get(token, "/me/drive", params={"$select": "id"})
        drive_id = drive.get("id")

        return await self.walk_drive_files(
            ctx,
            drive_id,
            WalkDriveOptions(max_depth=limits.max_depth, max_files=limits.max_files),
            {"source": "onedrive"},
        )

    async def collect_onedrive_files_delta(
        self, ctx: AuthContext, store: DeltaCursorStore
    ) -> list[GraphDriveItem]:
        """Incremental OneDrive sync using Graph delta when a cursor exists."""
        limits = self.get_crawl_limits()
        token = await self.get_access_token(ctx)
        drive = await self._graph_get(token, "/me/drive", params={"$select": "id"})
        drive_id = drive.get("id")
        saved_delta = await store.get_delta_link(ctx, "onedrive", drive_id)

        if not saved_delta:
            files = await self.collect_onedrive_files(ctx)
            try:
                next_link: str | None = f"/drives/{drive_id}/root/delta"
                latest_delta: str | None = None
                while next_link:
                    page = await self._graph_get(token, next_link)
                    latest_delta = page.get("@odata.deltaLink") or latest_delta
                    next_link = page.get("@odata.nextLink")
                if latest_delta:
                    await store.set_delta_link(ctx, "onedrive", drive_id, latest_delta)
            except Exception as err:
                logger.warning(f"[graph] initial delta cursor failed: {err}")
            return files

        files: list[GraphDriveItem] = []
        next_link = saved_delta
        latest_delta = None

        try:
            while next_link and len(files) < limits.max_files:
                page = await self._graph_get(token, next_link)
                for item in page.get("value") or []:
                    if item.get("folder") or item.get("deleted"):
                        continue
                    parent = item.get("parentReference") or {}
                    files.append(
                        GraphDriveItem(
                            id=item.get("id"),
                            name=item.get("name") or "file",
                            web_url=item.get("webUrl"),
                            last_modified=item.get("lastModifiedDateTime"),
                            drive_id=parent.get("driveId") or drive_id,
                            site_id=parent.get("siteId"),
                            mime_type=(item.get("file") or {}).get("mimeType"),
                            size=item.get("size"),
                            is_folder=False,
                            source="onedrive",
                        )
                    )
                    if len(files) >= limits.max_files:
                        break
                next_link = page.get("@odata.nextLink")
                latest_delta = page.get("@odata.deltaLink") or latest_delta
            if latest_delta:
                await store.set_delta_link(ctx, "onedrive", drive_id, latest_delta)
            logger.info(f"[graph] OneDrive delta sync files={len(files)}")
            if not files:
                return await self.collect_onedrive_files(ctx)
            return files
        except Exception as err:
            logger.warning(f"[graph] delta sync failed, full crawl: {err}")
            await store.set_delta_link(ctx, "onedrive", drive_id, None)
            return await self.collect_onedrive_files(ctx)

    async def fetch_recent_drive_items(
        self, ctx: AuthContext, top: int = 25
    ) -> list[GraphDriveItem]:
        token = await self.get_access_token(ctx)
        items: list[GraphDriveItem] = []

        try:
            response = await self._graph_get(
                token,
                "/me/drive/recent",
                params={
                    "$top": top,
                    "$select": (
                        "id,name,webUrl,lastModifiedDateTime,size,file,folder,"
                        "parentReference"
                    ),
                },
            )
            for r in response.get("value") or []:
                if r.get("folder"):
                    continue
                parent = r.get("parentReference") or {}
                items.append(
                    GraphDriveItem(
                        id=r.get("id"),
                        name=r.get("name") or "file",
                        web_url=r.get("webUrl"),
                        last_modified=r.get("lastModifiedDateTime"),
                        drive_id=parent.get("driveId") or "",
                        site_id=parent.get("siteId"),
                        mime_type=(r.get("file") or {}).get("mimeType"),
                        size=r.get("size"),
                        is_folder=False,
                        source="sharepoint" if parent.get("siteId") else "onedrive",
                    )
                )
        except Exception as err:
            logger.warning(f"[graph] fetchRecentDriveItems failed: {err}")

        return [f for f in items if f.drive_id]

    async def search_drive_items(
        self, ctx: AuthContext, query: str, top: int = 15
    ) -> list[GraphDriveItem]:
        token = await self.get_access_token(ctx)
        body = {
            "requests": [
                {
                    "entityTypes": ["driveItem"],
                    "query": {"queryString": query},
                    "from": 0,
                    "size": top,
                }
            ]
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            res = await client.post(
                f"{GRAPH_BASE}/search/query",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json=body,
            )

        if res.status_code >= 400:
            raise GraphRequestError(f"Graph search failed: {res.status_code}")

        data = res.json()
        value = data.get("value") or []
        hits = (
            ((value[0].get("hitsContainers") or [{}])[0].get("hits") or [])
            if value
            else []
        )

        items: list[GraphDriveItem] = []
        for hit in hits:
            r = hit.get("resource")
            if not r or not r.get("id"):
                continue
            parent = r.get("parentReference") or {}
            items.append(
                GraphDriveItem(
                    id=r.get("id"),
                    name=r.get("name") or "file",
                    web_url=r.get("webUrl"),
                    last_modified=r.get("lastModifiedDateTime"),
                    drive_id=parent.get("driveId") or "",
                    site_id=parent.get("siteId"),
                    mime_type=(r.get("file") or {}).get("mimeType"),
                    size=r.get("size"),
                    is_folder=bool(r.get("folder")),
                    source="sharepoint" if parent.get("siteId") else "onedrive",
                )
            )

        return [f for f in items if f.drive_id and not f.is_folder]

    async def fetch_drive_files(
        self, ctx: AuthContext, source: DocumentSource
    ) -> list[GraphDriveItem]:
        """Deprecated: use collect_sharepoint_files / collect_onedrive_files."""
        if source == "onedrive":
            return await self.collect_onedrive_files(ctx)
        return await self.collect_sharepoint_files(ctx)
