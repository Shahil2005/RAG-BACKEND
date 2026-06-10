"""Security primitives shared across auth services.

Faithful port of the NestJS auth crypto:
  - JWT: HS256 signed with JWT_SECRET, payload {sub, sid} (see JwtSessionService).
  - jwt_expires_in: supports "7d"/"24h"/"30m"/"60s"/"2w" or a bare seconds integer.
  - hash_token: SHA-256 hex digest stored in sessions.jwt_token.
"""

import hashlib
import re
from datetime import datetime, timedelta, timezone

import jwt

from app.core.settings import settings

_DURATION_RE = re.compile(r"^(\d+)\s*([smhdw])$")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
_DEFAULT_EXPIRY_SECONDS = 7 * 86400


def parse_duration_seconds(value: str | None, default: int = _DEFAULT_EXPIRY_SECONDS) -> int:
    """Parse a vercel/ms-style duration string into seconds (matches @nestjs/jwt)."""
    if not value:
        return default
    raw = value.strip()
    if raw.isdigit():
        return int(raw)
    match = _DURATION_RE.match(raw)
    if not match:
        return default
    return int(match.group(1)) * _UNIT_SECONDS[match.group(2)]


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def sign_session_jwt(sub: str, sid: str) -> tuple[str, datetime]:
    """Sign a session JWT and return (token, expires_at)."""
    expires_in = parse_duration_seconds(settings.jwt_expires_in)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=expires_in)
    token = jwt.encode(
        {
            "sub": sub,
            "sid": sid,
            "iat": int(now.timestamp()),
            "exp": int(expires_at.timestamp()),
        },
        settings.jwt_secret,
        algorithm="HS256",
    )
    return token, expires_at


def verify_session_jwt(token: str) -> dict:
    """Verify signature + expiry; raises jwt.PyJWTError on failure."""
    return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
