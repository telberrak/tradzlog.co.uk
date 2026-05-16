# TradzLog Documentation

Professional trading journal and analytics platform built with Python (FastAPI, SQLAlchemy, Redis) and a server-rendered web UI styled for dense terminal-style workflows.

---

## Table of contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Prerequisites](#prerequisites)
4. [Quick start (Docker)](#quick-start-docker)
5. [Local development](#local-development)
6. [Configuration](#configuration)
7. [Project structure](#project-structure)
8. [Database](#database)
9. [REST API](#rest-api)
10. [Web application](#web-application)
11. [Background workers](#background-workers)
12. [Testing](#testing)
13. [Deployment](#deployment)
14. [Troubleshooting](#troubleshooting)
15. [Related projects](#related-projects)

---

## Overview

TradzLog helps traders:

- Log and review trades with executions and metrics
- Keep structured journals (Tiptap-compatible JSON content)
- View performance analytics (summary, equity curve, grouped performance, streaks)
- Manage trading rules and rule breaches
- Import broker CSVs, upload screenshots, and generate reports
- Use AI coaching (Anthropic) when configured
- Share trades publicly and manage community/mentor features on the web UI

The stack is intentionally Python-first: FastAPI for the API, SQLAlchemy 2 for persistence, Redis for caching and job queues, and a Python ASGI web app for the UI (no separate React build required for local use).

---

## Architecture

```text
┌─────────────────────────────────────────────────────────────────┐
│                         Browser / API client                       │
└────────────────────────────┬────────────────────────────────────┘
                             │
         ┌───────────────────┴───────────────────┐
         │                                       │
         ▼                                       ▼
┌─────────────────┐                   ┌─────────────────┐
│  Web (port 8001) │                   │  API (port 8000) │
│  tradzlog_web    │                   │  tradzlog_api    │
│  SSR + forms    │                   │  REST + JWT      │
└────────┬────────┘                   └────────┬────────┘
         │                                       │
         └───────────────────┬───────────────────┘
                             │
         ┌───────────────────┼───────────────────┐
         ▼                   ▼                   ▼
┌─────────────────┐     ┌─────────────┐     ┌─────────────┐
│   PostgreSQL    │     │    Redis    │     │  RQ workers │
│   (SQLAlchemy)  │     │ cache/queues│     │  (optional) │
└─────────────────┘     └─────────────┘     └─────────────┘
```

| Component | Technology | Role |
|-----------|------------|------|
| API | FastAPI, Pydantic v2 | REST API, auth, business logic |
| Web | FastAPI (ASGI) | Server-rendered HTML UI |
| Database | PostgreSQL, Alembic | Persistence, migrations |
| Cache / jobs | Redis, RQ | Analytics cache, background tasks |
| AI | Anthropic API | Coaching insights (optional) |
| Auth | JWT (python-jose), bcrypt | API sessions; password hashing |

---

## Prerequisites

- **Python** 3.11 or newer
- **Docker** and Docker Compose (recommended for local full stack)
- **Git** (to clone the repository)

Optional for local API development without Docker for the app process:

- PostgreSQL 16+
- Redis 7+

---

## Quick start (Docker)

The fastest way to run the full stack locally:

```bash
# From the repository root
docker compose up -d --build
```

Optional: load demo data (user, accounts, instruments, trades, journals, rules):

```bash
docker compose run --rm migrate python prisma/seed.py
```

### URLs

| Service | URL |
|---------|-----|
| Web UI | http://localhost:8001 |
| API | http://localhost:8000 |
| API docs (Swagger) | http://localhost:8000/docs |
| PostgreSQL | localhost:5432 |
| Redis | localhost:6379 |

### Verify the deployment

```bash
python scripts/smoke_check.py http://localhost:8000
```

Expected: all checks pass for `/livez`, `/readyz`, and `/healthz`.

### Useful Docker commands

```bash
docker compose ps
docker compose logs -f api web worker
docker compose down
```

---

## Local development

### 1. Python environment

```bash
python -m venv .venv

# Windows PowerShell
.venv\Scripts\Activate.ps1

# macOS / Linux
source .venv/bin/activate

pip install -e .[dev]
```

### 2. Environment file

```bash
copy .env.example .env   # Windows
# cp .env.example .env     # macOS / Linux
```

Edit `.env` and set at least:

- `DATABASE_URL` — e.g. `postgresql+psycopg://tradzlog:tradzlog@localhost:5432/tradzlog`
- `REDIS_URL` — e.g. `redis://localhost:6379/0`
- `JWT_SECRET` — use a long random string in production (minimum 32 characters recommended)

### 3. Database services (without Docker for API only)

If you run Postgres and Redis yourself (not via Compose):

```bash
docker compose up -d postgres redis
```

### 4. Migrations and seed

```bash
alembic upgrade head
python prisma/seed.py
```

### 5. Run API and web separately

**API** (from repo root, with `apps/api` on the path):

```bash
uvicorn tradzlog_api.main:app --reload --app-dir apps/api --host 0.0.0.0 --port 8000
```

**Web UI**:

```bash
uvicorn tradzlog_web.main:app --reload --app-dir apps/web --host 0.0.0.0 --port 8001
```

**RQ worker** (optional, for async jobs):

```bash
rq worker default analytics imports reports ai --url redis://localhost:6379/0
```

---

## Configuration

Copy `.env.example` to `.env` and adjust values. Variables are loaded by Pydantic Settings (API) and by `tradzlog_db` settings (database URL).

| Variable | Required | Description |
|----------|----------|-------------|
| `APP_ENV` | No | Environment label (e.g. `local`, `production`) |
| `DATABASE_URL` | **Yes** | PostgreSQL connection string (`postgresql+psycopg://...`) |
| `REDIS_URL` | **Yes** | Redis URL for cache and RQ |
| `JWT_SECRET` | **Yes** | Secret for signing JWT access tokens |
| `JWT_ISSUER` | No | JWT issuer claim (default: `tradzlog.local`) |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | No | Token lifetime (default: 43200) |
| `ANTHROPIC_API_KEY` | No | Enables AI coaching when set |
| `ANTHROPIC_MODEL` | No | Claude model (default: `claude-sonnet-4-20250514`) |
| `RESEND_API_KEY` | No | Email delivery (magic link, etc.) |
| `GOOGLE_CLIENT_ID` | No | OAuth (planned integration) |
| `GOOGLE_CLIENT_SECRET` | No | OAuth (planned integration) |
| `R2_ACCOUNT_ID` | No | Cloudflare R2 for uploads (production) |
| `R2_ACCESS_KEY_ID` | No | R2 access key |
| `R2_SECRET_ACCESS_KEY` | No | R2 secret key |
| `R2_BUCKET` | No | R2 bucket name (default: `tradzlog-uploads`) |
| `SENTRY_DSN` | No | Sentry error tracking |
| `LOG_LEVEL` | No | Logging level (default: `INFO`) |
| `AXIOM_TOKEN` | No | Axiom logging (optional) |

Docker Compose injects its own defaults for `DATABASE_URL`, `REDIS_URL`, and `JWT_SECRET` when using the provided `docker-compose.yml` services.

---

## Project structure

```text
tradzlog.co.uk/
├── apps/
│   ├── api/                    # FastAPI application (tradzlog_api)
│   │   └── tradzlog_api/
│   │       ├── main.py           # Routes, middleware, app factory
│   │       ├── config.py         # API settings
│   │       ├── deps.py             # DB session, current user (JWT)
│   │       ├── security.py         # Password hashing, JWT, one-time tokens
│   │       └── services/           # Analytics, AI, cache, jobs, metrics, risk, reports, imports
│   └── web/                    # ASGI web app (tradzlog_web)
│       └── tradzlog_web/
│           └── main.py           # SSR routes, HTML templates, terminal-style CSS
├── packages/
│   ├── db/                     # SQLAlchemy models, session, Alembic
│   │   ├── tradzlog_db/
│   │   │   ├── models.py
│   │   └── alembic/
│   └── types/                  # Shared Pydantic schemas (tradzlog_types)
├── prisma/
│   └── seed.py                 # Demo data generator
├── scripts/
│   ├── verify_environment.py   # Pre-deploy env checks
│   └── smoke_check.py            # Post-deploy health checks
├── tests/                      # pytest suite
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
├── alembic.ini
├── .env.example
├── README.md
├── IMPLEMENTATION_PLAN.md      # Internal roadmap / status
└── DOCUMENTATION.md             # This file
```

---

## Database

- **ORM**: SQLAlchemy 2 with models in `packages/db/tradzlog_db/models.py`
- **Migrations**: Alembic (`alembic upgrade head` from repo root; config in `packages/db/alembic/` and `alembic.ini`)
- **Session**: `SessionLocal` in `packages/db/tradzlog_db/session.py`

Main entities include: `User`, `Account`, `Instrument`, `Trade`, `Execution`, `TradeMetrics`, `JournalEntry`, `TradingRule`, `RuleBreach`, `BrokerSync`, attachments, billing, community (shares, leaderboard, mentor), and related enums.

---

## REST API

Base URL when running locally: `http://localhost:8000`

Authentication: Bearer JWT in the `Authorization` header for protected routes. Obtain a token via `POST /api/auth/login` or `POST /api/auth/register`.

### Health

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/` | No | API info and links |
| GET | `/healthz` | No | Liveness |
| GET | `/readyz` | No | Readiness (DB + Redis) |
| GET | `/livez` | No | Liveness (simple) |
| GET | `/favicon.ico` | No | Empty favicon (204) |

### Authentication

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/auth/register` | Register user |
| POST | `/api/auth/login` | Login; returns `access_token` |
| POST | `/api/auth/verify-email` | Verify email with token |
| POST | `/api/auth/magic-link` | Request magic link (dev may return `devToken`) |
| POST | `/api/auth/magic-link/consume` | Exchange magic link for JWT |
| POST | `/api/auth/logout` | Logout (client discards token) |
| POST | `/api/auth/forgot-password` | Request password reset |
| POST | `/api/auth/reset-password` | Reset password with token |

### Accounts, instruments, trades, journals

- **Accounts**: `GET/POST /api/accounts`, `GET/PATCH/DELETE /api/accounts/{account_id}` — scoped to current user
- **Instruments**: `GET/POST /api/instruments`, `GET /api/instruments?symbol=...`
- **Trades**: Full CRUD, `POST .../close`, `POST .../executions`, `GET .../metrics`
- **Journals**: CRUD with optional filters (`type`, `date_from`, `date_to`, `trade_id`)

### Analytics and dashboard

- `GET /api/analytics/summary` — optional `account_id`
- `GET /api/analytics/equity-curve?account_id=...`
- `GET /api/analytics/by-setup`, `/by-instrument`, `/by-time`, `/streaks`, `/drawdown`
- `GET /api/dashboard/portfolio`
- `GET /api/positions` — open positions for current user

### Rules, AI, import, uploads, reports

- **Rules**: CRUD + `GET /api/rules/breaches`
- **AI**: `GET /api/ai/insights`, `POST /api/ai/insights/generate` (queued), `POST /api/ai/chat`, `GET /api/ai/journal-prompts`
- **Import**: `POST /api/import/csv`, `GET /api/import/history`
- **Uploads**: `POST /api/uploads/screenshot`, `DELETE /api/uploads/{upload_id}` (ownership via linked trade/journal)
- **Reports**: `POST /api/reports/performance-pdf`, `POST /api/reports/tax-csv` (queued), `POST /api/reports/prop-firm-pdf`

Sensitive API routes are rate-limited (see `SENSITIVE_POST_PATHS` in `apps/api/tradzlog_api/main.py`).

---

## Web application

Base URL when running locally: `http://localhost:8001`

Server-rendered HTML with a compact, dark terminal aesthetic (top market bar, horizontal tabs, dense tables). The web app uses the same database as the API via `SessionLocal` and shared models; it is not a separate frontend framework.

### Main navigation (tabs)

| Path | Description |
|------|-------------|
| `/`, `/dashboard` | Dashboard (KPIs, equity sparkline, setups, recent trades) |
| `/dashboard/portfolio` | Multi-account portfolio summary |
| `/trades` | Trade list and detail |
| `/trades/new` | Manual trade entry |
| `/positions` | Open positions with quick-close forms |
| `/journal` | Journal feed |
| `/journal/new` | New journal entry |
| `/analytics` | Analytics hub (redirects to overview) |
| `/analytics/overview`, `/analytics/instruments`, `/analytics/setups`, `/analytics/time`, `/analytics/risk`, `/analytics/streaks` | Analytics views |
| `/coaching` | AI coaching (generate, chat, prompts) |
| `/settings` | Redirects to `/settings/import` |
| `/settings/import`, `/settings/uploads`, `/settings/billing` | Settings |
| `/reports` | Reports hub and downloads |
| `/community` | Leaderboard, shares, mentor access |

### Demo credentials (after seed)

- Email: `demo@tradzlog.com`
- Password: `password`

---

## Background workers

RQ queues (Redis):

| Queue | Purpose |
|-------|---------|
| `default` | General |
| `analytics` | Daily stats, equity curve rebuilds |
| `imports` | CSV import processing |
| `reports` | Performance and tax reports |
| `ai` | AI insight generation |

Start a worker:

```bash
rq worker default analytics imports reports ai --url redis://localhost:6379/0
```

Enqueue helpers live in `apps/api/tradzlog_api/services/enqueue.py` and are used from API routes for long-running work.

---

## Testing

Install dev dependencies:

```bash
pip install -e .[dev]
```

Run tests:

```bash
python -m pytest
```

Tests cover metrics, caching, hardening, auth flows, ownership, API route security, web flows, smoke checks, and environment verification.

---

## Deployment

1. Provision **PostgreSQL** and **Redis**.
2. Set environment variables (see [Configuration](#configuration)).
3. Build the Docker image (`Dockerfile`) or install with `pip install -e .`.
4. Run `alembic upgrade head`.
5. Start API: `uvicorn tradzlog_api.main:app --host 0.0.0.0 --port $PORT`
6. Start web: `uvicorn tradzlog_web.main:app --host 0.0.0.0 --port $PORT`
7. Run RQ workers for background jobs.
8. Configure reverse proxy / TLS in production.

Pre-deploy checks:

```bash
python scripts/verify_environment.py
python scripts/verify_environment.py --skip-network   # CI without DB/Redis
```

Post-deploy:

```bash
python scripts/smoke_check.py https://your-api-domain.com
```

Observability:

- Structured JSON logs to stdout with `request_id`
- Optional `SENTRY_DSN` for Sentry
- Request ID header: `X-Request-ID` (preserved or generated)

---

## Troubleshooting

| Issue | What to check |
|-------|----------------|
| `404` on `/settings` | Use `/settings/import`, `/settings/uploads`, or `/settings/billing`; `/settings` redirects to import |
| `404` on API root `/` | Use `/docs` or ensure latest API image is running (root route added in recent versions) |
| Database connection errors | Postgres up? `DATABASE_URL` correct? Run `alembic upgrade head` |
| Redis errors on `/readyz` | Start Redis; check `REDIS_URL` |
| Rate limit `429` | Too many requests to sensitive endpoints; wait or adjust limits in `apps/api/tradzlog_api/main.py` |
| bcrypt / passlib warnings in seed | Project pins `bcrypt<5` in `pyproject.toml` for compatibility |
| Web shows old styling | Hard refresh (`Ctrl+F5`); rebuild web container: `docker compose up -d --build web` |
| Port already in use | Change host ports in `docker-compose.yml` or stop conflicting services |

---

## Related projects

- **backtester** (`c:\Workspace\local_repo\backtester`): Separate React/FastAPI backtesting UI; useful reference for terminal-style layouts and workflows. TradzLog’s web UI was aligned toward that dense, dark trading-terminal look while keeping TradzLog’s journal/analytics feature set.

---

## Implementation status

For a detailed feature checklist and remaining roadmap items, see `IMPLEMENTATION_PLAN.md` in this repository.

---

*Last updated to match the repository layout and Docker-based local workflow. For API request/response shapes, use the interactive docs at `/docs` when the API is running.*