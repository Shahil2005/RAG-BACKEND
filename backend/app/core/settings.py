"""Application settings.

Reads the EXISTING project .env (DATABASE_*, JWT_*, AZURE_*, TOKEN_ENCRYPTION_KEY,
CORS_ORIGIN, ...) so the migrated FastAPI backend is a drop-in for the NestJS API
without requiring any change to the deployed environment.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # pydantic-settings: LATER files in the tuple win. List least-specific first so
        # the app-local backend/.env takes precedence over the repo-root .env.
        env_file=("../../.env", "../.env", ".env"),
        extra="ignore",
        case_sensitive=False,
    )

    # --- App ---
    debug: bool = False
    app_env: str = "development"
    node_env: str = "development"
    api_port: int = 3001

    # --- Database (matches existing NestJS DATABASE_* vars) ---
    database_host: str = "localhost"
    database_port: int = 5432
    database_name: str = "starbot"
    database_user: str = "postgres"
    database_password: str = ""

    # --- Auth / JWT ---
    jwt_secret: str = "change-me"
    jwt_expires_in: str = "7d"
    session_cookie_name: str = "starbot_session"
    cookie_secure: bool = False

    # --- CORS / OAuth ---
    cors_origin: str = "http://localhost:3000"
    oauth_redirect_uri: str | None = None

    # --- Azure / Microsoft Graph ---
    azure_client_id: str | None = None
    azure_client_secret: str | None = None
    azure_tenant_id: str = "common"

    # --- Token encryption (AES-256-GCM, 64 hex chars / 32 bytes) ---
    token_encryption_key: str | None = None

    # --- Redis ---
    redis_url: str | None = None
    redis_password: str | None = None
    redis_port: int = 6379

    # --- AI service (internal embed/rerank/classify microservice) ---
    ai_service_url: str = "http://localhost:8001"
    ai_service_port: int = 8001

    # --- Worker / ingestion scheduler (ported from apps/worker BullMQ) ---
    # Service token the worker used to authenticate to the API; now used to
    # gate the scheduled-sync tasks the same way the NestJS worker did.
    worker_service_token: str | None = None
    api_url: str = "http://localhost:3001"
    # Default: 6 hours (matches apps/worker `INGESTION_SYNC_INTERVAL_MS`).
    ingestion_sync_interval_ms: int = 6 * 60 * 60 * 1000

    # --- OpenAI ---
    openai_api_key: str | None = None

    # --- Pinecone ---
    pinecone_api_key: str | None = None
    pinecone_index_name: str = "starbot-dev"
    pinecone_environment: str | None = None

    # --- Teams ingestion (gated until ChannelMessage.Read.All admin consent) ---
    enable_teams_ingestion: bool = False

    @property
    def db_url(self) -> str:
        """Async SQLAlchemy URL (asyncpg driver)."""
        return (
            f"postgresql+asyncpg://{self.database_user}:{self.database_password}"
            f"@{self.database_host}:{self.database_port}/{self.database_name}"
        )

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_origin.split(",") if o.strip()]

    @property
    def web_origin(self) -> str:
        origins = self.cors_origins
        return origins[0] if origins else "http://localhost:3000"


settings = Settings()
