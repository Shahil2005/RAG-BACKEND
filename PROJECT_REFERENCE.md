# Starbot (RAG-OUTLOOK) — Complete Project Reference

> Internal reference document for the Starbot codebase. Captures architecture, every app/service, all features, the data model, configuration, and operational details. Generated from a full read of the source.

---

## 1. What This Project Is

**Starbot** is a production-oriented, multi-tenant **Enterprise RAG (Retrieval-Augmented Generation) assistant** built for **AppXcess Technologies**. It unifies a user's **Microsoft 365** data — Outlook mail, SharePoint documents, OneDrive files — plus optional **business web research** (via Tavily) into a single chat/search experience grounded with citations.

Core principles baked into the design:
- **Chat is scoped** to Microsoft 365 + company topics. Off-topic queries (weather, sports, trivia, general code generation) are refused *before* calling the LLM.
- **Internet search is off by default**; business research (industry benchmarks, vendor/company lookup, pricing) is opt-in via `TAVILY_API_KEY`.
- **Multi-tenancy** by `organizationId` on every row and as the Pinecone namespace.
- **Permission-aware retrieval**: only content the user actually owns/synced is returned.
- **App-layer security** (no Postgres RLS): NestJS guards + parameterized SQL scoped by org/user.

License: **Proprietary — AppXcess Intern project**.

---

## 2. Monorepo Layout

pnpm workspace + Turborepo. Root `package.json` pins `pnpm@9.15.0`, Node `>=20.0.0 <22.13.0`.

```
RAG-OUTLOOK/
├── apps/
│   ├── web/         Next.js 15 App Router frontend (Microsoft OAuth, ShadCN UI)
│   ├── api/         NestJS 10 REST API (JWT sessions, pg raw SQL) — the core backend
│   ├── worker/      BullMQ scheduled ingestion worker
│   └── ai-service/  FastAPI (Python) — Sentence-Transformers embeddings + intent + rerank
├── packages/
│   ├── types/       Shared TypeScript types (@starbot/types)
│   ├── config/      Constants: embedding dims, top-K, model, Graph scopes, vectorId() (@starbot/config)
│   ├── prompts/     LLM system/user prompt builders (@starbot/prompts)
│   └── utils/       chunkText, cleanEmailBody, reciprocalRankFusion (@starbot/utils)
├── infrastructure/
│   ├── postgres/    migrations/ (001–005), scripts/ (migrate.mjs, setup-windows.ps1, etc.)
│   ├── docker/      Dockerfile.api + docker-compose.yml (redis, ai-service, api, worker, web)
│   ├── kubernetes/  api-deployment.yaml
│   ├── terraform/   main.tf
│   ├── supabase/    legacy migrations (001 initial, 002 projects) — historical
│   └── scripts/     seed-org.sql
├── docs/            BUSINESS_RESEARCH_SEARCH.md
├── .github/workflows/ci.yml   typecheck + topic-guard tests
├── ARCHITECTURE.md  Design decisions
├── README.md        Setup + troubleshooting
└── run-dev*.cmd/ps1, start-api.cmd  Local dev launchers
```

**Workspace builds (order matters):** `types` → `config` → `utils` → `prompts` are built first; `api` and `web` depend on them.

---

## 3. Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | Next.js 15.1.3, React 19, TypeScript 5.7, Tailwind 3.4, Radix UI / ShadCN, class-variance-authority |
| Backend API | NestJS 10, Express, `pg` (raw SQL, Pool), class-validator/transformer, @nestjs/throttler, @nestjs/jwt |
| Worker | BullMQ 5 + ioredis |
| AI service | Python, FastAPI 0.115, Uvicorn, sentence-transformers 3.3.1 (`all-MiniLM-L6-v2`), numpy<2, pydantic 2 |
| LLM | OpenAI SDK 4.x — model constant `LLM_MODEL = 'gpt-5.4-mini'` (in `packages/config`) |
| Vector DB | Pinecone serverless (`@pinecone-database/pinecone` 4) — 384-dim, cosine |
| Database | PostgreSQL 15+ (local DB `starbot`) |
| Cache/Queue | Redis (optional for dev) |
| Identity | Microsoft Entra (Azure AD) OAuth 2.0 + Microsoft Graph |
| Doc parsing | mammoth (.docx), pdf-parse (.pdf), xlsx (.xlsx/.xls), jszip (.pptx), native (.txt/.md/.csv/.json/.xml/.html) |
| External research | Tavily Search API (optional) |
| Encryption | AES-256-GCM (Node crypto) for Graph tokens |

---

## 4. System Architecture

```
┌─────────────┐     ┌─────────────┐     ┌──────────────┐
│  Next.js    │────▶│  NestJS API │────▶│ Redis Cache  │ (optional)
│  (web:3000) │     │  (api:3001) │     └──────────────┘
└──────┬──────┘     └──────┬──────┘
       │ JWT cookie        ├──▶ PostgreSQL (pg Pool, app-layer auth)
       │                   ├──▶ Pinecone (vectors only, per-org namespace)
       │                   ├──▶ Microsoft Graph (mail/files)
       │                   ├──▶ ai-service (embeddings, intent, rerank)
       │                   └──▶ OpenAI (synthesis) / Tavily (research)
┌──────▼──────┐     ┌─────────────┐
│  Worker     │────▶│ ai-service  │
│  (BullMQ)   │     │(FastAPI:8001)│
└─────────────┘     └─────────────┘
```

**Key design decisions:**
- **Embeddings live only in Pinecone**; Postgres holds searchable metadata, permissions, audit. Avoids PG vector scale limits; one index, per-org namespace.
- **Four apps** decouple long Graph/Pinecone jobs (worker) and CPU-heavy embeddings (Python ai-service) from the request path.
- **Redis is optional in dev** — login & chat work without it; the API falls back to in-memory OAuth state and skips RAG response caching.

---

## 5. The API (apps/api) — NestJS Backend

Bootstrap (`main.ts`): global prefix `api/v1`, cookie-parser, CORS (credentials, `CORS_ORIGIN`), global `ValidationPipe` (whitelist + transform + forbidNonWhitelisted). Listens on `API_PORT` (default 3001). `ThrottlerModule` global rate limit: **120 req / 60 s**. `TraceMiddleware` applied to all routes (OpenTelemetry-style trace ids).

### Modules registered in `AppModule`
Database, Redis, Auth, Graph, Pinecone, Ingestion, Rag, Search, Chat, Mail, Workspaces, Projects, Documents, Audit, Health, Teams. (Query orchestration lives inside Rag/Search wiring.)

### 5.1 Auth Module — Microsoft OAuth + JWT sessions

**Flow** (`auth.controller.ts`, `auth.service.ts`, `oauth.service.ts`):
1. `GET /auth/microsoft/login` (or `/authorize-url?popup=1`) → builds Entra authorize URL with `GRAPH_OAUTH_SCOPE`, `prompt=select_account`, random 32-byte `state` stored in Redis (TTL 600 s) with **in-memory fallback**.
2. User authenticates at Microsoft → `GET /auth/microsoft/callback?code&state`.
3. `completeMicrosoftLogin`: validate state → exchange code for tokens → fetch Graph `/me` profile → upsert `users` → **encrypt + store** access/refresh tokens in `oauth_tokens` (AES-256-GCM) → revoke prior sessions → create JWT session → `ensureOrganization` (auto-provision personal org as `owner` on first login) → **kick off background Outlook + documents sync** → set HttpOnly `starbot_session` cookie (sameSite lax, `secure` if `COOKIE_SECURE=true`) → redirect to `/dashboard` (or popup complete page).
4. Error mapping via `azure-env.ts` (`mapOAuthFailureReason`, detects Secret-ID-instead-of-Value misconfig).

**Endpoints:** `GET /auth/microsoft/authorize-url`, `GET /auth/microsoft/login`, `GET /auth/microsoft/callback`, `POST /auth/logout` (JWT), `GET /auth/me`, `GET /auth/session` (JWT), `GET /auth/bootstrap` (JWT).

**Supporting services:** `JwtSessionService` (create/verify/revoke JWT, sessions table), `OAuthService` (state, code exchange, profile, token expiry decode), `UserService` (`upsertFromGraph`, `findById`), `EncryptionService` (AES-256-GCM; key = `TOKEN_ENCRYPTION_KEY`, 64 hex chars).

**Guards & decorators:**
- `JwtAuthGuard` — verifies session cookie/Bearer, loads `AuthContext` (userId, organizationId, role) onto `request.user`.
- `JwtOrWorkerAuthGuard` — also accepts `Bearer <WORKER_SERVICE_TOKEN>` + `x-user-id`/`x-organization-id`/`x-role` headers (used by the worker for service-to-service ingestion). Protects ingestion routes.
- `RolesGuard` + `@Roles(...)` decorator — RBAC (owner > admin > member > viewer).
- `@CurrentUser()` — injects `AuthContext`.

### 5.2 Graph Module — Microsoft Graph client (`graph.service.ts`)

- **Token lifecycle:** `getAccessToken` decrypts stored token, refreshes via Entra token endpoint when within 5-min skew, re-encrypts. `syncFromProviderTokens` for external token import.
- **Mail:** `fetchRecentEmails(top)` (`/me/messages`, ordered by receivedDateTime, body cleaned via `cleanEmailBody`), `searchEmails(query)` (Graph `$search`).
- **Files:** `listSharePointSites` (search `*`, paginated), `listSiteDrives`, `listDriveChildren`, `walkDriveFiles` (BFS crawl with depth/file caps), `downloadDriveItem` (uses `@microsoft.graph.downloadUrl`, enforces max bytes), `collectSharePointFiles`, `collectOneDriveFiles`, **`collectOneDriveFilesDelta`** (incremental via Graph `delta` cursors stored in `sync_cursors`), `fetchRecentDriveItems` (`/me/drive/recent`), `searchDriveItems` (Graph `/search/query`).
- **Crawl limits** (`getCrawlLimits`): `SHAREPOINT_MAX_SITES` (25), `SHAREPOINT_MAX_FILES_PER_SYNC` (200), `DOCUMENT_SYNC_RECURSION_DEPTH` (5), `DOCUMENT_MAX_BYTES` (5 MiB).
- **Controller:** `POST /graph/sync` (re-sync tokens, JWT), `GET /graph/status` (whether Graph mail works, JWT).

**Graph delegated scopes** (`packages/config`): `openid, profile, email, offline_access, User.Read, Mail.Read, Mail.ReadWrite, Files.Read.All, Sites.Read.All`.

### 5.3 Ingestion Module — indexing pipeline (`ingestion.service.ts`)

- **`syncOutlook`**: fetch up to 100 recent emails → upsert `email_metadata` → chunk `subject + body` (subject-only fallback if body empty) → delete stale vectors by stable ID → embed (ai-service) → upsert to Pinecone with `source: outlook` metadata → mark `is_indexed`. Audited as `ingestion.outlook.sync`.
- **`syncAllDocuments`**: guards on schema (`hasFileMetadataDriveColumns`, needs migration 003) → `syncDocumentSource('sharepoint')` + `syncDocumentSource('onedrive')`. OneDrive uses **delta sync** unless `ENABLE_DELTA_SYNC=false`.
- **`indexDriveFile`**: upsert `file_metadata` → skip if over max bytes (metadata-only fallback) → download → `DocumentTextService.extractFromBuffer` → chunk → delete stale → embed → upsert → record `index_reason` (`ok`, `metadata_fallback:*`, etc.).
- **`getStatus`**: returns `IngestionStatus` — Pinecone index/namespace/vector count, email & file counts, last sync timestamps + last errors (from audit logs), metadata-only file count, ai-service reachability, scheduled-sync enabled/interval/next-estimate.
- **`listWorkerSyncTargets`**: all org members with `oauth_tokens` (for the worker).
- **`createJob`**: inserts `ingestion_jobs` row.

**Controllers:**
- `ingestion.controller.ts` (`JwtOrWorkerAuthGuard`): `GET /ingestion/status`, `POST /ingestion/outlook/sync`, `POST /ingestion/documents/sync`.
- `ingestion-worker.controller.ts`: `GET /ingestion/worker/targets` (worker-token authed).

**Helpers:** `DocumentTextService` (extraction — see §5.11), `SyncCursorService` (delta link get/set in `sync_cursors`).

### 5.4 RAG Module — retrieval + grounded answers (`rag.service.ts`)

The heart of grounded chat. `query(ctx, query, options)`:
1. **Topic guard** (unless `bypassTopicGuard`): `assertInScopeQuery` → off-topic returns `OUT_OF_SCOPE_REFUSAL_MESSAGE`, no LLM call.
2. **Recency detection**: `isMailRecencyQuery` → live Outlook; `isDocumentRecencyQuery` → live docs. Otherwise calls ai-service **intent classifier** for `suggestedSources`.
3. **Source selection** — default `[outlook, sharepoint, onedrive]`; narrowed for recency; `project` added when `projectId` present.
4. **Redis cache** keyed by org + (live prefix) + base64 query; live-context queries are not cached.
5. **Hybrid retrieval**: `HybridRetrievalService.findKeywordChunks` (ILIKE on `file_metadata.file_name` / `email_metadata.subject` / `project_files.file_name` for exact-name & quoted/filename queries) **merged** with Pinecone vector results.
6. **Pinecone retrieval** (`retrieveFromPinecone`): embed query → per-source metadata-filtered query (topK=20 default) → **permission filter** → **Reciprocal Rank Fusion** across source lists → **rerank** (ai-service, top-5) → `ensureDocumentChunksInResult` guard (keeps doc chunks from being hidden by Outlook-heavy rerank).
7. **Live fallbacks**: if no Outlook/doc chunks but source requested and Graph connected, build live context from Graph directly and schedule background sync.
8. **Instructions**: project instructions (`ProjectsService.getInstructionsForRag`) or active workspace instructions injected into the prompt.
9. **Generation**: OpenAI `LLM_MODEL`, temp 0.2, `RAG_SYSTEM_PROMPT` + `buildRagUserPrompt`. Empty-context returns a typed message (`not_connected` / `no_indexed_data` / empty mailbox / empty docs).
10. **Citations**: `[n]` mapped to `{source, title, snippet, url, timestamp}`. Response cached 120 s (non-live).

**Supporting RAG services:**
- `PermissionService` — `getAllowedContentIds` (email/file/project IDs the user owns) + `filterChunks` (workspace chunks always allowed; outlook by emailId; files by fileId or vector-id contentId or legacy no-id; project by fileId).
- `OutlookMailService` — live mail context, `upsertEmailMetadata`, `isGraphConnected`, `countEmailMetadata`.
- `SharePointDocumentsService` — live document context (search + recent), `upsertFileMetadata`, `countFileMetadata`.
- `HybridRetrievalService` — keyword/exact-name matching.
- `topic-guard.util.ts` — re-exports the query-intent classifiers.

### 5.5 Query Module — orchestration & intent (`query-orchestration.service.ts`)

`QueryOrchestrationService.query` routes by intent:
- **`off_topic`** → refusal, no LLM.
- **`m365_only`** → `RagService.query` (bypass topic guard).
- **`business_research`** → `runBusinessResearch` (Tavily only).
- **`hybrid`** → internal RAG **+** Tavily, synthesized together with `BUSINESS_RESEARCH_SYSTEM_PROMPT` + `buildHybridSynthesisUserPrompt` (internal cites `[n]`, external cites `[EXT-n]`).
- `forceExternal` option forces business_research (used by `/search/internet`).
- `unifiedSearch` wraps `query` into `UnifiedSearchResponse`.

**Intent classifier** (`query-intent.util.ts`, pure regex, unit-tested):
- `OFF_TOPIC_PATTERNS` — weather, sports, recipes, news, movies, crypto, generic code-gen.
- `M365_KEYWORD_PATTERNS` — outlook/email/sharepoint/onedrive/document/workspace/m365/classify, plus recency/content detectors.
- `BUSINESS_RESEARCH_PATTERNS` — industry average, benchmark, market rates/size, vendor/supplier, pricing, quote, RFP, competitor, "vs", compare-with, etc.
- `classifyQueryIntent` → `off_topic | m365_only | business_research | hybrid`. AppXcess aliases (`APPXCESS_TOPIC_ALIASES`) count as in-scope; bare company name + external-comparison routes to research.

### 5.6 Search Module (`search.service.ts`, `search.controller.ts`)
- `POST /search/unified` (JWT) — cross-platform search via orchestrator; audited `search.unified`.
- `POST /search/internet` (JWT) — `forceExternal` research; audited `search.internet`. (Legacy `ENABLE_INTERNET_SEARCH` gate; business research is the modern path.)
- `TavilyResearchService` — `isEnabled()` (needs `TAVILY_API_KEY` + `ENABLE_BUSINESS_RESEARCH!=false`), `searchBusinessWeb` (appends "commercial business pricing benchmark industry" to query, basic depth, 5 results → `ExternalResearchChunk[]`).

### 5.7 Chat Module (`chat.service.ts`, `chat.controller.ts`)
Session-based conversations, optionally bound to a workspace or project.
- `POST /chat/sessions` — create (title, workspaceId?, projectId?; project ownership asserted).
- `GET /chat/sessions[?projectId]` — list (project-scoped or general only).
- `GET /chat/sessions/:id/messages` — history.
- `POST /chat/sessions/:id/messages` — send → persists user msg, auto-titles session, **auto-triggers background sync if no mail/files indexed**, runs orchestrator, persists assistant msg + citations.
- `POST /chat/sessions/:id/messages/stream` — same but **SSE streaming**: emits `{type:'token'}` word-by-word (8 ms cadence — simulated streaming), then `{type:'done'}` with full answer/citations/intent/emptyReason/scopeReason.
- All routes assert session ownership (org + user).

### 5.8 Mail Module — Smart Mail Agent (`mail-agent.service.ts`)
- `POST /mail/classify` (JWT) — fetch 30 recent emails → LLM classify each into `important | spam | closed | pending_action` with confidence + reasoning (`MAIL_CLASSIFIER_PROMPT`, JSON response) → upsert `email_metadata` + `email_classifications`.
- `GET /mail/classifications` (JWT) — joined classification rows.
- `POST /mail/bulk-dismiss` (JWT) — count spam-classified (placeholder dismissal), audited.

### 5.9 Workspaces Module (`workspaces.service.ts`)
Persistent department context. `ensureDefaults` seeds **Sales / Operations / Restoration** workspaces per org (Pinecone partition `{slug}-workspace`).
- `GET /workspaces` (JWT) — list with active instructions.
- `PUT /workspaces/:id/instructions` (JWT) — **versioned** instruction updates (deactivates prior, inserts new version, active=true). Injected as system context in RAG.

### 5.10 Projects Module (`projects.service.ts`, `project-files.service.ts`)
ChatGPT-"Projects"-style knowledge containers (per user+org).
- `GET /projects`, `POST /projects`, `GET /projects/:id`, `PATCH /projects/:id`, `DELETE /projects/:id` — CRUD with `name/description/customInstructions`; all ownership-checked; audited.
- `GET /projects/:id/files`, `POST /projects/:id/files` (multipart upload interceptor), `DELETE /projects/:id/files/:fileId`.
- **File upload + index**: saved under `PROJECT_UPLOAD_DIR/{org}/{project}/{uuid}-name`, extracted, chunked, embedded, upserted to Pinecone with `source: project` + `projectId`. Tracks `chunk_count`, `is_indexed`, `index_reason`. Deletion removes vectors + file from disk.
- Project chat injects `description + custom_instructions` and adds `project` to retrieval sources.

### 5.11 Document Text Service (`document-text.service.ts`)
Extraction engine used by all ingestion paths:
- **Plain text**: `.txt .md .csv .json .xml .html .htm .log` or `text/*`.
- **`.docx`** → mammoth; **`.pdf`** → pdf-parse (optional OCR hook, not implemented); **`.xlsx/.xls`** → xlsx → CSV per sheet; **`.pptx`** → jszip + regex over slide XML `<a:t>`.
- **Skip list**: images, audio/video, archives, executables → metadata-only fallback.
- `resolveChunks` produces `metadata_fallback:*` chunks (filename + URL) when extraction fails/empty, so the file is still discoverable by name.

### 5.12 Teams Module (`teams-ingestion.service.ts`)
- `POST /teams/sync` (JWT) — **placeholder** returning guidance text. Requires `ChannelMessage.Read.All` admin consent + `ENABLE_TEAMS_INGESTION=true`. Audited `ingestion.teams.sync`.

### 5.13 Pinecone Module (`pinecone.service.ts`)
- On init: connects, validates index dimension vs `EMBEDDING_DIMENSIONS` (384), logs namespaces.
- Namespace = `organizationId`. Vector ID = `{source}:{contentId}:{chunkId}` (`vectorId()`).
- `upsert` (batches of 100), `query` (vector + metadata filter, includes metadata), `buildUpsert`, `deleteByVectorIds` (tolerates 404/400 on serverless), `getNamespaceVectorCount`, `deleteByEmailId` is a no-op (serverless rejects metadata-filter deletes).

### 5.14 Cross-cutting (common/)
- `DatabaseService` — `pg` Pool wrapper: `query`, `queryOne`, `hasFileMetadataDriveColumns` (schema guard).
- `RedisService` — ioredis client, `getCached`/`setCached`, graceful when Redis down.
- `AuditService` — `logAudit(ctx, action, resourceType, resourceId?, metadata?)` → `audit_logs`.
- `AppLogger` / `logAuth` — structured JSON logging.
- `TraceMiddleware` — request trace ids.
- `Audit Module` — `GET /audit/logs` (JWT + **RolesGuard**, admin/owner).
- `Health Module` — `GET /health`.

---

## 6. The AI Service (apps/ai-service) — FastAPI / Python

Loads `all-MiniLM-L6-v2` (384-dim) at startup. Endpoints:
- `GET /health` — liveness.
- `POST /embed` `{texts[]}` → `{embeddings[][], model, dimensions}` (normalized embeddings).
- `POST /agent/classify` `{query}` → `{intent, suggestedSources}` — regex router (`mail | documents | workspace | general`) suggesting Pinecone sources.
- `POST /rerank` `{query, chunks[], topK}` → token-overlap re-ranking (`base*0.6 + overlap*0.4`); production note: swap for cross-encoder.
- Request-logging middleware (skips `/health`).

Runs on **port 8001** (Windows often reserves 8000). Launch via `run.cmd`/`run.ps1` or `uvicorn app.main:app`.

---

## 7. The Worker (apps/worker) — BullMQ Scheduler

- Two queues: **`starbot-scheduler`** (repeating tick every `INGESTION_SYNC_INTERVAL_MS`, default 6 h) and **`starbot-ingestion`** (per-user sync jobs).
- On tick: `fetchSyncTargets` (`GET /ingestion/worker/targets` with `WORKER_SERVICE_TOKEN`) → enqueue outlook + sharepoint jobs per user.
- Worker processes each job by POSTing to `/ingestion/outlook/sync` or `/ingestion/documents/sync` with worker-token + `x-user-id`/`x-organization-id` headers.
- **No-op if `WORKER_SERVICE_TOKEN` is unset.** Requires Redis.

---

## 8. The Web App (apps/web) — Next.js 15 Frontend

Middleware (`middleware.ts`) gates routes by the `starbot_session` cookie; public: `/`, `/login`, `/auth/microsoft/*`, `/api/v1/*`. Authenticated users hitting `/login` redirect to `/dashboard`.

### Pages (`src/app/`)
| Route | Type | Purpose |
|-------|------|---------|
| `/` | redirect | → `/login` |
| `/login` | client | Microsoft sign-in form; health-checks API; friendly OAuth error messages |
| `/login/microsoft` | client | Popup OAuth entry (fetches authorize-url) |
| `/auth/microsoft/complete` | client | Popup completion — postMessage to opener / redirect to dashboard |
| `/dashboard` | server | Protected; main chat shell (projects + sessions + chat) |
| `/documents` | client | Template-based document generation UI |
| `/mail` | client | Smart Mail Agent — classify inbox, grouped results |
| `/search` | client | Unified search with source toggles + external research display |
| `/settings` | server | Microsoft 365 status, Pinecone index stats, sync panel, audit log |
| `/workspaces` | client | List + edit versioned workspace instructions |

### API proxy routes (`src/app/api/v1/`)
- `auth/microsoft/authorize-url`, `auth/microsoft/login`, `auth/microsoft/callback` (forwards Set-Cookie onto web origin), `health`. All proxy to NestJS (`API_URL`).

### Key components
- **Chat**: `dashboard-chat-shell` (orchestrates projects+sessions+panel, URL params `?project&session`), `chat-panel` (SSE streaming reader, history), `chat-sidebar`, `chat-citations` (toggle sources, EXT- prefix), `chat-message-content` (markdown→React).
- **Projects**: `projects-sidebar`, `project-settings-modal`, `project-settings-panel` (edit fields, upload files `.txt/.md/.pdf/.docx/.xlsx/.pptx/.csv/.json`, list/delete, delete project).
- **Settings**: `ingestion-sync-panel` (manual Outlook + Docs sync, errors, metadata-only warning), `documents-sync-button`, `audit-log-panel`.
- **Mail**: `mail-classification-list` (color-coded category cards, confidence, reasoning).
- **Layout/Auth**: `app-nav` (Chat/Search/Workspaces/Documents/Mail/Settings), `sign-out-button`, `dashboard-sync` (invisible, fires background indexing once per session).

### Lib
`api.ts` (`apiFetch`/`apiUpload`, credentials include), `api-server.ts` (`serverApiFetch` with forwarded cookies, no-store, 15 s timeout), `auth.ts` (`getCurrentUser` via `/auth/me`), `format-markdown.tsx`, `format-sync-interval.ts`, `setup-errors.ts` (maps API errors to fix-it guidance), `logger.ts`, `middleware.ts`.

---

## 9. Data Model (PostgreSQL)

Migrations in `infrastructure/postgres/migrations/` (run with `pnpm db:migrate` → `migrate.mjs`). **No RLS** — security enforced in the API layer.

### 001_auth_core
- **users** (microsoft_id unique, email, name, avatar)
- **oauth_tokens** (user_id unique, encrypted access/refresh, expires_at) — AES-256-GCM ciphertext
- **sessions** (user_id, jwt_token, expires_at)
- **organizations** (name) — extended in 002
- **workspaces** (org_id, name) — extended in 002

### 002_enterprise_schema
- **organizations** += slug (unique), timestamps
- **organization_members** (org_id, user_id, **role** `member_role` enum: owner/admin/member/viewer, unique pair)
- **workspaces** += slug, description, pinecone_partition, timestamps (unique org+slug)
- **workspace_instructions** (workspace_id, instructions, **version**, is_active)
- **workspace_members**, **workspace_documents** (file_name, storage_path, mime, size, indexed_at)
- **chat_sessions** (org, workspace_id?, user, title, timestamps)
- **chat_messages** (session_id, role check user/assistant/system, content, citations JSONB, metadata JSONB)
- **document_templates** (`template_type` enum: estimate/job_summary/report/quotation/customer_email; content, variables JSONB)
- **email_metadata** (graph_message_id, subject, sender, received_at, conversation_id, is_indexed; unique org+user+graph_message_id)
- **file_metadata** (`file_source` enum sharepoint/onedrive; graph_item_id, file_name, web_url, modified_at, is_indexed; unique org+user+graph_item_id)
- **ingestion_jobs** (`ingestion_status` enum pending/processing/completed/failed; job_type, payload JSONB)
- **email_classifications** (`email_category` enum important/spam/closed/pending_action; confidence, reasoning; unique email_metadata_id)
- **audit_logs** (action, resource_type, resource_id, metadata JSONB, ip_address)
- Indexes on members/email/file/chat/audit.

### 003_file_metadata_drive
- `file_metadata` += **drive_id, site_id, mime_type** (needed for re-download; document sync errors without it → `run-003.mjs`).

### 004_sync_cursors_index_reason
- **sync_cursors** (org, user, source, drive_id, delta_link; PK on all four) — OneDrive delta cursors.
- `file_metadata` += **index_reason** (full-text vs metadata-only tracking).

### 005_projects
- **projects** (org, user, name, description, custom_instructions, timestamps)
- **project_files** (project_id, org, file_name, storage_path, mime, size, is_indexed, index_reason, **chunk_count**)
- `chat_sessions` += **project_id** (FK, cascade).

> `infrastructure/supabase/migrations/` (001 initial, 002 projects) are **legacy** from an earlier Supabase iteration; the live system uses local Postgres migrations above.

---

## 10. Pinecone Indexing Strategy

- **Index:** `PINECONE_INDEX_NAME` (default `starbot-dev`), serverless, **384-dim, cosine**.
- **Namespace:** `{organizationId}` (multi-tenant). Default namespace is NOT used.
- **Vector ID:** `{source}:{contentId}:{chunkId}`.
- **`source` metadata values:** `outlook`, `sharepoint`, `onedrive`, `project`, `{slug}-workspace`.
- **Metadata** stored per vector: source, organizationId, workspaceId?, projectId?, emailId?, fileId?, fileName?, sender?, subject?, timestamp?, webUrl?, chunkId, **text** (chunk content).
- **Upsert** batched at 100; stale chunks deleted by stable ID before re-upsert.

---

## 11. Retrieval Pipeline (end to end)

1. Topic guard (regex) → refuse off-topic before any model call.
2. Recency/content detection or ai-service intent classifier → choose sources.
3. Redis cache check (skipped for live/recency queries).
4. Keyword/exact-name SQL match (hybrid) merged with…
5. Embed query (ai-service `all-MiniLM-L6-v2`) → per-source Pinecone query (topK=20) with metadata filter.
6. Permission filter (allowed email/file/project IDs from Postgres).
7. Reciprocal Rank Fusion across source result lists.
8. Rerank in ai-service (token overlap; top-5) + document-chunk preservation guard.
9. Live Graph fallback if nothing indexed yet + schedule background sync.
10. Inject workspace/project instructions → OpenAI `LLM_MODEL` (temp 0.2) with grounded system prompt.
11. Build `[n]` citations; cache response 120 s (non-live).

---

## 12. Security Model

- **Multi-tenancy:** `organizationId` on every row + Pinecone namespace.
- **RBAC:** owner > admin > member > viewer via `RolesGuard` (e.g. audit logs admin-only).
- **App-layer authz:** every query parameterized & scoped by `organization_id`/`user_id`; ownership asserted for projects/sessions.
- **Token encryption:** Graph access/refresh tokens AES-256-GCM (`TOKEN_ENCRYPTION_KEY`).
- **Permission-aware retrieval:** pre-filter allowed content IDs; post-filter chunks lacking a metadata match.
- **Sessions:** HttpOnly cookie, JWT verified against `sessions` table; all prior sessions revoked on new login.
- **Rate limiting:** Throttler 120/60 s.
- **Audit trail:** `audit_logs` for queries, search, ingestion, mail actions, project/workspace changes.
- **Scope guard:** chat answers only from indexed M365 data; refuses personal/general queries.

---

## 13. Configuration / Environment (`.env`)

Copy root `.env` → `apps/api/.env`; set `NEXT_PUBLIC_API_URL=http://localhost:3001` in `apps/web/.env.local`.

| Group | Keys |
|-------|------|
| Postgres | `DATABASE_HOST/PORT/NAME/USER/PASSWORD` |
| JWT/session | `JWT_SECRET`, `JWT_EXPIRES_IN` (7d), `SESSION_COOKIE_NAME`, `COOKIE_SECURE` |
| API | `API_PORT` (3001), `API_URL`, `OAUTH_REDIRECT_URI`, `CORS_ORIGIN` |
| AI service | `AI_SERVICE_PORT` (8001), `AI_SERVICE_URL`, `OPENAI_API_KEY`, `LOG_LEVEL` |
| Pinecone | `PINECONE_API_KEY`, `PINECONE_INDEX_NAME`, `PINECONE_ENVIRONMENT` |
| Redis | `REDIS_URL` |
| Azure/Graph | `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET` (Secret **Value**, not ID), `AZURE_TENANT_ID` (common) |
| Encryption | `TOKEN_ENCRYPTION_KEY` (`openssl rand -hex 32`) |
| Business research | `ENABLE_BUSINESS_RESEARCH`, `TAVILY_API_KEY`, `ENABLE_INTERNET_SEARCH` |
| Topic guard | `APPXCESS_TOPIC_ALIASES` |
| Worker | `WORKER_SERVICE_TOKEN`, `INGESTION_SYNC_INTERVAL_MS` |
| Sync tuning | `ENABLE_DELTA_SYNC`, `ENABLE_TEAMS_INGESTION`, `SHAREPOINT_MAX_SITES`, `SHAREPOINT_MAX_FILES_PER_SYNC`, `DOCUMENT_MAX_BYTES`, `DOCUMENT_SYNC_RECURSION_DEPTH` |
| Projects | `PROJECT_UPLOAD_DIR` |
| Misc | `NODE_ENV`, `ENABLE_PDF_OCR` (hook only) |

**Constants (`packages/config`):** `EMBEDDING_DIMENSIONS=384`, `DEFAULT_TOP_K=20`, `RERANK_TOP_K=5`, `CHUNK_SIZE=512`, `CHUNK_OVERLAP=64`, `LLM_MODEL='gpt-5.4-mini'`.

---

## 14. Running Locally

1. **Prereqs:** Node 20.x, pnpm 9.15 (`corepack prepare pnpm@9.15.0`), Python 3.11+, PostgreSQL 15+, Pinecone index (384/cosine), OpenAI key, Azure app registration. Redis optional.
2. **Env:** `cp .env.example .env`, fill values, copy to `apps/api/.env`.
3. **DB:** `pnpm db:migrate` (Node migrator) — or `setup-windows.ps1` / manual psql. On existing DBs needing only drive cols: `node infrastructure/postgres/scripts/run-003.mjs`.
4. **Install:** `npx pnpm@9.15.0 install` (never bare `npx pnpm` → pulls pnpm 10).
5. **Build shared packages:** types → config → utils → prompts.
6. **AI service:** `cd apps/ai-service && run.cmd` (port 8001) — required before any sync (embeddings).
7. **API + Web:** `run-dev.cmd`; with scheduled sync (Redis + worker): `run-dev-full.cmd`.
8. **Azure:** multi-tenant Entra app, delegated scopes (Mail.Read/ReadWrite, Files.Read.All, Sites.Read.All, offline_access, openid, profile, email), redirect `http://localhost:3000/api/v1/auth/microsoft/callback`.
9. **Index content:** sign in with Microsoft, then `POST /api/v1/ingestion/outlook/sync` and `/ingestion/documents/sync` (or use Settings sync buttons / dashboard auto-sync).

**Dev launchers:** `run-dev.cmd/.ps1` (API+Web), `run-dev-full.cmd` (+Redis/worker), `start-api.cmd`, `scripts/verify-auth.ps1`.

---

## 15. API Endpoint Index (base `/api/v1`)

| Method | Path | Guard | Feature |
|--------|------|-------|---------|
| GET | `/auth/microsoft/authorize-url` | — | OAuth start (popup) |
| GET | `/auth/microsoft/login` | — | OAuth redirect |
| GET | `/auth/microsoft/callback` | — | OAuth callback → cookie |
| POST | `/auth/logout` | JWT | Clear session |
| GET | `/auth/me` | token | Current user |
| GET | `/auth/session`, `/auth/bootstrap` | JWT | Org context |
| POST | `/graph/sync` | JWT | Re-sync tokens |
| GET | `/graph/status` | JWT | Graph mail reachable |
| GET | `/ingestion/status` | JWT/Worker | Pinecone & sync health |
| POST | `/ingestion/outlook/sync` | JWT/Worker | Index Outlook |
| POST | `/ingestion/documents/sync` | JWT/Worker | Index SharePoint+OneDrive |
| GET | `/ingestion/worker/targets` | Worker token | Sync targets |
| POST | `/chat/sessions` | JWT | New session |
| GET | `/chat/sessions` | JWT | List sessions |
| GET | `/chat/sessions/:id/messages` | JWT | History |
| POST | `/chat/sessions/:id/messages` | JWT | RAG chat |
| POST | `/chat/sessions/:id/messages/stream` | JWT | SSE streaming chat |
| POST | `/search/unified` | JWT | Cross-platform search |
| POST | `/search/internet` | JWT | External research |
| POST | `/mail/classify` | JWT | Smart Mail Agent |
| GET | `/mail/classifications` | JWT | Classification rows |
| POST | `/mail/bulk-dismiss` | JWT | Dismiss spam |
| GET | `/workspaces` | JWT | List workspaces |
| PUT | `/workspaces/:id/instructions` | JWT | Versioned instructions |
| GET/POST | `/projects` | JWT | List/create projects |
| GET/PATCH/DELETE | `/projects/:id` | JWT | Project CRUD |
| GET/POST | `/projects/:id/files` | JWT | List/upload files |
| DELETE | `/projects/:id/files/:fileId` | JWT | Delete file |
| GET | `/documents/templates` | JWT | List templates |
| POST | `/documents/generate` | JWT | Template doc generation |
| POST | `/teams/sync` | JWT | Teams placeholder |
| GET | `/audit/logs` | JWT + Roles | Audit log (admin/owner) |
| GET | `/health` | — | Liveness |

---

## 16. Deployment & CI

- **Docker:** `infrastructure/docker/docker-compose.yml` — services: redis, ai-service, api, worker, web. `Dockerfile.api` present; web/worker dockerfiles referenced.
- **Kubernetes:** `infrastructure/kubernetes/api-deployment.yaml` (api 2+ replicas; worker autoscale on queue depth per ARCHITECTURE.md).
- **Terraform:** `infrastructure/terraform/main.tf`.
- **CI** (`.github/workflows/ci.yml`): on push/PR to main/master/develop — pnpm 9.15 + Node 20, install, build shared packages, **typecheck API**, **test API** (topic-guard + query-intent tsts via `tsx --test`), typecheck Web.
- **Observability:** OpenTelemetry-style trace ids (TraceMiddleware), structured JSON logs, `/health` on services.

---

## 17. Feature Checklist (all implemented)

- [x] Monorepo + Microsoft OAuth + local PostgreSQL
- [x] Microsoft Graph OAuth (mail/drive clients, token refresh, AES-256-GCM token storage)
- [x] Email & document ingestion (Outlook, SharePoint, OneDrive; chunking; cleaning; metadata fallback)
- [x] Sentence-Transformer embeddings (ai-service, 384-dim)
- [x] Pinecone vector storage (per-org namespace, metadata filters)
- [x] RAG + GPT synthesis + citations (hybrid keyword+vector, RRF, rerank, permission filter)
- [x] Unified + business/internet search (Tavily, hybrid synthesis)
- [x] Smart Mail Agent (classify into important/spam/closed/pending_action)
- [x] Persistent workspaces (Sales/Operations/Restoration, versioned instructions)
- [x] Template document generation
- [x] Projects with file upload & per-project knowledge + custom instructions
- [x] Scheduled ingestion (BullMQ worker, scheduler + ingestion queues)
- [x] OneDrive delta sync (Graph delta cursors)
- [x] Document types: .txt/.md/.csv/.json/.xml/.html, .docx, .pdf, .xlsx/.xls, .pptx (+ metadata-only fallback)
- [x] Streaming chat (SSE), topic guard, off-topic refusal
- [x] Docker, K8s, Terraform, audit logging, rate limiting, optional Redis cache
- [x] CI (typecheck + unit tests)
- [ ] Teams channel ingestion — **placeholder** (needs admin consent + implementation)

---

## 18. Notable Implementation Details / Gotchas

- **`LLM_MODEL = 'gpt-5.4-mini'`** is a single constant in `packages/config/src/index.ts` — change there to swap models.
- **Redis is optional in dev**; OAuth state falls back to an in-memory `Map`, RAG caching is skipped.
- **Streaming is simulated** — the full answer is generated, then emitted word-by-word over SSE (8 ms delay), not true token streaming from OpenAI.
- **Pinecone deletes** on serverless tolerate 404/400; `deleteByEmailId` is intentionally a no-op (metadata-filter deletes rejected) — re-index deletes by stable vector ID instead.
- **Background sync** is auto-triggered on login, on first chat message when nothing is indexed, and once per dashboard session (`dashboard-sync.tsx`).
- **Migration 003** is the usual culprit for `column "drive_id" ... does not exist` document-sync errors on older DBs.
- **Azure secret pitfall:** must use the Secret *Value*, not the Secret *ID* (UUID) — detected and logged by `azure-env.ts`.
- **First login auto-provisions** a personal organization with the user as `owner`.
- **Supabase migrations are legacy**; the active datastore is local PostgreSQL.
</content>
