# Starbot — Enterprise AI Assistant Architecture

## Executive Summary

Starbot is a multi-tenant RAG assistant that unifies Microsoft 365 (Outlook, SharePoint, OneDrive), workspace knowledge, and optional internet search. Data flows from Graph API through ingestion workers into Pinecone vectors with metadata in local PostgreSQL (via `pg`, raw SQL). Queries traverse LangGraph orchestration, permission-filtered retrieval, re-ranking, and GPT-4o-mini synthesis with citations.

---

## 1. System Architecture

```
┌─────────────┐     ┌─────────────┐     ┌──────────────┐
│  Next.js    │────▶│  NestJS API │────▶│ Redis Cache  │
│  (web)      │     │  (api)      │     └──────────────┘
└──────┬──────┘     └──────┬──────┘
       │                   │
       │ JWT cookie        ├──▶ PostgreSQL (pg Pool, app-layer auth)
       │                   ├──▶ Pinecone (vectors only)
       │                   ├──▶ Microsoft Graph
       │                   └──▶ ai-service (embeddings + LangGraph)
       │
┌──────▼──────┐     ┌─────────────┐
│  Worker     │────▶│ ai-service  │
│  (BullMQ)   │     │ (FastAPI)   │
└─────────────┘     └─────────────┘
```

### Decision: Monorepo with four apps

| App | Role | Rationale |
|-----|------|-----------|
| **web** | UI, SSR, cookie session | Next.js 15 App Router; proxies `/api/v1` to NestJS |
| **api** | REST, JWT guards, Microsoft OAuth | NestJS + `pg`; Entra OAuth callback stores users/tokens/sessions |
| **worker** | Async ingestion, indexing, mail classification | Decouples long Graph/Pinecone jobs from request path; BullMQ + Redis |
| **ai-service** | Embeddings + LangGraph | Sentence Transformers is Python-native; isolates GPU/CPU-heavy work |

### Decision: Embeddings not in PostgreSQL

Vectors live only in Pinecone; PostgreSQL holds searchable metadata, permissions, and audit trails. Avoids PG vector scale limits and keeps a single vector index per org namespace.

### Decision: Redis

Caches query embeddings (short TTL), session context snippets, Graph token metadata, and rate-limit counters.

---

## 2. PostgreSQL Schema

See `infrastructure/postgres/migrations/001_auth_core.sql` and `002_enterprise_schema.sql`.

**Core entities:**

- `organizations` — tenant root
- `organization_members` — user ↔ org with role (owner, admin, member, viewer)
- `workspaces` — persistent department context (sales, operations, restoration)
- `workspace_instructions` — system prompts per workspace
- `workspace_documents` — uploaded KB file metadata (blobs in Storage)
- `chat_sessions` / `chat_messages` — conversation history
- `document_templates` — generation templates with variables JSON
- `email_metadata` / `file_metadata` — Graph sync state, no embeddings
- `ingestion_jobs` — job status for worker
- `audit_logs` — security/compliance
- `user_graph_tokens` — encrypted refresh tokens (app-level encryption layer)
- `email_classifications` — Smart Mail Agent labels

**RLS:** Every table scoped by `organization_id`; members can only read rows for orgs they belong to; workspace rows additionally check `workspace_members` or org role ≥ admin.

---

## 3. Pinecone Indexing Strategy

**Index:** `starbot-{env}` (single index, multi-tenant via namespaces)

**Namespace:** `{organizationId}`

**Metadata filters within namespace:**

| Logical partition | Metadata field `source` |
|-------------------|-------------------------|
| Outlook | `outlook` |
| SharePoint | `sharepoint` |
| OneDrive | `onedrive` |
| Workspace KB | `{workspace-slug}-workspace` e.g. `sales-workspace` |

**Vector ID:** `{source}:{contentId}:{chunkId}`

**Upsert batch:** 100 vectors per request; worker coordinates after chunking.

**Query:** Filter `organizationId` + optional `source` + permission-derived `emailId`/`fileId` allowlists from Supabase before/after Pinecone query.

---

## 4. LangGraph Workflow

```
START → classify_intent
     → route: [internal_rag | cross_platform | internet | mail_agent | doc_gen]
     
internal_rag:
  embed_query → pinecone_search → permission_filter → rerank → generate_answer → cite_sources → END

cross_platform:
  parallel_search[outlook, sharepoint, onedrive, workspaces] → merge → rerank → generate → END

internet:
  web_search → separate_context_block → generate (labeled external) → END

mail_agent:
  fetch_recent_emails → classify (important/spam/closed/pending) → summarize_actions → END

doc_gen:
  load_template → fill_variables → llm_polish → END
```

**State:** `StarbotGraphState` with messages, retrieved chunks, citations, workspace_id, org_id, user_id.

---

## 5. API Contracts

Base: `/api/v1` (NestJS global prefix)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/auth/microsoft/login` | Redirect to Microsoft Entra |
| GET | `/auth/me` | Current user from session cookie |
| POST | `/auth/logout` | Revoke session |
| GET | `/auth/session` | Org context (JWT guard) |
| GET | `/organizations/current` | Current org + role |
| CRUD | `/workspaces` | Workspace management |
| POST | `/chat/sessions` | New session |
| POST | `/chat/sessions/:id/messages` | Stream RAG response |
| POST | `/search/unified` | Cross-platform search |
| POST | `/search/internet` | External search |
| POST | `/ingestion/outlook/sync` | Trigger email sync |
| POST | `/ingestion/documents/sync` | SharePoint/OneDrive sync |
| GET | `/mail/classifications` | Smart Mail Agent results |
| POST | `/mail/bulk-dismiss` | Bulk draft dismissal |
| CRUD | `/templates` | Document templates |
| POST | `/documents/generate` | Template-based generation |

**ai-service internal:**

- `POST /embed` — batch texts → vectors
- `POST /rag/query` — full LangGraph pipeline
- `POST /rerank` — cross-encoder or score fusion

---

## 6. Authentication Flow

1. User clicks **Continue with Microsoft** in **web** → `GET /api/v1/auth/microsoft/login` (proxied to **api**).
2. **api** redirects to Entra with Graph scopes; callback `GET /auth/microsoft/callback` exchanges code, fetches Graph `/me`, upserts `users` and encrypted `oauth_tokens`, creates JWT `sessions` row, sets HttpOnly cookie.
3. **web** middleware checks `starbot_session` cookie; server components call **api** with forwarded cookies.
4. **api** `JwtAuthGuard` verifies JWT + DB session; loads `organization_members` for `orgId` and `role`.
5. Graph access uses `oauth_tokens` per user; refresh via Entra token endpoint.
6. Graph API calls use decrypted delegated tokens from `user_graph_tokens`.
7. Authorization is enforced in **api** services via SQL filters on `organization_id` / `user_id` (no RLS).

---

## 7. Retrieval Workflow

1. **Query embedding** — ai-service `all-MiniLM-L6-v2` (384-dim).
2. **Pinecone** — top-K=20 per source namespace partition, metadata filter `organizationId`.
3. **Permission filter** — api loads allowed `emailId`/`fileId` set from `email_metadata`/`file_metadata` where user has Graph permission snapshot.
4. **Re-rank** — reciprocal rank fusion + optional cross-encoder in ai-service; top-5 to LLM.
5. **Generation** — GPT-4o-mini with grounded prompt from `packages/prompts`.
6. **Citations** — map chunk metadata to `[1] Sender — Subject (date)` format.

---

## 8. Security Architecture

- **Multi-tenancy:** `organizationId` on every row and Pinecone namespace.
- **RBAC:** owner > admin > member > viewer; enforced in NestJS guards + RLS.
- **Workspace isolation:** `workspace_id` on vectors and documents; members table optional per workspace.
- **App-layer auth:** JwtAuthGuard + parameterized SQL scoped by org/user.
- **Audit:** `audit_logs` for search, export, token refresh, admin actions.
- **Encryption:** Graph tokens AES-256-GCM at application layer in `oauth_tokens`.
- **Permission-aware retrieval:** Pre-filter metadata IDs; post-filter low-confidence matches without metadata match.

---

## 9. Deployment Architecture

| Component | Runtime |
|-----------|---------|
| web | Vercel / K8s |
| api | K8s Deployment (2+ replicas) |
| worker | K8s Deployment (autoscale on queue depth) |
| ai-service | K8s with CPU node pool (optional GPU) |
| Redis | ElastiCache / Redis Cloud |
| PostgreSQL | Local / hosted (pg Pool) |
| Pinecone | Serverless index |

**Docker:** `infrastructure/docker/` — multi-stage builds per app.

**Observability:** OpenTelemetry traces (api → ai-service), structured JSON logs, health `/health` on all services.

---

## Phase Implementation Map

| Phase | Deliverables |
|-------|----------------|
| 1 | Monorepo, PostgreSQL migrations, Microsoft OAuth, JWT guards |
| 2 | Graph OAuth, mail/drive clients |
| 3 | Ingestion services, chunking, cleaning |
| 4 | ai-service embedding endpoint |
| 5 | Pinecone service in api/worker |
| 6 | LangGraph RAG + chat streaming |
| 7 | Unified search endpoint |
| 8 | Mail classification pipeline |
| 9 | Workspace CRUD + memory |
| 10 | Templates + document generation |
| 11 | Rate limits, audit, Docker, K8s manifests |
