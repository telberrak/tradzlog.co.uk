# TradzLog

TradzLog is a Python-first professional trading journal and analytics platform.

## Stack

- API: FastAPI, Pydantic v2, SQLAlchemy 2, Alembic
- Database: PostgreSQL
- Cache/jobs: Redis and RQ-ready queues
- AI: Anthropic Claude (`claude-sonnet-4-20250514`)
- Web shell: Python ASGI app with a dark trading-terminal UI foundation

## Local Setup

```bash
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .[dev]
copy .env.example .env
docker compose up -d postgres redis
alembic revision --autogenerate -m "initial schema"
alembic upgrade head
python prisma/seed.py
uvicorn tradzlog_api.main:app --reload --app-dir apps/api
```

API docs are available at `http://localhost:8000/docs`.

## Environment

Required:

- `DATABASE_URL`
- `REDIS_URL`
- `JWT_SECRET`

Optional integrations:

- `ANTHROPIC_API_KEY`
- `RESEND_API_KEY`
- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- Cloudflare R2 credentials
- `SENTRY_DSN`
- `LOG_LEVEL`
- `AXIOM_TOKEN`

## Monorepo Layout

```text
apps/
  api/          FastAPI REST API
  web/          Python ASGI web shell
packages/
  db/           SQLAlchemy models and Alembic config
  types/        Shared Pydantic request/response schemas
prisma/
  seed.py       Demo seed script requested by the product spec
```

## Implemented Foundation

- Email/password register and login with JWT bearer sessions.
- Account CRUD with user ownership checks.
- Instrument search and custom instrument creation.
- Trade CRUD, execution append, close flow, and pure trade metrics calculation.
- Journal create/list support using Tiptap-compatible JSON content.
- Analytics summary and equity curve endpoints.
- Trading rules and rule breach storage models.
- Import, upload, report, and AI route contracts ready for production integrations.
- JSON request logging with `X-Request-ID`, optional Sentry error tracking, and Redis-backed rate limits for sensitive endpoints.
- Demo seed data for `demo@tradzlog.com` / `password`, two accounts, instruments, 120 days of trades, recent journals, and three active rules.

## Deployment Guide

This Python version is ASGI-native. Deploy the API and web app to Railway, Fly.io, Render, or any container host. A later React client can still be deployed on Vercel and consume this API.

## Local Docker Deployment

Run the full local stack with PostgreSQL, Redis, API, web UI, migrations, and an RQ worker:

```bash
docker compose up -d --build
docker compose run --rm migrate python prisma/seed.py  # optional demo data; run once
python scripts/smoke_check.py http://localhost:8000
```

Local URLs:

- API: `http://localhost:8000`
- API docs: `http://localhost:8000/docs`
- Web UI: `http://localhost:8001`

Useful commands:

```bash
docker compose ps
docker compose logs -f api web worker
docker compose down
```

1. Provision PostgreSQL and Redis.
2. Set variables from `.env.example`.
3. Install with `pip install -e .`.
4. Run `alembic upgrade head`.
5. Seed demo data with `python prisma/seed.py` if needed.
6. Start the API with `uvicorn tradzlog_api.main:app --host 0.0.0.0 --port $PORT`.
7. Run background workers for analytics, imports, reports, and AI jobs.

Both API and web apps emit structured JSON logs to stdout. Incoming `X-Request-ID` values are preserved, otherwise the middleware generates one and returns it on the response. Set `SENTRY_DSN` to enable Sentry error capture; leave it blank for local development.

Worker queues use RQ. Start one worker per queue group, or one worker listening to all queues:

```bash
rq worker default analytics imports reports ai --url redis://localhost:6379/0
```

Queue names:

- `analytics`: daily stats and equity rebuilds
- `imports`: CSV preview/confirmation work
- `reports`: performance and tax report generation
- `ai`: coaching insight generation

Before deploying, verify the environment without printing secrets:

```bash
python scripts/verify_environment.py
```

For CI jobs that do not have Postgres/Redis attached, run:

```bash
python scripts/verify_environment.py --skip-network
```

After deploying, smoke-check the running API health endpoints:

```bash
python scripts/smoke_check.py https://api.your-domain.com
```

## Next Build Steps

The repo now has the Python foundation. The next production steps are CSV parser implementations, Redis-cached analytics group-bys, R2 uploads, PDF report generation, OAuth/magic-link auth, and the full interactive frontend.
