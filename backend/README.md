# Starbot — Enterprise AI Assistant

Production-ready RAG assistant for Outlook, SharePoint, OneDrive, and workspace knowledge for AppXcess Technologies (chat is scoped to M365 + company topics; internet search is off by default).

## Architecture

See [ARCHITECTURE.md](./ARCHITECTURE.md) for full design decisions: PostgreSQL schema, Pinecone namespaces, LangGraph workflow, API contracts, auth, retrieval, security, and deployment.

## Monorepo Structure

```
apps/
  web/          Next.js 15 + Microsoft OAuth + ShadCN UI
  api/          NestJS REST API + JWT sessions (pg)
  worker/       BullMQ ingestion jobs
  ai-service/   FastAPI + Sentence Transformers + LangGraph
packages/
  types, config, prompts, utils, ui
infrastructure/
  postgres/migrations, docker, kubernetes, scripts
```

## Quick Start

### Prerequisites

- **Node 20.x** (LTS; do not use bare `npx pnpm` — it installs pnpm 10 which needs Node 22.13+)
- **pnpm 9.15** via `npx pnpm@9.15.0` or Corepack: `corepack enable && corepack prepare pnpm@9.15.0 --activate`
- Python 3.11+ (ai-service)
- PostgreSQL 15+ (local database `starbot`)
- Pinecone index (384 dimensions, cosine)
- Redis
- OpenAI API key
- Azure app registration (Microsoft Graph + OAuth redirect to API)

### 1. Environment

```bash
cp .env.example .env
# Fill DATABASE_*, JWT_SECRET, Pinecone, OpenAI, Azure, TOKEN_ENCRYPTION_KEY (openssl rand -hex 32)
```

Copy the same `.env` to `apps/api/.env`. Set `NEXT_PUBLIC_API_URL=http://localhost:3001` in `apps/web/.env.local`.

### 2. Database

**Option A — Node (no `psql` required)** — set `DATABASE_*` in `.env` first, then:

```bash
pnpm db:migrate
```

**Option B — Windows + psql** (if PostgreSQL bin is on PATH):

```powershell
.\infrastructure\postgres\scripts\setup-windows.ps1
```

**Option C — manual psql:**

```bash
psql -U postgres -f infrastructure/postgres/scripts/create-database.sql
psql -U postgres -d starbot -f infrastructure/postgres/migrations/001_auth_core.sql
psql -U postgres -d starbot -f infrastructure/postgres/migrations/002_enterprise_schema.sql
```

After first Microsoft login, optionally run `infrastructure/scripts/seed-org.sql` with `users.id` (the API auto-creates a personal organization on first login).

### 3. Install & run

```bash
# Always pin pnpm 9 on Node 20 (never: npx pnpm — pulls pnpm 10)
npx pnpm@9.15.0 install

# Or after install (use .cmd if PowerShell blocks .ps1 scripts):
run-dev.cmd

pnpm --filter @starbot/types build
pnpm --filter @starbot/config build
pnpm --filter @starbot/utils build
pnpm --filter @starbot/prompts build

# Terminal 1 — AI embeddings (port 8001; Windows blocks 8000 on many machines)
cd apps/ai-service
run.cmd
# Or: pip install -r requirements.txt && uvicorn app.main:app --reload --host 127.0.0.1 --port 8001

# Terminal 2 — API + Web (one command)
run-dev.cmd
# API + Web + scheduled M365 sync (Redis + worker):
run-dev-full.cmd

# Or: npx pnpm@9.15.0 dev
# Or: node_modules\.bin\turbo run dev --parallel

# Worker only (if API/Web already running)
pnpm --filter @starbot/worker dev
```

### 4. Microsoft sign-in (users)

1. In **Azure Portal**, create a multi-tenant Entra app with delegated permissions: `Mail.Read`, `Mail.ReadWrite`, `Files.Read.All`, `Sites.Read.All`, `offline_access`, `openid`, `profile`, `email`.
2. Add redirect URI: `http://localhost:3000/api/v1/auth/microsoft/callback`
3. Set `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, `JWT_SECRET`, and `DATABASE_*` in `.env` (copy to `apps/api/.env`).
4. Sign in at http://localhost:3000/login with **Continue with Microsoft**. The API exchanges the code, stores Graph tokens, and sets an HttpOnly session cookie.

Enterprise tenants may require an admin to grant consent for mail/file scopes.

### 5. Index content

```bash
POST /api/v1/ingestion/outlook/sync
POST /api/v1/ingestion/documents/sync
```

## Troubleshooting

### Dashboard shows "Setup required"

This means the web app could not load or create a chat session. Common causes:

1. **API not running** — Check http://localhost:3001/api/v1/health. Start the API: `pnpm --filter @starbot/api dev` (from repo root; use `npx pnpm@9.15.0` if pnpm is not on PATH).
2. **Wrong API URL** — Set `NEXT_PUBLIC_API_URL=http://localhost:3001` in `apps/web/.env.local`.
3. **API env missing** — Copy root `.env` to `apps/api/.env` so NestJS loads database and JWT keys.
4. **Database not migrated** — Run `infrastructure/postgres/migrations/*.sql` (see setup script above).

On first login, the API provisions a personal organization automatically. You can still use `infrastructure/scripts/seed-org.sql` to join the shared `acme` org.

### Graph / Microsoft errors after login

- **`error=oauth`** — Token exchange or Graph profile fetch failed. Check Entra redirect URI, `AZURE_*` env vars, and API logs.
- **Settings shows Not connected** — Sign out and sign in again; confirm admin consent in the tenant if applicable.

### Redis / chat errors (`Stream isn't writeable`)

Redis is **optional** for local dev. Login and chat work without it; the API skips RAG response caching and uses in-memory OAuth state when Redis is down.

To enable Redis (faster repeat RAG queries, shared OAuth state):

```bash
docker compose -f infrastructure/docker/docker-compose.yml up -d redis
```

Ensure `REDIS_URL=redis://localhost:6379` in `.env` and `apps/api/.env`.

### Pinecone shows requests but no vectors

Vectors are stored in a **per-organization namespace** (your `organizationId` UUID), not the default namespace.

1. **Settings** page (`/settings`) shows index name, namespace id, and vector count via `GET /api/v1/ingestion/status`.
2. Confirm `PINECONE_INDEX_NAME` in `apps/api/.env` matches the index in the [Pinecone console](https://app.pinecone.io).
3. Index must use **384 dimensions** (cosine) for `all-MiniLM-L6-v2`.
4. Start **ai-service** (`apps/ai-service`, port 8001) before sync — embeddings are required for upsert.
5. Run sync: open the dashboard (auto once per session), use **Settings** sync buttons, or enable **scheduled sync** — set `WORKER_SERVICE_TOKEN` in `.env` / `apps/api/.env`, start Redis, then `run-dev-full.cmd` (or `pnpm --filter @starbot/worker dev`). Interval: `INGESTION_SYNC_INTERVAL_MS` (default 1h in `.env.example`). Expect `{ indexed, vectorsUpserted }` **> 0** for each source you use.
6. In Pinecone: open your index → **Namespaces** → select your org UUID (from Settings), not `__default__`. Filter metadata by `source: sharepoint` or `source: onedrive` for document vectors.

If sync logs show delete errors, restart the API so the latest Pinecone delete handling is loaded (only one process on port 3001).

### SharePoint sync logs `column "drive_id" of relation "file_metadata" does not exist`

The document indexer needs migration `003`. On an **existing** database (after `001`/`002` already ran), apply only:

```bash
node infrastructure/postgres/scripts/run-003.mjs
```

Then restart the API and run document sync from Settings or `POST /api/v1/ingestion/documents/sync`.

### SharePoint / OneDrive sync returns 403 or 0 files

- **Admin consent** — `Sites.Read.All` often requires a tenant admin to grant consent in Entra ID → Enterprise applications → your app → Permissions.
- **Re-sign in** — Sign out and sign in again so delegated tokens include file/site scopes.
- **Sync caps** — Tune `SHAREPOINT_MAX_SITES`, `SHAREPOINT_MAX_FILES_PER_SYNC`, and `DOCUMENT_MAX_BYTES` in `.env` (see `.env.example`).
- **Supported types** — Phase 1 extracts `.txt`, `.md`, `.csv`, `.json`, `.docx`, and `.pdf`; other types are indexed with filename/URL metadata only.

## API Overview

| Endpoint | Feature |
|----------|---------|
| `GET /auth/microsoft/login` | Start Microsoft OAuth |
| `GET /auth/me` | Current user profile |
| `GET /auth/session` | Session + org context |
| `POST /auth/logout` | Clear session cookie |
| `POST /graph/sync` | Re-sync Microsoft tokens (optional) |
| `GET /graph/status` | Whether Graph mail access works |
| `GET /ingestion/status` | Pinecone namespace, vector counts, sync health |
| `POST /ingestion/outlook/sync` | Index Outlook mail into Pinecone |
| `POST /chat/sessions/:id/messages` | RAG chat with citations |
| `POST /search/unified` | Cross-platform search |
| `POST /search/internet` | Disabled by default (`ENABLE_INTERNET_SEARCH=false`); requires Tavily when enabled |
| `POST /mail/classify` | Smart Mail Agent |
| `GET /workspaces` | Sales / Operations / Restoration |
| `POST /documents/generate` | Template-based docs |

## Advancements (roadmap implemented)

- **Scheduled ingestion** — BullMQ worker (`starbot-scheduler` + `starbot-ingestion`) syncs mail/documents every 6h when `WORKER_SERVICE_TOKEN` and Redis are set.
- **OneDrive delta sync** — `ENABLE_DELTA_SYNC=true` uses Graph delta cursors (`sync_cursors` table, migration `004`).
- **Document types** — `.xlsx` and `.pptx` text extraction; metadata-only files shown in Settings.
- **UI** — `/workspaces`, `/documents`, `/search`, Settings sync panel + audit log viewer.
- **RAG** — Hybrid filename search, ai-service intent classifier, streaming chat (SSE), workspace instructions in prompts.
- **Teams** — `POST /teams/sync` placeholder until channel scopes are granted.
- **CI** — `.github/workflows/ci.yml` runs typecheck + topic-guard tests.

## Business research (Chat + Search)

For industry averages, vendor/company research, and pricing references, Starbot routes queries to **Tavily** when `TAVILY_API_KEY` is set. See [docs/BUSINESS_RESEARCH_SEARCH.md](./docs/BUSINESS_RESEARCH_SEARCH.md).

```env
ENABLE_BUSINESS_RESEARCH=true
TAVILY_API_KEY=your_key
```

Personal trivia (weather, sports, etc.) remains blocked.

## Chat scope (AppXcess + Microsoft 365)

Dashboard chat uses **indexed Outlook / SharePoint / OneDrive data only** — not the public internet. Off-topic questions (weather, general trivia, etc.) receive a short refusal without calling the LLM.

Configure in `apps/api/.env`:

```env
APPXCESS_TOPIC_ALIASES=AppXcess,AppXcess Technologies,Starbot
ENABLE_INTERNET_SEARCH=false
```

`POST /api/v1/search/internet` returns 403 unless `ENABLE_INTERNET_SEARCH=true` and `TAVILY_API_KEY` are set.

## Pinecone Setup

Create serverless index:

- **Name:** `starbot-dev`
- **Dimensions:** 384
- **Metric:** cosine

Namespace per organization: `{organizationId}` with metadata `source`: `outlook`, `sharepoint`, `onedrive`, `sales-workspace`, etc.

## Phase Checklist

- [x] Phase 1: Monorepo + Microsoft OAuth + local PostgreSQL
- [x] Phase 2: Microsoft Graph OAuth
- [x] Phase 3: Email & document ingestion
- [x] Phase 4: Sentence Transformer embeddings (ai-service)
- [x] Phase 5: Pinecone vector storage
- [x] Phase 6: RAG + GPT-4o-mini + citations
- [x] Phase 7: Unified + internet search
- [x] Phase 8: Smart Mail Agent
- [x] Phase 9: Persistent workspaces
- [x] Phase 10: Template document generation
- [x] Phase 11: Docker, K8s, audit, throttling, Redis cache

## License

Proprietary — AppXcess Intern project.
