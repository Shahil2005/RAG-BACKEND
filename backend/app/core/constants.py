# ** ------------------------------ Celery Setup --------------------------
CEL_DEFAULT_QUEUE = "my_lab"
CEL_MAIN_NAME = "lab_queue_"
CEL_TASK_PATHS = ["app.job.tasks"]
# ** ------------------------------ Celery Setup:END --------------------------


# ** ------------------------------ Celery DB Setup --------------------------
# Connection-pool settings for the Celery worker's DB sessions (see app/core/celery.py).
DB_CEL_ECHO = True
DB_CEL_POOL_SIZE = (5,)
DB_CEL_MAX_OVERFLOW = 10
DB_CEL_POOL_TIMEOUT = 30
# ** ------------------------------ Celery DB Setup:END --------------------------


# ** ------------------------------ Microsoft Graph OAuth --------------------------
# Mirrors packages/config GRAPH_SCOPES from the original monorepo.
GRAPH_SCOPES = (
    "openid",
    "profile",
    "email",
    "offline_access",
    "User.Read",
    "Mail.Read",
    "Mail.ReadWrite",
    "Files.Read.All",
    "Sites.Read.All",
)
GRAPH_OAUTH_SCOPE = " ".join(GRAPH_SCOPES)

# OAuth login-state lifetime (seconds).
OAUTH_STATE_TTL_SEC = 600
# ** ------------------------------ Microsoft Graph OAuth:END ----------------------


# ** ------------------------------ RAG / Vector config --------------------------
# Mirrors packages/config from the original monorepo (@starbot/config).
EMBEDDING_DIMENSIONS = 384
DEFAULT_TOP_K = 20
RERANK_TOP_K = 5
CHUNK_SIZE = 512
CHUNK_OVERLAP = 64
LLM_MODEL = "gpt-5.4-mini"
# ** ------------------------------ RAG / Vector config:END ----------------------
