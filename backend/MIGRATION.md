# Backend migration: NestJS → FastAPI — COMPLETE (pending live-DB validation)

The former NestJS `apps/api` (~8.6k LOC, 25 modules) + BullMQ `apps/worker` were ported
to this FastAPI backend (on the team boilerplate), with **full SQLAlchemy ORM**. The web
app was extracted to a standalone `frontend/`, and the Python AI microservice now lives at
`backend/ai-service/`.

## Final structure

```
RAG-OUTLOOK/
├── frontend/            # Next.js (was apps/web) — standalone, @starbot/types vendored to src/types/starbot.ts
├── backend/            # FastAPI (replaces apps/api + apps/worker)
│   ├── app/
│   │   ├── core/       # settings, database, security, redis, celery, trace middleware, utils, constants
│   │   ├── models/     # SQLAlchemy ORM (19 tables)
│   │   ├── schema/     # Pydantic (camelCase JSON preserved via serialization_alias)
│   │   ├── services/   # business logic (one *_service.py per module)
│   │   ├── router/     # APIRouter per module, all under /api/v1
│   │   ├── job/        # Celery tasks (was BullMQ worker)
│   │   └── prompts/    # ported @starbot/prompts
│   └── ai-service/     # embeddings/rerank/agent FastAPI microservice (was apps/ai-service)
├── infrastructure/     # Postgres migrations (still authoritative — models mirror these)
└── _legacy-nestjs/     # archived monorepo build files + old worker (safe to delete after validation)
```

## What was migrated (all verified to import + boot)

- **47 routes** under `/api/v1` — 100% parity with the NestJS controllers (verified by diffing
  every `@Get/@Post/@Put/@Patch/@Delete` against the FastAPI route table).
- **19 ORM tables**, all mappers configure cleanly, mirroring `infrastructure/postgres/migrations/*.sql`.
- Modules: auth, user, health (foundation) + audit, teams, workspaces, graph, mail, search,
  query, documents, ingestion, rag, projects, chat, pinecone + worker→Celery + shared
  (types/config/utils/prompts, common, redis, ai-client).
- Auth: HS256 JWT, AES-256-GCM token encryption **byte-compatible** with the Node format,
  MS Graph OAuth, `CurrentUserDep` replacing `JwtAuthGuard`/`@CurrentUser`.
- External SDKs: OpenAI (`openai`), Pinecone (`pinecone`), MS Graph (`httpx`), docs
  (`python-docx`/`pypdf`/`openpyxl`/`reportlab`).

### Ownership / dedup note
Several tables are read by multiple modules. Single owner per table; readers re-export:
`ingestion` owns email_metadata/file_metadata/ingestion_jobs/sync_cursors; `projects` owns
projects/project_sectors/project_files; `workspaces` owns workspace_instructions; `mail` owns
email_classifications. `app/models/rag.py` and `mail.py` re-export the owners (no duplicate
`Base.metadata` registration). `app/services/{outlook_mail,sharepoint_documents,document_text}_service.py`
are thin shims re-exporting classes that live inside rag/ingestion services.

## ⚠️ Verified vs. not-yet-verified

**Verified (static):** every service + router module imports clean; all ORM mappers configure;
route table matches NestJS; AES-GCM/JWT/duration round-trips; frontend `tsc --noEmit` passes.

**NOT yet verified (needs a live Postgres + Redis + Pinecone/OpenAI/Graph creds):** runtime
behavior — actual SQL execution, column-level parity, and the lazy cross-service calls. These
could not be exercised in the build sandbox. **Before the team relies on it, run the backend
against a real DB and smoke-test the key flows.**

**Known stubs (search `TODO(migration)`):** `AuthService._schedule_post_login_sync` (wire to
ingestion); `teams.sync_teams` (placeholder, same as the original); worker `_run_sync` guard.

## Run locally (Windows)

```powershell
# Backend
cd backend
python -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 3001   # docs: /api/docs

# AI microservice (separate process; backend calls it at AI_SERVICE_URL)
cd backend\ai-service
python -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8001

# Celery worker (background ingestion/scheduler)
cd backend
.\.venv\Scripts\celery.exe -A app.core.celery.celery_app worker -B

# Frontend
cd frontend
npm install
npm run dev    # http://localhost:3000, talks to API_URL (default http://localhost:3001)
```

The backend reads the repo-root `.env` (DATABASE_*, JWT_SECRET, AZURE_*, TOKEN_ENCRYPTION_KEY,
OPENAI_API_KEY, PINECONE_*, REDIS_URL, …) unchanged. `uvloop` is Linux-only (skipped on Windows).

## Database / Alembic

The schema already exists (`infrastructure/postgres/migrations/*.sql`); ORM models mirror it.
Baseline before using autogenerate:
```bash
cd backend
alembic revision --autogenerate -m "baseline existing schema"   # REVIEW the diff carefully
alembic stamp head
```
Never let Alembic drop/recreate existing tables.

## Legacy cleanup (do after validating the FastAPI backend)

Some old folders are still locked by the running IDE / dev servers and could not be moved.
Once you close them, from the repo root:
```powershell
Remove-Item -Recurse -Force apps, packages, node_modules, _legacy-nestjs
```
`apps/web` is superseded by `frontend/`; `apps/api` + `apps/worker` by `backend/`;
`packages/*` were ported into `backend/app`. Keep `infrastructure/`.
