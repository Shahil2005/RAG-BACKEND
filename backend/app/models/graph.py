"""Graph module models.

The graph module owns no tables of its own. Its only persisted state is the
`oauth_tokens` row (access/refresh tokens), which is part of the foundation auth
schema and already modeled as `OAuthToken` in `app.models.user`. It is re-exported
here so callers can import it from the module namespace without redefining it.
"""

from app.models.user import OAuthToken

__all__ = ("OAuthToken",)
