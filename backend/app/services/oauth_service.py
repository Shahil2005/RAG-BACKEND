"""Microsoft OAuth + Graph service (port of the NestJS OAuthService).

Login state is stored in Redis with a short TTL and an in-memory fallback when Redis
is unavailable, exactly like the original implementation.
"""

import base64
import json
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx

from app import logger
from app.core.constants import GRAPH_OAUTH_SCOPE, OAUTH_STATE_TTL_SEC
from app.core.redis import RedisHelper
from app.core.settings import settings
from app.schema.auth import GraphProfile


@dataclass
class ExchangedTokens:
    access_token: str
    refresh_token: str | None
    expires_at: datetime


# In-memory fallback when Redis is unavailable: state -> (expires_epoch, popup)
_memory_oauth_state: dict[str, tuple[float, bool]] = {}


class OAuthError(Exception):
    """Raised on any recoverable OAuth failure (maps to a login redirect)."""


class OAuthService:
    def __init__(self, cache: RedisHelper | None = None) -> None:
        self._cache = cache

    @property
    def cache(self) -> RedisHelper:
        if self._cache is None:
            self._cache = RedisHelper()
        return self._cache

    def _azure_credentials(self) -> tuple[str, str, str]:
        client_id = (settings.azure_client_id or "").strip()
        client_secret = (settings.azure_client_secret or "").strip()
        tenant = (settings.azure_tenant_id or "").strip() or "common"
        if not client_id or not client_secret:
            msg = (
                "Microsoft OAuth is not configured. Set AZURE_CLIENT_ID and "
                "AZURE_CLIENT_SECRET in the backend .env"
            )
            raise OAuthError(msg)
        return client_id, client_secret, tenant

    def get_redirect_uri(self) -> str:
        if settings.oauth_redirect_uri:
            return settings.oauth_redirect_uri.strip()
        return f"{settings.web_origin}/api/v1/auth/microsoft/callback"

    async def _store_oauth_state(self, state: str, popup: bool) -> None:
        value = "popup" if popup else "redirect"
        try:
            ok = await self.cache.set(f"oauth:state:{state}", value, expire=OAUTH_STATE_TTL_SEC)
            if not ok:
                raise RuntimeError("redis set failed")
        except Exception as err:
            logger.warning(f"[oauth] state store redis fallback: {err!r}")
            _memory_oauth_state[state] = (time.time() + OAUTH_STATE_TTL_SEC, popup)

    async def _consume_oauth_state(self, state: str) -> bool | None:
        """Returns popup flag if state is valid, else None."""
        try:
            key = f"oauth:state:{state}"
            stored = await self.cache.get(key)
            if stored is None:
                raise RuntimeError("miss")
            await self.cache.delete(key)
            return stored == "popup"
        except Exception:
            meta = _memory_oauth_state.pop(state, None)
            if meta is None or meta[0] < time.time():
                return None
            return meta[1]

    async def create_login_state(self, popup: bool = False) -> tuple[str, str]:
        """Returns (state, authorize_url)."""
        state = secrets.token_hex(32)
        await self._store_oauth_state(state, popup)

        client_id, _, tenant = self._azure_credentials()
        params = urlencode(
            {
                "client_id": client_id,
                "response_type": "code",
                "redirect_uri": self.get_redirect_uri(),
                "response_mode": "query",
                "scope": GRAPH_OAUTH_SCOPE,
                "state": state,
                "prompt": "select_account",
            }
        )
        authorize_url = (
            f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize?{params}"
        )
        return state, authorize_url

    async def validate_state(self, state: str) -> bool:
        """Returns popup flag; raises OAuthError if invalid/expired."""
        popup = await self._consume_oauth_state(state)
        if popup is None:
            raise OAuthError("Invalid or expired OAuth state")
        return popup

    async def exchange_code(self, code: str) -> ExchangedTokens:
        client_id, client_secret, tenant = self._azure_credentials()
        token_url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                token_url,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": self.get_redirect_uri(),
                    "scope": GRAPH_OAUTH_SCOPE,
                },
            )

        data = response.json()
        if response.status_code >= 400 or not data.get("access_token"):
            logger.error(
                f"[oauth] token exchange failed status={response.status_code} "
                f"error={data.get('error')} desc={data.get('error_description')}"
            )
            raise OAuthError(
                data.get("error_description") or data.get("error") or "Token exchange failed"
            )

        expires_in = data.get("expires_in") or 3600
        return ExchangedTokens(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=expires_in),
        )

    async def fetch_graph_profile(self, access_token: str) -> GraphProfile:
        async with httpx.AsyncClient(timeout=30.0) as client:
            res = await client.get(
                "https://graph.microsoft.com/v1.0/me",
                headers={"Authorization": f"Bearer {access_token}"},
            )
        if res.status_code >= 400:
            logger.error(f"[oauth] graph profile failed status={res.status_code}")
            raise OAuthError("Failed to fetch Microsoft Graph profile")
        return GraphProfile.model_validate(res.json())

    def decode_access_token_expiry(self, access_token: str) -> datetime:
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
        return datetime.now(timezone.utc) + timedelta(hours=1)
