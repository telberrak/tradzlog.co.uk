# TradzLog Implementation Plan

This plan tracks the build order for implementing the full TradzLog product in the Python-first stack.

## Feature Order

1. Auth and onboarding foundations
2. Interactive web UI shell and dashboard
3. Trade entry, trade history, and positions UX
4. Journaling system
5. Analytics visualizations
6. Broker imports and uploads
7. Reports and exports
8. AI coaching
9. Billing and subscriptions
10. Community and mentor features
11. Production hardening

## Step 1: Auth and Onboarding Foundations

Status: Complete

Completed:

- Added `AuthToken` model for email verification, password reset, and magic links.
- Added `email_verified_at` to `User`.
- Added secure one-time token generation and SHA-256 token hashing.
- Implemented `POST /api/auth/verify-email`.
- Implemented `POST /api/auth/magic-link`.
- Implemented `POST /api/auth/magic-link/consume`.
- Implemented functional `POST /api/auth/forgot-password`.
- Implemented functional `POST /api/auth/reset-password`.
- Validated with `python -m compileall apps packages prisma tests`.

Remaining:

- Add email delivery through Resend for verification, reset, and magic-link emails.
- Add Google OAuth flow.
- Add onboarding models/routes for account setup, instruments, setup tags, rules, and initial import/manual trade.
- Add rate limiting for auth endpoints.
- Add tests for token expiry, single-use behavior, and password reset.

## Step 2: Interactive Web UI Shell and Dashboard

Status: In progress

Build the authenticated app shell with the dark trading-terminal visual identity:

Completed:

- Replaced the static web shell with a reusable server-rendered Python ASGI app shell.
- Added dark trading-terminal visual design with sidebar navigation, top bar, account controls, dense cards, and responsive layout.
- Added database-backed `/dashboard` with KPI cards for net P&L, win rate, profit factor, total trades, average R, and average hold time.
- Added setup scoreboard, recent trades table, account switching links, and equity sparkline.
- Added `/dashboard/portfolio` with all-account KPIs and per-account summary cards.

Remaining:

- Add authenticated session handling for the web app instead of demo-user fallback.
- Add real date range filtering in dashboard queries.
- Add notifications bell data and account selector dropdown behavior.
- Add client-side widget dragging/resizing for the dashboard grid.

## Step 3: Trade Entry, Trade History, and Positions UX

Status: In progress

Completed:

- Added `/trades` server-rendered trade history with status filters, account filtering hooks, dense trade rows, P&L coloring, status badges, and links to detail pages.
- Added `/trades/new` manual trade entry form with account, instrument, direction, status, setup tag, timeframe, timestamps, planned stop/target, entry/exit prices, fees, quantity, and notes.
- Added `POST /trades/new` to create real trades with entry/exit executions, planned R calculation, trade metrics, daily stats rebuilds, and equity curve rebuilds.
- Added `/trades/{trade_id}` detail page with KPI cards, trade plan, notes, and execution breakdown.
- Added `/positions` open positions risk board with account grouping data, risk calculation, and inline quick-close forms.
- Added `POST /positions/{trade_id}/close` to close open positions, append exit executions, recompute metrics, and rebuild analytics.

Remaining:

- Add repeatable multi-execution rows in the UI instead of a single entry plus optional full exit.
- Add asset-class-specific fields for options, futures, forex, stocks, crypto, and commodities.
- Add live browser-side risk/reward and position sizing calculator.
- Add richer filters for asset class, setup tag, direction, outcome, tags, and P&L range.
- Add expandable trade rows, inline quick edit, reviewed flag toggles, CSV/PDF export buttons, and virtualized table behavior.

## Step 4: Journaling System

Status: In progress

Completed:

- Added `/journal` chronological feed with type filters, mood badges, market condition badges, and content previews.
- Added `/journal/new` structured journal form for daily, weekly, freeform, and trade review entries.
- Added `POST /journal/new` to save Tiptap-compatible JSON content, mood, market condition, linked trade, and up to five key lessons.
- Added `/journal/{journal_id}` detail page with reflection content, context, key lessons, and linked trade navigation.
- Added `/trades/{trade_id}/journal` shortcut to create a prefilled per-trade review.
- Added trade detail page link to open the trade journal flow.

Remaining:

- Add a richer browser editor with formatting controls, image embeds, and section templates.
- Add journal search by keyword and tag filtering.
- Add editable journal entries and delete/archive actions in the web UI.
- Add chart attachment upload and annotation persistence.
- Add automatic daily stats block and linked-trades block inside daily reviews.

## Step 5: Analytics Visualizations

Status: In progress

Completed:

- Added `/analytics` and `/analytics/overview` with portfolio KPI cards, combined equity curve, daily P&L bars, and setup performance table.
- Added `/analytics/instruments` for instrument-level trades, net P&L, win rate, profit factor, and average R.
- Added `/analytics/setups` for setup tag performance.
- Added `/analytics/time` with day-of-week and hour-of-day heat-map style cards.
- Added `/analytics/risk` with max drawdown, best/worst day, drawdown curve, and daily P&L distribution.
- Added `/analytics/streaks` with longest win streak, longest loss streak, current streak, and setup stability table.

Remaining:

- Add real charting components for line, bar, histogram, scatter, and calendar heat-map visuals.
- Add date range filtering and account filtering across all analytics pages.
- Add win/loss R-multiple distribution and rolling 10-trade moving average.
- Add comparison tab for periods, setup tags, and accounts.
- Add Redis caching for expensive analytics queries.

## Step 6: Broker Imports and Uploads

Status: In progress

Completed:

- Added `/settings/import` CSV import page with account selector, broker format selector, CSV upload, spreadsheet-paste area, and import history.
- Added `POST /settings/import/preview` to parse CSV rows, normalize broker column aliases, show preview rows, log `BrokerSync` history, and flag duplicate broker order IDs.
- Added import history table backed by `BrokerSync`.
- Added `/settings/uploads` screenshot upload page with trade/journal linking.
- Added `POST /settings/uploads` to store uploaded chart images locally for development and persist `Attachment` rows.
- Mounted local development uploads at `/uploads-local`.

Remaining:

- Convert CSV preview rows into confirmed trade/execution imports.
- Add visual column mapper UI for unmatched CSV formats.
- Add broker-specific parsing profiles for IBKR, TD Ameritrade, TradeStation, NinjaTrader, MT4/MT5, and generic OHLCV exports.
- Add spreadsheet paste preview/import confirmation.
- Replace local upload storage with Cloudflare R2 and signed object URLs.
- Add attachment annotation UI and JSON persistence.

## Step 7: Reports and Exports

Status: In progress

Completed:

- Added `/reports` hub with report cards, summary KPIs, and generation status.
- Added `/reports/performance` printable performance report with summary KPIs, setup breakdown, top wins, and top losses.
- Added `/reports/tax-csv` download route using the tax CSV export service.
- Added `/reports/prop-firm` report with prop account limits, P&L, daily loss utilisation, and total drawdown utilisation.

Remaining:

- Add true PDF rendering with `@react-pdf` equivalent or a Python PDF renderer.
- Add background report generation jobs and persisted download links.
- Add period/account selectors for reports.
- Add chart image embedding into generated performance reports.
- Add prop firm consistency score and days-traded calculations.

## Step 8: AI Coaching

Status: In progress

Completed:

- Added `/coaching` dashboard with performance context, setup scorecard, latest AI insights, and generate action.
- Added `POST /coaching/generate` to build a coaching payload and persist `AIInsight` records using the Anthropic-ready AI service.
- Added `/coaching/chat` Ask the Coach interface with deterministic data-grounded responses from current stats, setup breakdown, and recent trades.
- Added `/coaching/prompts` journal prompt generator based on selected date and that day's trades.
- Reused the configured Anthropic service path, with graceful fallback when `ANTHROPIC_API_KEY` is not configured.

Remaining:

- Add streaming Claude chat responses.
- Add weekly summary cron/background job and Monday delivery.
- Add four-hour debounce and Redis-backed rate limiting for on-demand insight generation.
- Add supporting-trade filters and links from each insight.
- Expand setup scorecards with common mistakes, hold time, expectancy, and AI narrative per setup.

## Step 9: Billing and Subscriptions

Status: In progress

Completed:

- Added `BillingSubscription` and `BillingInvoice` models for provider-ready subscription and invoice state.
- Added `/settings/billing` page with current plan, subscription status, plan comparison cards, feature gates, billing portal action, and invoice history.
- Added `POST /settings/billing/checkout` local checkout flow to change plans, create subscription records, and create paid invoice rows for paid plans.
- Added `POST /settings/billing/portal` local development portal placeholder.
- Added visible feature gate status for Reports, AI Coaching, and Community by plan.

Remaining:

- Integrate real Stripe Checkout sessions.
- Integrate Stripe Billing Portal sessions.
- Add webhook handling for subscription, invoice, payment failure, and cancellation events.
- Enforce plan gates across protected routes rather than displaying status only.
- Add cancellation and invoice hosted URL flows.

## Step 10: Community and Mentor Features

Status: In progress

Completed:

- Added `PublicTradeShare`, `LeaderboardProfile`, `MentorAccess`, and `MentorComment` models.
- Added `/community` page with leaderboard opt-in, public leaderboard table, public trade share list, and mentor entry point.
- Added `POST /community/leaderboard` to save public leaderboard profile and preferred ranking metric.
- Added `POST /trades/{trade_id}/share` to create anonymized public trade share links.
- Added `/share/{slug}` public trade page showing symbol, direction, setup, and R-multiple without dollar P&L.
- Added `/community/mentor` page to grant mentor access and view mentor feedback.
- Added mentor access creation and mentor comment creation flows.

Remaining:

- Add share buttons directly in trade history rows.
- Add Twitter/X image card generation and Open Graph metadata.
- Add normalized leaderboard filters by asset class, timeframe, and account type.
- Add mentor invitation emails, authentication, and true read-only mentor sessions.
- Add mentor comment notifications and comment resolution workflow.

## Step 11: Production Hardening

Status: In progress

Completed:

- Added shared security headers for API and web responses.
- Added in-memory rate limiting middleware for sensitive auth, AI, upload, import, and billing endpoints.
- Added `/livez` and `/readyz` API endpoints.
- Added database readiness check and Redis availability check.
- Added tests for the rate limiter and security headers.
- Added `scripts/verify_environment.py` for deployment environment checks without printing secrets.
- Documented deployment verification commands in `README.md`.
- Added tests for required environment validation, JWT secret validation, and network-skip mode.
- Added Redis-backed JSON cache helper with in-memory fallback.
- Added 5-minute caching for analytics summary and grouped performance reads.
- Added analytics cache invalidation after daily stats rebuilds.
- Added tests for cache fallback, prefix deletion, and Decimal restoration.
- Added RQ task functions for analytics rebuilds, report generation, tax CSV generation, AI insight generation, and CSV import previews.
- Added enqueue helpers for analytics, imports, reports, and AI queues.
- Updated API AI/report endpoints to enqueue background work.
- Documented RQ worker startup and queue names in `README.md`.
- Added tests for enqueue helper queue/function routing.
- Added JSON request logging, generated/preserved `X-Request-ID` headers, and optional Sentry initialization from `SENTRY_DSN`.
- Replaced single-process-only sensitive endpoint throttling with Redis-backed rate limiting and an in-memory fallback when Redis is unavailable.
- Added tests for Redis rate limiting, fallback behavior, structured log formatting, and user-scoped account/trade/journal ownership lookups.
- Added `scripts/smoke_check.py` to call deployed `/livez`, `/readyz`, and `/healthz` endpoints.
- Documented post-deploy smoke-check usage in `README.md`.
- Added tests for smoke-check success/failure behavior and route-level auth/ownership boundaries.
- Added auth token lifecycle tests for registration verification tokens, email verification, magic-link single-use behavior, and password reset single-use behavior.
- Added API boundary tests for CSV import ownership, import history account scoping, screenshot upload validation, and authenticated report queue ownership.
- Added screenshot upload validation for supported image MIME types and path-like file names.
- Added web flow tests for local billing checkout, leaderboard profile create/update, public trade sharing ownership, mentor access grants, and mentor comment ownership.
- Completed final security review across row-level ownership, auth token lifecycle, uploads, public sharing, mentor access, rate limits, observability, and deployment checks.
- Patched final review gaps: API upload deletion ownership checks, expanded sensitive POST rate-limit coverage, and stricter web upload validation.

Remaining:

- Production integrations: real email delivery, Cloudflare R2 uploads, Stripe billing, OAuth, and frontend polish.
