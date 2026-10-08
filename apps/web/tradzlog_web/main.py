from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from decimal import Decimal
from html import escape
from urllib.parse import quote, urlencode
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import selectinload

from tradzlog_api.config import settings
from tradzlog_api.services import storage
from tradzlog_api.services.ai import build_coaching_payload, generate_coaching_insight
from tradzlog_api.services.analytics import (
    grouped_performance,
    rebuild_daily_stats,
    rebuild_equity_curve,
    summary,
)
from tradzlog_api.services.hardening import SECURITY_HEADERS, rate_limiter
from tradzlog_api.services.metrics import compute_planned_rr, compute_trade_metrics
from tradzlog_api.services.observability import (
    finish_request,
    init_observability,
    start_request,
)
from tradzlog_api.services.reports import performance_report_payload, tax_report_csv
from tradzlog_db.models import (
    Account,
    AccountSnapshot,
    AIInsight,
    Attachment,
    BillingInvoice,
    BillingStatus,
    BillingSubscription,
    CashTransaction,
    Direction,
    Execution,
    ExecutionType,
    Instrument,
    JournalEntry,
    JournalType,
    LeaderboardMetric,
    LeaderboardProfile,
    MarketCondition,
    MentorAccess,
    MentorAccessStatus,
    MentorComment,
    Plan,
    PublicTradeShare,
    Trade,
    TradeMetrics,
    TradeStatus,
    User,
)
from tradzlog_db.session import SessionLocal
from tradzlog_web import book, localtime
from tradzlog_web.account_routes import router as account_router
from tradzlog_web.auth import LoginRequired, current_user, web_auth
from tradzlog_web.context import CURRENT_USER_ID
from tradzlog_web.data_routes import router as data_router
from tradzlog_web.components import (
    account_picker,
    edge_bars,
    equity_chart,
    execution_timeline,
    filter_bar,
    hold_time,
    kpi,
    money,
    number,
    pnl_calendar,
    price_ladder,
    query_string,
    short_datetime,
    tone,
    trade_table,
)
from tradzlog_web.import_routes import router as import_router
from tradzlog_web.onboarding import checklist
from tradzlog_web.onboarding import router as onboarding_router
from tradzlog_web.public_routes import error_page, feature_gate, landing_page
from tradzlog_web.public_routes import router as public_router
from tradzlog_web.settings_routes import router as settings_router
from tradzlog_web.transaction_routes import router as transaction_router
from tradzlog_web.ui import (
    coach_quality_badge,
    empty_state,
    filter_tabs,
    insight_cards_html,
    kpi_card,
    shell,
    side_badge,
    status_badge,
)

init_observability()
logger = logging.getLogger("tradzlog.web")

class RedirectTo(Exception):
    def __init__(self, url: str) -> None:
        self.url = url


async def custom_range_redirect(request: Request) -> None:
    """App-wide dependency: the filter bar's From/To fields become one ``range`` value (see book.custom_code)."""
    params = request.query_params
    if request.method != "GET" or ("from" not in params and "to" not in params):
        return
    code = book.custom_code(book.parse_day(params.get("from", "")), book.parse_day(params.get("to", "")))
    kept = [(key, value) for key, value in params.multi_items() if key not in {"from", "to", "range", "month"}]
    if code:
        kept.append(("range", code))
    elif params.get("range"):
        kept.append(("range", params["range"]))
    raise RedirectTo(request.url.path + (f"?{urlencode(kept)}" if kept else ""))


app = FastAPI(
    title="TradzLog Web",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    # Identify the signed-in user and enforce CSRF on every POST (see tradzlog_web.auth).
    # Switched-off features answer 404 (tradzlog_web.public_routes.feature_gate).
    dependencies=[Depends(web_auth), Depends(feature_gate), Depends(custom_range_redirect)],
)
app.include_router(public_router)
app.include_router(account_router)
app.include_router(settings_router)
app.include_router(import_router)
app.include_router(data_router)
app.include_router(onboarding_router)
app.include_router(transaction_router)


@app.exception_handler(LoginRequired)
async def login_required(request: Request, exc: LoginRequired) -> RedirectResponse:
    del exc
    target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    destination = f"/login?next={quote(target)}" if request.method == "GET" and target not in {"/", "/dashboard"} else "/login"
    return RedirectResponse(destination, status_code=status.HTTP_303_SEE_OTHER)


@app.exception_handler(RedirectTo)
async def redirect_to(request: Request, exc: RedirectTo) -> RedirectResponse:
    del request
    return RedirectResponse(exc.url, status_code=status.HTTP_303_SEE_OTHER)


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException) -> HTMLResponse:
    del request
    detail = exc.detail if isinstance(exc.detail, str) else None
    return HTMLResponse(error_page(exc.status_code, detail), status_code=exc.status_code, headers=getattr(exc, "headers", None))


@app.exception_handler(Exception)
async def server_error(request: Request, exc: Exception) -> HTMLResponse:
    # Runs outside the middleware below, so it adds the security headers itself.
    del request, exc
    return HTMLResponse(error_page(500), status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, headers=SECURITY_HEADERS)
UPLOAD_ROOT = storage.LOCAL_ROOT
UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
# Only used by local storage; in production screenshots are private S3 objects behind presigned links.
app.mount("/uploads-local", StaticFiles(directory=str(UPLOAD_ROOT)), name="uploads-local")


WEB_SENSITIVE_POSTS = {
    "/coaching/generate",
    "/coaching/chat",
    "/settings/import/preview",
    "/settings/uploads",
    "/login",
    "/signup",
    "/settings/security/password",
    "/settings/data/export",
    "/settings/data/delete",
    "/settings/billing/checkout",
    "/settings/billing/portal",
    "/community/leaderboard",
    "/community/mentor/access",
    "/community/mentor/comments",
}

WEB_SENSITIVE_POST_PREFIXES = ("/positions/", "/trades/", "/settings/import/", "/transactions")

ALLOWED_UPLOAD_TYPES = set(storage.IMAGE_EXTENSIONS)
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


@app.get("/livez", include_in_schema=False)
def livez() -> dict[str, str]:
    return {"status": "ok"}


def is_sensitive_web_post(path: str) -> bool:
    return path in WEB_SENSITIVE_POSTS or any(path.startswith(prefix) for prefix in WEB_SENSITIVE_POST_PREFIXES)


@app.middleware("http")
async def web_security_headers(request: Request, call_next):
    request_id, started_at = start_request(request.headers.get("X-Request-ID"))
    client = request.client.host if request.client else "unknown"
    if request.method == "POST" and is_sensitive_web_post(request.url.path):
        result = rate_limiter.check(f"web:{request.url.path}:{client}", limit=30, window_seconds=60)
        if not result.allowed:
            response = HTMLResponse(
                error_page(status.HTTP_429_TOO_MANY_REQUESTS),
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                headers={"X-Request-ID": request_id},
            )
            for key, value in SECURITY_HEADERS.items():
                response.headers.setdefault(key, value)
            finish_request(
                logger=logger,
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                started_at=started_at,
                client=client,
            )
            return response
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("request failed")
        raise
    for key, value in SECURITY_HEADERS.items():
        response.headers.setdefault(key, value)
    response.headers.setdefault("X-Request-ID", request_id)
    finish_request(
        logger=logger,
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        started_at=started_at,
        client=client,
    )
    return response

def today_utc() -> date:
    """Today in the signed-in user's timezone (name kept for existing callers)."""
    return localtime.today()


def load_book(session, user: User, account_id: str | None) -> tuple[list[Account], Account | None, list[Trade]]:
    """Accounts, the selected account (None = all), and every trade in scope, newest first."""
    accounts = list(
        session.scalars(
            select(Account).where(Account.user_id == user.id, Account.archived_at.is_(None)).order_by(Account.created_at)
        ).all()
    )
    selected = next((account for account in accounts if account.id == account_id), None)
    scope = [selected.id] if selected else [account.id for account in accounts]
    if not scope:
        return accounts, selected, []
    trades = list(
        session.scalars(
            select(Trade)
            .options(selectinload(Trade.metrics), selectinload(Trade.instrument), selectinload(Trade.account))
            .where(Trade.user_id == user.id, Trade.account_id.in_(scope))
            .order_by(Trade.opened_at.desc())
        ).all()
    )
    return accounts, selected, trades


def closed_in(trades: list[Trade], period: book.Period, previous: bool = False) -> list[Trade]:
    selected = []
    for trade in trades:
        day = book.closed_day(trade)
        if trade.status != TradeStatus.CLOSED or day is None:
            continue
        if period.contains_previous(day) if previous else period.contains(day):
            selected.append(trade)
    return selected


def load_cash_flows(session, accounts: list[Account]) -> list[CashTransaction]:
    """Deposits and withdrawals of ``accounts`` (already the user's own), oldest first."""
    if not accounts:
        return []
    return list(session.scalars(
        select(CashTransaction).where(CashTransaction.account_id.in_([account.id for account in accounts]))
        .order_by(CashTransaction.occurred_on, CashTransaction.created_at)
    ).all())


def cash_flows_in(flows: list[CashTransaction], period: book.Period) -> dict[date, Decimal]:
    days: dict[date, Decimal] = {}
    for flow in flows:
        if period.contains(flow.occurred_on):
            days[flow.occurred_on] = days.get(flow.occurred_on, Decimal("0")) + flow.signed_amount
    return days


def opening_balance(accounts: list[Account], trades: list[Trade], period: book.Period, flows: list[CashTransaction] = ()) -> Decimal:
    """Starting balances plus everything before the period: closed P&L, deposits and withdrawals."""
    balance = sum((account.starting_balance for account in accounts), Decimal("0"))
    if period.start is not None:
        balance += sum(
            (book.pnl(trade) for trade in trades
             if trade.status == TradeStatus.CLOSED and (day := book.closed_day(trade)) is not None and day < period.start),
            Decimal("0"),
        )
        balance += sum((flow.signed_amount for flow in flows if flow.occurred_on < period.start), Decimal("0"))
    return balance


def parse_month(value: str | None, fallback: date) -> date:
    try:
        return datetime.strptime(value, "%Y-%m").date() if value else fallback.replace(day=1)
    except ValueError:
        return fallback.replace(day=1)


def drawdown_note(selected: Account | None) -> str:
    if selected is not None and selected.max_total_loss and selected.starting_balance:
        limit = selected.max_total_loss / selected.starting_balance * Decimal("100")
        return f"Limit -{limit:.1f}%"
    return "Peak to trough"


def kpi_strip(current: book.Stats, previous: book.Stats | None, max_dd: Decimal, dd_note: str) -> str:
    if previous is None:
        net_note, net_note_tone = f"{current.trades} closed trades", ""
    else:
        change = book.percent_change(current.net_pnl, previous.net_pnl)
        delta = current.net_pnl - previous.net_pnl
        net_note = f"{money(delta, signed=True)} vs prior period" + (f" ({change:+.0f}%)" if change is not None else "")
        net_note_tone = tone(delta)
    pf = current.profit_factor
    pf_tone = "" if pf is None else ("positive" if pf >= Decimal("1.5") else "negative" if pf < 1 else "")
    hold = f"Avg hold {hold_time(int(current.average_hold_hours * 3600))}" if current.average_hold_hours is not None else "per trade"
    return f"""<section class="kpi-row">
      {kpi("Net P&L", money(current.net_pnl, signed=True), tone(current.net_pnl), net_note, net_note_tone)}
      {kpi("Win rate", number(current.win_rate, "%"), "", f"{current.wins} / {current.trades} trades")}
      {kpi("Profit factor", number(pf) if pf is not None else "—", pf_tone, "Target ≥ 1.50")}
      {kpi("Expectancy", number(current.expectancy_r, "R") if current.expectancy_r is not None else "—", tone(current.expectancy_r), hold)}
      {kpi("Max drawdown", number(max_dd, "%"), "negative" if max_dd < 0 else "", dd_note)}
    </section>"""


def no_user_page(title: str, active: str) -> str:
    return shell(title, active, empty_state("No trader profile yet", "Run `python prisma/seed.py` to load demo data."))


def render_dashboard(account_id: str | None = None, range_code: str | None = None, month: str | None = None) -> str:
    session = SessionLocal()
    try:
        user = current_user(session)
        if user is None:
            return no_user_page("Dashboard", "dashboard")
        today = today_utc()
        period = book.resolve_period(range_code, today)
        accounts, selected, trades = load_book(session, user, account_id)
        guide = checklist(session, user)
        if not trades:
            # Nothing to chart yet: the guide (or a pointer to it) instead of empty charts.
            filters = filter_bar("/dashboard", accounts, selected.id if selected else None, period) if accounts else ""
            nothing = "" if guide else empty_state(
                "No trades yet", "Import a broker export or log a trade, and your dashboard fills in.",
                "/settings/import" if accounts else "/settings/accounts", "Import trades" if accounts else "Add a trading account")
            return shell("Dashboard", "dashboard", f"{filters}{guide}{nothing}", selected.name if selected else "All accounts", user.name or user.email)
        scope_accounts = [selected] if selected else accounts
        current = closed_in(trades, period)
        previous = book.stats(closed_in(trades, period, previous=True)) if period.previous_start else None
        current_stats = book.stats(current)
        flows = load_cash_flows(session, scope_accounts)
        opening = opening_balance(scope_accounts, trades, period, flows)
        curve = book.equity_curve(opening, current, cash_flows_in(flows, period))
        closing = curve[-1].balance if curve else opening
        account_param = selected.id if selected else None
        nav_href = "/dashboard" + query_string({"account_id": account_param, "range": period.code})
        recent = [trade for trade in trades if period.contains(book.opened_day(trade))][:8]
        setups = book.by_expectancy(book.group_by(current, lambda trade: trade.setup_tag or "Untagged"))
        instruments = book.by_expectancy(book.group_by(current, lambda trade: trade.instrument.symbol))
        all_closed = [trade for trade in trades if trade.status == TradeStatus.CLOSED]
        body = f"""
          {filter_bar("/dashboard", accounts, account_param, period)}
          {guide}
          {kpi_strip(current_stats, previous, book.max_drawdown_pct(curve), drawdown_note(selected))}
          <section class="dash-grid">
            <div class="card">
              <div class="card-title"><h2>Equity curve</h2><span class="meta">{money(opening)} → <b class="{tone(closing - opening)}">{money(closing)}</b></span></div>
              {equity_chart(curve, opening)}
            </div>
            <div class="card">{pnl_calendar(parse_month(month, today), book.daily_pnl(all_closed), today, nav_href)}</div>
          </section>
          <section class="grid two-col" style="grid-template-columns:repeat(auto-fit,minmax(320px,1fr));margin-bottom:14px">
            <div class="card"><div class="card-title"><h2>Edge by setup</h2><span class="meta">Expectancy per trade</span></div>{edge_bars(setups)}</div>
            <div class="card"><div class="card-title"><h2>Edge by instrument</h2><span class="meta">Expectancy per trade</span></div>{edge_bars(instruments)}</div>
          </section>
          <section class="card">
            <div class="card-title"><h2>Recent trades</h2><a class="btn btn-sm" href="/trades{escape(query_string({"account_id": account_param, "range": period.code}))}">View all</a></div>
            {trade_table(recent, today, show_account=selected is None)}
          </section>
        """
        return shell("Dashboard", "dashboard", body, selected.name if selected else "All accounts", user.name or user.email)
    except SQLAlchemyError as exc:
        logger.exception("dashboard failed")
        return shell("Dashboard", "dashboard", empty_state("Dashboard unavailable", type(exc).__name__))
    finally:
        session.close()


@app.get("/", response_class=HTMLResponse)
def home(
    account_id: str | None = Query(default=None),
    range_code: str | None = Query(default=None, alias="range"),
    month: str | None = Query(default=None),
) -> str:
    # Visitors see the landing page; signed-in users go straight to their dashboard.
    if CURRENT_USER_ID.get() is None:
        return landing_page()
    return render_dashboard(account_id, range_code, month)


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(
    account_id: str | None = Query(default=None),
    range_code: str | None = Query(default=None, alias="range"),
    month: str | None = Query(default=None),
) -> str:
    return render_dashboard(account_id, range_code, month)


@app.get("/settings")
def settings_home() -> RedirectResponse:
    return RedirectResponse("/settings/import", status_code=status.HTTP_303_SEE_OTHER)


def account_card(account: Account, trades: list[Trade], flows: list[CashTransaction], today: date) -> str:
    """One trading account: balance (starting + closed P&L + cash flows), results, prop-firm limits."""
    closed = [trade for trade in trades if trade.status == TradeStatus.CLOSED]
    result = book.stats(closed)
    deposits = sum((flow.signed_amount for flow in flows), Decimal("0"))
    balance = account.starting_balance + result.net_pnl + deposits
    open_count = sum(1 for trade in trades if trade.status == TradeStatus.OPEN)
    limits = ""
    if account.max_daily_loss:
        today_pnl = book.daily_pnl(closed).get(today, Decimal("0"))
        used = min(max(-today_pnl, Decimal("0")) / account.max_daily_loss * 100, Decimal("100"))
        limits += f"""<div class="limit"><div class="card-title" style="margin:10px 0 6px"><span class="meta">Daily loss limit used today</span>
          <span class="meta">{number(used, "%")} of {money(account.max_daily_loss)}</span></div><div class="bar"><span style="width:{used}%"></span></div></div>"""
    if account.max_total_loss:
        drawdown = max(account.starting_balance - balance, Decimal("0"))
        used = min(drawdown / account.max_total_loss * 100, Decimal("100"))
        limits += f"""<div class="limit"><div class="card-title" style="margin:10px 0 6px"><span class="meta">Max loss limit used</span>
          <span class="meta">{number(used, "%")} of {money(account.max_total_loss)}</span></div><div class="bar"><span style="width:{used}%"></span></div></div>"""
    return f"""<article class="card">
      <div class="card-title"><div><h2>{escape(account.name)}</h2><span class="meta">{escape(account.broker)} · {escape(account.account_type.value.replace("_", " ").title())} · {escape(account.currency)}</span></div>
        <a class="btn btn-sm" href="/dashboard?account_id={escape(account.id)}">Dashboard</a></div>
      <div class="kpi-row" style="margin:0">
        {kpi("Balance", money(balance), "", f"Started at {money(account.starting_balance)}")}
        {kpi("Net P&L", money(result.net_pnl, signed=True), tone(result.net_pnl), f"{result.trades} closed trades")}
        {kpi("Win rate", number(result.win_rate, "%"), "", f"{open_count} open")}
        {kpi("Deposits − withdrawals", money(deposits, signed=True), tone(deposits))}
      </div>
      {limits}
    </article>"""


@app.get("/dashboard/portfolio", response_class=HTMLResponse)
def portfolio() -> str:
    session = SessionLocal()
    try:
        user = current_user(session)
        accounts, _, trades = load_book(session, user, None)
        if not accounts:
            body = empty_state("No trading accounts yet", "Add a trading account to see its balance and results here.",
                               "/settings/accounts", "Add a trading account")
            return shell("Portfolio", "portfolio", body, "All accounts", user.name or user.email)
        flows = load_cash_flows(session, accounts)
        today = today_utc()
        closed = [trade for trade in trades if trade.status == TradeStatus.CLOSED]
        total = book.stats(closed)
        balance = (sum((account.starting_balance for account in accounts), Decimal("0")) + total.net_pnl
                   + sum((flow.signed_amount for flow in flows), Decimal("0")))
        cards = "".join(
            account_card(account, [trade for trade in trades if trade.account_id == account.id],
                         [flow for flow in flows if flow.account_id == account.id], today)
            for account in accounts
        )
        body = f"""
          <section class="kpi-row">
            {kpi("Total balance", money(balance), "", f"{len(accounts)} accounts")}
            {kpi("Net P&L", money(total.net_pnl, signed=True), tone(total.net_pnl), f"{total.trades} closed trades")}
            {kpi("Win rate", number(total.win_rate, "%"))}
            {kpi("Profit factor", number(total.profit_factor) if total.profit_factor is not None else "—")}
          </section>
          <section class="grid" style="grid-template-columns:repeat(auto-fit,minmax(380px,1fr));gap:14px">{cards}</section>"""
        return shell("Portfolio", "portfolio", body, "All accounts", user.name or user.email)
    finally:
        session.close()


TRADE_STATUS_TABS = (("All", None), ("Open", "OPEN"), ("Closed", "CLOSED"), ("Cancelled", "CANCELLED"))


@app.get("/trades", response_class=HTMLResponse)
def trades(
    status_filter: str | None = Query(default=None),
    account_id: str | None = Query(default=None),
    range_code: str | None = Query(default=None, alias="range"),
) -> str:
    session = SessionLocal()
    try:
        user = current_user(session)
        if user is None:
            return no_user_page("Trades", "trades")
        today = today_utc()
        period = book.resolve_period(range_code or "ALL", today)
        current_status = (status_filter or "").upper() or None
        accounts, selected, all_trades = load_book(session, user, account_id)
        account_param = selected.id if selected else None
        rows = [
            trade for trade in all_trades
            # open positions stay visible whatever the range: they are still live risk
            if (period.contains(book.opened_day(trade)) or trade.status == TradeStatus.OPEN)
            and (current_status is None or trade.status.value == current_status)
        ][:500]
        tabs = filter_tabs([
            (label, "/trades" + query_string({"status_filter": value, "account_id": account_param, "range": period.code}), value == current_status)
            for label, value in TRADE_STATUS_TABS
        ])
        closed = [trade for trade in rows if trade.status == TradeStatus.CLOSED]
        totals = book.stats(closed)
        summary_line = (
            f'<span class="muted small">{len(rows)} trades · net <b class="{tone(totals.net_pnl)}">{money(totals.net_pnl, signed=True)}</b>'
            f" · {number(totals.win_rate, '%')} win rate</span>"
        )
        body = f"""
          {filter_bar("/trades", accounts, account_param, period, {"status_filter": current_status})}
          <section class="card">
            <div class="toolbar">{tabs}{summary_line}</div>
            {trade_table(rows, today, show_account=selected is None)}
          </section>
        """
        return shell("Trades", "trades", body, selected.name if selected else "All accounts", user.name or user.email)
    finally:
        session.close()


LOG_TRADE_SCRIPT = """
(function () {
  var form = document.getElementById("log-trade");
  if (!form) return;
  var opened = form.elements.opened_at;
  if (opened && !opened.value) {
    var now = new Date(); now.setMinutes(now.getMinutes() - now.getTimezoneOffset());
    opened.value = now.toISOString().slice(0, 16);
  }
  function num(name) { var v = parseFloat(form.elements[name].value); return isNaN(v) ? null : v; }
  function fmt(v, d) { return v.toLocaleString(undefined, {minimumFractionDigits: d, maximumFractionDigits: d}); }
  function usd(v) { return (v < 0 ? "-$" : "$") + fmt(Math.abs(v), 2); }
  function set(id, text, cls) { var el = document.getElementById(id); el.textContent = text; if (cls !== undefined) el.className = cls; }
  function update() {
    var inst = form.elements.instrument_id.selectedOptions[0];
    var acct = form.elements.account_id.selectedOptions[0];
    var pv = inst ? parseFloat(inst.dataset.pv || "1") : 1;
    var balance = acct ? parseFloat(acct.dataset.balance || "0") : 0;
    var long = form.elements.direction.value !== "SHORT";
    var entry = num("entry_price"), stop = num("planned_stop"), target = num("planned_target");
    var qty = num("quantity"), exit = num("exit_price");
    var fees = (num("entry_fees") || 0) + (num("exit_fees") || 0);
    var warn = [];
    var risk = null, reward = null;
    if (entry !== null && stop !== null && qty) {
      if (long ? stop >= entry : stop <= entry) warn.push("Stop is on the wrong side of entry for a " + (long ? "long" : "short") + ".");
      risk = Math.abs(entry - stop) * qty * pv;
    }
    if (entry !== null && target !== null && qty) {
      if (long ? target <= entry : target >= entry) warn.push("Target is on the wrong side of entry.");
      reward = Math.abs(target - entry) * qty * pv;
    }
    set("pv-risk", risk !== null ? usd(risk) : "—");
    set("pv-risk-pct", risk !== null && balance ? fmt(risk / balance * 100, 2) + "%" : "—");
    set("pv-reward", reward !== null ? usd(reward) : "—");
    set("pv-rr", risk && reward !== null ? fmt(reward / risk, 2) + "R" : "—", "big");
    var closed = exit !== null && entry !== null && qty;
    if (closed) {
      var pnl = (long ? exit - entry : entry - exit) * qty * pv - fees;
      set("pv-pnl", usd(pnl), pnl > 0 ? "positive" : pnl < 0 ? "negative" : "");
      set("pv-r", risk ? fmt(pnl / risk, 2) + "R" : "—", pnl > 0 ? "positive" : pnl < 0 ? "negative" : "");
    } else {
      set("pv-pnl", "—", ""); set("pv-r", "—", "");
    }
    set("pv-status", closed ? "Saved as closed" : "Saved as open position");
    var w = document.getElementById("pv-warn"); w.textContent = warn.join(" "); w.hidden = !warn.length;
  }
  form.addEventListener("input", update);
  form.addEventListener("change", update);
  update();
})();
"""


@app.get("/trades/new", response_class=HTMLResponse)
def new_trade(account_id: str | None = Query(default=None)) -> str:
    session = SessionLocal()
    try:
        user = current_user(session)
        if user is None:
            return no_user_page("Log trade", "trades")
        accounts = session.scalars(
            select(Account).where(Account.user_id == user.id, Account.archived_at.is_(None)).order_by(Account.created_at)
        ).all()
        if not accounts:
            return shell("Log trade", "trades", empty_state("Create a trading account first", "Trades are logged against a trading account, such as your broker account or a prop-firm challenge.", "/settings/accounts", "Create trading account"), "Log trade", user.name or user.email)
        instruments = session.scalars(select(Instrument).order_by(Instrument.symbol).limit(500)).all()
        if not instruments:
            return shell("Log trade", "trades", empty_state("Add the instruments you trade", "Add each symbol once, with its point value, so P&L and risk are calculated correctly.", "/settings/instruments", "Add instruments"), "Log trade", user.name or user.email)
        setup_tags = session.scalars(
            select(Trade.setup_tag).where(Trade.user_id == user.id, Trade.setup_tag.is_not(None)).distinct().limit(50)
        ).all()
    finally:
        session.close()
    account_options = "".join(
        f'<option value="{escape(account.id)}" data-balance="{account.starting_balance}" {"selected" if account.id == account_id else ""}>'
        f"{escape(account.name)} · {escape(account.currency)}</option>"
        for account in accounts
    )
    instrument_options = "".join(
        f'<option value="{escape(instrument.id)}" data-pv="{instrument.point_value or 1}">'
        f"{escape(instrument.symbol)} — {escape(instrument.name)}</option>"
        for instrument in instruments
    )
    setup_list = "".join(f'<option value="{escape(tag)}"></option>' for tag in sorted(setup_tags))
    timeframes = "".join(f"<option>{tf}</option>" for tf in ("", "1m", "5m", "15m", "1h", "4h", "D", "W"))
    decimal_input = 'type="number" step="any" inputmode="decimal"'
    body = f"""
      <form id="log-trade" class="form-layout" method="post" action="/trades/new">
        <section class="card">
          <div class="form-section">
            <h3>What did you trade</h3>
            <div class="form-row">
              <div class="field"><label for="f-inst">Instrument</label><select id="f-inst" name="instrument_id" required>{instrument_options}</select></div>
              <div class="field"><label for="f-acct">Account</label><select id="f-acct" name="account_id" required>{account_options}</select></div>
            </div>
            <div class="field" style="margin-top:12px"><label>Side</label>
              <div class="seg" role="radiogroup" aria-label="Side">
                <input class="long" type="radio" id="f-long" name="direction" value="LONG" checked /><label for="f-long">▲ Long</label>
                <input class="short" type="radio" id="f-short" name="direction" value="SHORT" /><label for="f-short">▼ Short</label>
              </div>
            </div>
          </div>
          <div class="form-section">
            <h3>Plan</h3>
            <div class="form-row">
              <div class="field"><label for="f-entry">Entry</label><input id="f-entry" name="entry_price" {decimal_input} required /></div>
              <div class="field"><label for="f-stop">Stop</label><input id="f-stop" name="planned_stop" {decimal_input} /></div>
              <div class="field"><label for="f-target">Target</label><input id="f-target" name="planned_target" {decimal_input} /></div>
              <div class="field"><label for="f-qty">Size</label><input id="f-qty" name="quantity" {decimal_input} min="0" required /></div>
            </div>
            <div class="form-row" style="margin-top:12px">
              <div class="field"><label for="f-opened">Opened ({escape(user.timezone or "UTC")})</label><input id="f-opened" type="datetime-local" name="opened_at" required /></div>
              <div class="field"><label for="f-efee">Entry fees</label><input id="f-efee" name="entry_fees" {decimal_input} value="0" /></div>
            </div>
          </div>
          <div class="form-section">
            <h3>Outcome <span class="muted" style="text-transform:none;letter-spacing:0;font-weight:500">— leave blank if still open</span></h3>
            <div class="form-row">
              <div class="field"><label for="f-exit">Exit</label><input id="f-exit" name="exit_price" {decimal_input} /></div>
              <div class="field"><label for="f-closed">Closed ({escape(user.timezone or "UTC")})</label><input id="f-closed" type="datetime-local" name="closed_at" /></div>
              <div class="field"><label for="f-xfee">Exit fees</label><input id="f-xfee" name="exit_fees" {decimal_input} value="0" /></div>
            </div>
          </div>
          <div class="form-section">
            <h3>Context</h3>
            <div class="form-row">
              <div class="field"><label for="f-setup">Setup</label><input id="f-setup" name="setup_tag" list="setup-tags" placeholder="ORB" /><datalist id="setup-tags">{setup_list}</datalist></div>
              <div class="field"><label for="f-tf">Timeframe</label><select id="f-tf" name="timeframe">{timeframes}</select></div>
            </div>
            <div class="field" style="margin-top:12px"><label for="f-notes">Notes</label><textarea id="f-notes" name="notes" placeholder="Thesis, execution, what you'd do differently"></textarea></div>
          </div>
        </section>
        <aside class="card preview" aria-live="polite">
          <div class="label">Planned reward : risk</div>
          <div id="pv-rr" class="big">—</div>
          <dl>
            <dt>Risk</dt><dd id="pv-risk">—</dd>
            <dt>Risk of account</dt><dd id="pv-risk-pct">—</dd>
            <dt>Potential reward</dt><dd id="pv-reward">—</dd>
            <dt>Net P&amp;L</dt><dd id="pv-pnl">—</dd>
            <dt>Result</dt><dd id="pv-r">—</dd>
          </dl>
          <p id="pv-warn" class="hint warn" hidden></p>
          <p id="pv-status" class="hint">Saved as open position</p>
          <button class="btn btn-primary" type="submit" style="width:100%;margin-top:14px">Save trade</button>
        </aside>
      </form>
      <script>{LOG_TRADE_SCRIPT}</script>
    """
    return shell("Log trade", "trades", body, "Manual entry", user.name or user.email)


def form_decimal(value: str) -> Decimal | None:
    try:
        return Decimal(value.strip()) if value and value.strip() else None
    except ArithmeticError:
        raise HTTPException(status_code=400, detail=f"Invalid number: {value!r}") from None


def form_datetime(value: str) -> datetime | None:
    if not value or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date: {value!r}") from None
    # The form's datetime-local fields carry no timezone: they are the user's local time.
    return localtime.from_local_input(parsed)


@app.post("/trades/new")
def create_trade_from_form(
    account_id: str = Form(...),
    instrument_id: str = Form(...),
    direction: Direction = Form(...),
    opened_at: str = Form(...),
    entry_price: str = Form(...),
    quantity: str = Form(...),
    trade_status: TradeStatus | None = Form(default=None),
    setup_tag: str = Form(""),
    timeframe: str = Form(""),
    planned_stop: str = Form(""),
    planned_target: str = Form(""),
    entry_fees: str = Form("0"),
    closed_at: str = Form(""),
    exit_price: str = Form(""),
    exit_fees: str = Form("0"),
    notes: str = Form(""),
) -> RedirectResponse:
    entry = form_decimal(entry_price)
    size = form_decimal(quantity)
    if entry is None or size is None or size <= 0:
        raise HTTPException(status_code=400, detail="Entry price and a positive size are required")
    stop = form_decimal(planned_stop)
    target = form_decimal(planned_target)
    exit_value = form_decimal(exit_price)
    opened = form_datetime(opened_at)
    if opened is None:
        raise HTTPException(status_code=400, detail="Opened time is required")
    closed = form_datetime(closed_at)
    # An exit price means the trade is closed, unless the caller explicitly says otherwise.
    resolved_status = trade_status or (TradeStatus.CLOSED if exit_value is not None else TradeStatus.OPEN)
    if resolved_status == TradeStatus.CLOSED:
        if exit_value is None:
            raise HTTPException(status_code=400, detail="A closed trade needs an exit price")
        closed = closed or datetime.now(UTC)
        if closed < opened:
            raise HTTPException(status_code=400, detail="Closed time is before opened time")
    session = SessionLocal()
    try:
        user = current_user(session)
        if user is None:
            raise HTTPException(status_code=400, detail="No demo user available")
        account = session.scalar(select(Account).where(Account.id == account_id, Account.user_id == user.id))
        instrument = session.get(Instrument, instrument_id)
        if account is None or instrument is None:
            raise HTTPException(status_code=400, detail="Invalid account or instrument")
        trade = Trade(
            account_id=account.id,
            user_id=user.id,
            instrument_id=instrument.id,
            direction=direction,
            status=resolved_status,
            opened_at=opened,
            closed_at=closed if resolved_status == TradeStatus.CLOSED else None,
            setup_tag=setup_tag.strip() or None,
            timeframe=timeframe or None,
            planned_entry=entry,
            planned_stop=stop,
            planned_target=target,
            planned_rr=compute_planned_rr(direction, entry, stop, target),
            commissions=Decimal("0"),
            notes=notes or None,
            mistake_flags=[],
            tags=[],
            is_reviewed=False,
        )
        trade.executions = [
            Execution(type=ExecutionType.ENTRY, executed_at=opened, price=entry, quantity=size, fees=form_decimal(entry_fees) or Decimal("0"))
        ]
        if resolved_status == TradeStatus.CLOSED and exit_value is not None and closed is not None:
            trade.executions.append(
                Execution(type=ExecutionType.EXIT, executed_at=closed, price=exit_value, quantity=size, fees=form_decimal(exit_fees) or Decimal("0"))
            )
        session.add(trade)
        session.flush()
        computed = compute_trade_metrics(trade, trade.executions, instrument)
        session.add(
            TradeMetrics(
                trade_id=trade.id,
                average_entry=computed.average_entry,
                average_exit=computed.average_exit,
                total_quantity=computed.total_quantity,
                realized_pnl=computed.net_pnl,
                pnl_percent=computed.pnl_percent,
                r_multiple=computed.r_multiple,
                holding_period_seconds=computed.holding_period_seconds,
            )
        )
        if trade.status == TradeStatus.CLOSED:
            rebuild_daily_stats(session, account.id)
            rebuild_equity_curve(session, account)
        session.commit()
        return RedirectResponse(f"/trades/{trade.id}", status_code=status.HTTP_303_SEE_OTHER)
    finally:
        session.close()


@app.get("/trades/{trade_id}", response_class=HTMLResponse)
def trade_detail(trade_id: str) -> str:
    session = SessionLocal()
    try:
        user = current_user(session)
        if user is None:
            return no_user_page("Trade", "trades")
        trade = session.scalar(
            select(Trade)
            .options(selectinload(Trade.metrics), selectinload(Trade.instrument), selectinload(Trade.account), selectinload(Trade.executions))
            .where(Trade.id == trade_id, Trade.user_id == user.id)
        )
        if trade is None:
            return shell("Trade", "trades", empty_state("Trade not found", "It may have been deleted.", "/trades", "Back to trades"), "Trade", user.name or user.email)
        journals = session.scalars(
            select(JournalEntry)
            .where(JournalEntry.user_id == user.id, (JournalEntry.trade_id == trade.id) | (JournalEntry.id == trade.journal_entry_id))
            .order_by(JournalEntry.date.desc())
        ).all()
        attachments = session.scalars(select(Attachment).where(Attachment.trade_id == trade.id).order_by(Attachment.created_at)).all()
        today = today_utc()
        metrics = trade.metrics
        closed = metrics is not None and metrics.average_exit is not None
        net = metrics.realized_pnl if metrics else Decimal("0")
        r_value = metrics.r_multiple if metrics else None
        size = metrics.total_quantity if metrics else Decimal("0")
        entry = metrics.average_entry if metrics and metrics.average_entry else trade.planned_entry
        point_value = trade.instrument.point_value or Decimal("1")
        planned_risk = abs(entry - trade.planned_stop) * size * point_value if entry is not None and trade.planned_stop is not None else None
        tid = escape(trade.id)
        share_button = (
            f'<form method="post" action="/trades/{tid}/share" style="margin:0"><button class="btn" type="submit">Share</button></form>'
            if settings.feature_community
            else ""
        )
        meta = " · ".join(
            escape(part) for part in (trade.account.name, trade.setup_tag or "No setup", trade.timeframe or "", short_datetime(trade.opened_at, today)) if part
        )
        journal_items = "".join(
            f'<li><a href="/journal/{escape(entry_row.id)}">{escape(entry_row.title)}</a> <span class="muted small">{escape(entry_row.date.isoformat())}</span></li>'
            for entry_row in journals
        )
        thumbs = "".join(
            f'<a href="{escape(link)}" target="_blank" rel="noopener"><img src="{escape(link)}" alt="{escape(row.file_name)}" loading="lazy" /></a>'
            for row in attachments
            for link in (storage.display_url(row.url),)
        )
        flags = "".join(f'<span class="badge badge-review">{escape(flag)}</span> ' for flag in trade.mistake_flags or [])
        body = f"""
          <div class="trade-head">
            <span class="sym">{escape(trade.instrument.symbol)}</span>
            {side_badge(trade.direction.value)} {status_badge(trade.status.value)}
            <span class="meta">{meta}</span>
            <div class="actions">
              <a class="btn" href="/trades/{tid}/journal">Write review</a>
              {share_button}
            </div>
          </div>
          <section class="kpi-row">
            {kpi("Net P&L", money(net, signed=True) if closed else "Open", tone(net) if closed else "", f"{number(metrics.pnl_percent, '%')} on capital" if closed and metrics.pnl_percent is not None else "Unrealised")}
            {kpi("Result", number(r_value, "R") if closed and r_value is not None else "—", tone(r_value) if closed else "", f"Risked {money(planned_risk)}" if planned_risk is not None else "No stop set")}
            {kpi("Planned R:R", number(trade.planned_rr, "R") if trade.planned_rr is not None else "—", "", "Target vs stop")}
            {kpi("Hold time", hold_time(metrics.holding_period_seconds if metrics else None), "", "Entry to exit")}
            {kpi("Size", number(size).rstrip("0").rstrip("."), "", f"Point value {number(point_value).rstrip('0').rstrip('.')}")}
          </section>
          <section class="dash-grid">
            <div class="card">
              <div class="card-title"><h2>Price ladder</h2><span class="meta">Plan vs actual</span></div>
              {price_ladder(trade.direction, entry, trade.planned_stop, trade.planned_target, metrics.average_exit if metrics else None)}
            </div>
            <div class="card">
              <div class="card-title"><h2>Executions</h2><span class="meta">{len(trade.executions)} fills</span></div>
              {execution_timeline(trade.executions, today)}
            </div>
          </section>
          <section class="dash-grid">
            <div class="card">
              <div class="card-title"><h2>Notes</h2></div>
              <p style="white-space:pre-wrap;margin:0">{escape(trade.notes or "No notes yet.")}</p>
              {f'<div style="margin-top:12px">{flags}</div>' if flags else ""}
            </div>
            <div class="card">
              <div class="card-title"><h2>Journal and screenshots</h2><a class="btn btn-sm" href="/settings/uploads">Upload</a></div>
              {f'<ul style="margin:0 0 12px;padding-left:18px">{journal_items}</ul>' if journal_items else f'<p class="muted">No review yet. <a href="/trades/{tid}/journal">Write one</a> while it is fresh.</p>'}
              {f'<div class="thumbs">{thumbs}</div>' if thumbs else ""}
            </div>
          </section>
        """
        return shell(f"{trade.instrument.symbol} {trade.direction.value.lower()}", "trades", body, trade.account.name, user.name or user.email)
    finally:
        session.close()


@app.get("/positions", response_class=HTMLResponse)
def positions(account_id: str | None = Query(default=None)) -> str:
    session = SessionLocal()
    try:
        user = current_user(session)
        accounts = list(session.scalars(
            select(Account).where(Account.user_id == user.id, Account.archived_at.is_(None)).order_by(Account.created_at)
        ).all())
        selected = next((account for account in accounts if account.id == account_id), None)
        query = (
            select(Trade)
            .options(selectinload(Trade.metrics), selectinload(Trade.instrument), selectinload(Trade.account))
            .where(Trade.user_id == user.id, Trade.status == TradeStatus.OPEN)
            .order_by(Trade.opened_at.desc())
        )
        if selected is not None:
            query = query.where(Trade.account_id == selected.id)
        trades = session.scalars(query).all()
        rows = ""
        total_risk = Decimal("0")
        largest_risk = Decimal("0")
        for trade in trades:
            metrics = trade.metrics
            entry = metrics.average_entry if metrics else trade.planned_entry or Decimal("0")
            quantity = metrics.total_quantity if metrics else Decimal("0")
            risk = abs(entry - trade.planned_stop) * quantity if trade.planned_stop is not None else Decimal("0")
            total_risk += risk
            largest_risk = max(largest_risk, risk)
            rows += f"""
              <tr>
                <td><a class="pill" href="/trades/{escape(trade.id)}">{escape(trade.instrument.symbol)}</a></td>
                <td>{escape(trade.account.name)}</td>
                <td>{side_badge(trade.direction.value)}</td>
                <td>{money(entry)}</td>
                <td>{number(quantity)}</td>
                <td>{money(risk)}</td>
                <td>{escape(localtime.fmt(trade.opened_at))}</td>
                <td>
                  <form method="post" action="/positions/{escape(trade.id)}/close" style="display:flex;gap:8px;align-items:center">
                    <input aria-label="Exit price" name="exit_price" type="number" step="0.00000001" placeholder="Exit" required style="max-width:110px" />
                    <input aria-label="Exit fees" name="fees" type="number" step="0.01" value="0" style="max-width:84px" />
                    <button type="submit">Close</button>
                  </form>
                </td>
              </tr>
            """
        if not rows:
            rows = '<tr><td colspan="8" class="muted">No open positions. Open trades will appear here.</td></tr>'
        scope = selected.name if selected else "All accounts"
        body = f"""
          <div class="filter-bar">{account_picker("/positions", accounts, selected.id if selected else None)}</div>
          <section class="grid kpis">
            {kpi_card("Open Positions", str(len(trades)), "neutral", scope)}
            {kpi_card("Total Planned Risk", money(total_risk), "amber" if total_risk else "neutral", "Entry vs stop")}
            {kpi_card("Largest Risk", money(largest_risk), "neutral", "Single position")}
            {kpi_card("Quick Close", "Enabled", "positive", "Manual exit price")}
          </section>
          <section class="card" style="margin-top:16px">
            <div class="section-head"><div><div class="label">Open Positions</div><h2 style="margin:4px 0 0">Live Risk Board</h2></div><a class="pill primary" href="/trades/new">New Trade</a></div>
            <table><thead><tr><th>Symbol</th><th>Account</th><th>Side</th><th>Entry</th><th>Size</th><th>Risk</th><th>Opened</th><th>Action</th></tr></thead><tbody>{rows}</tbody></table>
          </section>
        """
        return shell("Positions", "positions", body, scope, user.name or user.email)
    finally:
        session.close()


@app.post("/positions/{trade_id}/close")
def close_position(
    trade_id: str,
    exit_price: Decimal = Form(...),
    fees: Decimal = Form(default=Decimal("0")),
) -> RedirectResponse:
    session = SessionLocal()
    try:
        user = current_user(session)
        if user is None:
            raise HTTPException(status_code=400, detail="No demo user available")
        trade = session.scalar(
            select(Trade)
            .options(selectinload(Trade.metrics), selectinload(Trade.instrument), selectinload(Trade.executions))
            .where(Trade.id == trade_id, Trade.user_id == user.id, Trade.status == TradeStatus.OPEN)
        )
        if trade is None:
            raise HTTPException(status_code=404, detail="Open trade not found")
        quantity = trade.metrics.total_quantity if trade.metrics else sum(
            (execution.quantity for execution in trade.executions if execution.type in {ExecutionType.ENTRY, ExecutionType.ADD}),
            Decimal("0"),
        )
        now = datetime.now(UTC)
        trade.status = TradeStatus.CLOSED
        trade.closed_at = now
        trade.executions.append(
            Execution(
                type=ExecutionType.EXIT,
                executed_at=now,
                price=exit_price,
                quantity=quantity,
                fees=fees,
            )
        )
        computed = compute_trade_metrics(trade, trade.executions, trade.instrument)
        if trade.metrics is None:
            trade.metrics = TradeMetrics(trade_id=trade.id, average_entry=computed.average_entry, total_quantity=computed.total_quantity, realized_pnl=computed.net_pnl)
        trade.metrics.average_entry = computed.average_entry
        trade.metrics.average_exit = computed.average_exit
        trade.metrics.total_quantity = computed.total_quantity
        trade.metrics.realized_pnl = computed.net_pnl
        trade.metrics.pnl_percent = computed.pnl_percent
        trade.metrics.r_multiple = computed.r_multiple
        trade.metrics.holding_period_seconds = computed.holding_period_seconds
        account = session.get(Account, trade.account_id)
        if account is not None:
            rebuild_daily_stats(session, account.id)
            rebuild_equity_curve(session, account)
        session.commit()
        return RedirectResponse("/positions", status_code=status.HTTP_303_SEE_OTHER)
    finally:
        session.close()


def tiptap_doc(text: str) -> dict[str, object]:
    paragraphs = [line.strip() for line in text.splitlines() if line.strip()]
    if not paragraphs:
        paragraphs = ["No journal content recorded."]
    return {
        "type": "doc",
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": paragraph}]}
            for paragraph in paragraphs
        ],
    }


def plain_text_from_tiptap(content: dict[str, object]) -> str:
    parts: list[str] = []
    for node in content.get("content", []):
        if not isinstance(node, dict):
            continue
        for child in node.get("content", []):
            if isinstance(child, dict) and isinstance(child.get("text"), str):
                parts.append(child["text"])
    return "\n".join(parts)


@app.get("/journal", response_class=HTMLResponse)
def journal_feed(entry_type: str | None = Query(default=None), day: date | None = Query(default=None)) -> str:
    session = SessionLocal()
    try:
        user = current_user(session)
        if user is None:
            return shell("Journal", "journal", '<section class="empty" style="margin-top:18px">No users found. Run seed data first.</section>')
        query = select(JournalEntry).where(JournalEntry.user_id == user.id).order_by(JournalEntry.date.desc(), JournalEntry.created_at.desc())
        if entry_type:
            query = query.where(JournalEntry.type == JournalType(entry_type))
        if day:
            query = query.where(JournalEntry.date == day)
        entries = session.scalars(query.limit(100)).all()
        day_panel = ""
        if day:
            day_trades = [
                trade for trade in session.scalars(
                    select(Trade)
                    .options(selectinload(Trade.metrics), selectinload(Trade.instrument), selectinload(Trade.account))
                    .where(Trade.user_id == user.id)
                    .order_by(Trade.opened_at)
                ).all()
                if book.opened_day(trade) == day or book.closed_day(trade) == day
            ]
            day_net = sum((book.pnl(trade) for trade in day_trades if book.closed_day(trade) == day), Decimal("0"))
            day_panel = f"""<section class="card" style="margin-top:16px">
              <div class="card-title"><h2>{day.strftime("%A, %B")} {day.day}</h2>
                <span class="meta">Closed P&amp;L <b class="{tone(day_net)}">{money(day_net, signed=True)}</b></span></div>
              {trade_table(day_trades, today_utc(), empty='<p class="muted">No trades on this day.</p>')}
              <div class="actions" style="margin-top:12px"><a class="btn btn-primary" href="/journal/new?entry_type=DAILY&amp;entry_date={day.isoformat()}">Write daily journal</a>
              <a class="btn" href="/journal">All entries</a></div>
            </section>"""
        filters = "".join(
            f'<a class="pill {"primary" if entry_type == item.value else ""}" href="/journal?entry_type={item.value}">{item.value.replace("_", " ").title()}</a>'
            for item in JournalType
        )
        rows = ""
        for entry in entries:
            preview = plain_text_from_tiptap(entry.content)[:180]
            rows += f"""
              <article class="card">
                <div class="section-head">
                  <div><div class="label">{escape(entry.type.value.replace("_", " "))}</div><h2 style="margin:4px 0 0"><a class="pill" href="/journal/{escape(entry.id)}">{escape(entry.title)}</a></h2></div>
                  <span class="badge">{escape(entry.date.isoformat())}</span>
                </div>
                <p class="muted">{escape(preview or "No preview available.")}</p>
                <div class="actions"><span class="badge">Mood {entry.mood or "N/A"}</span><span class="badge">{escape(entry.market_condition.value if entry.market_condition else "No condition")}</span></div>
              </article>
            """
        if not rows:
            rows = '<section class="empty">No journal entries match this filter.</section>'
        body = f"""
          {day_panel}
          <div class="actions" style="margin-top:16px"><a class="pill primary" href="/journal/new">New Journal</a><a class="pill" href="/journal">All</a>{filters}</div>
          <section class="grid" style="margin-top:16px">{rows}</section>
        """
        return shell("Journal", "journal", body, "Journal Feed", user.name or user.email)
    except (SQLAlchemyError, ValueError) as exc:
        return shell("Journal", "journal", f'<section class="empty" style="margin-top:18px">{escape(str(exc))}</section>')
    finally:
        session.close()


@app.get("/journal/new", response_class=HTMLResponse)
def new_journal(
    trade_id: str | None = Query(default=None),
    entry_type: str = Query(default="DAILY"),
    entry_date: date | None = Query(default=None),
) -> str:
    session = SessionLocal()
    try:
        user = current_user(session)
        if user is None:
            return shell("New Journal", "journal", '<section class="empty" style="margin-top:18px">No users found. Run seed data first.</section>')
        trade = None
        if trade_id:
            trade = session.scalar(select(Trade).options(selectinload(Trade.instrument)).where(Trade.id == trade_id, Trade.user_id == user.id))
        type_options = "".join(
            f'<option value="{item.value}" {"selected" if item.value == entry_type else ""}>{item.value.replace("_", " ").title()}</option>'
            for item in JournalType
        )
        condition_options = '<option value="">Not specified</option>' + "".join(
            f'<option value="{item.value}">{item.value.replace("_", " ").title()}</option>' for item in MarketCondition
        )
        title = f"{trade.instrument.symbol} trade review" if trade else ""
        prompt = (
            "Pre-trade thesis:\nExecution notes:\nPost-trade review:\nWhat would I improve next time?"
            if trade
            else "Day summary:\nKey decisions:\nWhat went well:\nWhat needs improvement:\nTomorrow's plan:"
        )
        body = f"""
          <form class="card" style="margin-top:16px" method="post" action="/journal/new">
            <input type="hidden" name="trade_id" value="{escape(trade_id or "")}" />
            <div class="section-head"><div><div class="label">Structured Reflection</div><h2 style="margin:4px 0 0">New Journal Entry</h2></div><button class="primary" type="submit">Save Journal</button></div>
            <div class="form-grid">
              <div class="field"><label>Type</label><select name="entry_type">{type_options}</select></div>
              <div class="field"><label>Date</label><input type="date" name="entry_date" value="{(entry_date or date.today()).isoformat()}" required /></div>
              <div class="field"><label>Title</label><input name="title" value="{escape(title)}" required /></div>
              <div class="field"><label>Mood</label><select name="mood"><option value="">N/A</option><option>1</option><option>2</option><option>3</option><option>4</option><option>5</option></select></div>
              <div class="field"><label>Market Condition</label><select name="market_condition">{condition_options}</select></div>
              <div class="field"><label>Key Lessons</label><input name="key_lessons" placeholder="Separate lessons with semicolons" /></div>
            </div>
            <div class="field" style="margin-top:14px"><label>Journal Content</label><textarea name="content" required>{escape(prompt)}</textarea></div>
          </form>
        """
        return shell("New Journal", "journal", body, "Journal", user.name or user.email)
    finally:
        session.close()


@app.post("/journal/new")
def create_journal_from_form(
    entry_type: JournalType = Form(...),
    entry_date: date = Form(...),
    title: str = Form(...),
    content: str = Form(...),
    trade_id: str = Form(""),
    mood: str = Form(""),
    market_condition: str = Form(""),
    key_lessons: str = Form(""),
) -> RedirectResponse:
    session = SessionLocal()
    try:
        user = current_user(session)
        if user is None:
            raise HTTPException(status_code=400, detail="No demo user available")
        linked_trade_id = trade_id or None
        if linked_trade_id:
            trade = session.scalar(select(Trade).where(Trade.id == linked_trade_id, Trade.user_id == user.id))
            if trade is None:
                raise HTTPException(status_code=404, detail="Trade not found")
        lessons = [lesson.strip() for lesson in key_lessons.split(";") if lesson.strip()][:5]
        entry = JournalEntry(
            user_id=user.id,
            type=entry_type,
            trade_id=linked_trade_id,
            date=entry_date,
            title=title,
            content=tiptap_doc(content),
            mood=int(mood) if mood else None,
            market_condition=MarketCondition(market_condition) if market_condition else None,
            key_lessons=lessons,
        )
        session.add(entry)
        session.flush()
        if linked_trade_id:
            trade = session.get(Trade, linked_trade_id)
            if trade is not None:
                trade.journal_entry_id = entry.id
        session.commit()
        return RedirectResponse(f"/journal/{entry.id}", status_code=status.HTTP_303_SEE_OTHER)
    finally:
        session.close()


@app.get("/trades/{trade_id}/journal")
def trade_journal_shortcut(trade_id: str) -> RedirectResponse:
    return RedirectResponse(f"/journal/new?trade_id={trade_id}&entry_type=TRADE_REVIEW", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/journal/{journal_id}", response_class=HTMLResponse)
def journal_detail(journal_id: str) -> str:
    session = SessionLocal()
    try:
        user = current_user(session)
        if user is None:
            return shell("Journal Entry", "journal", '<section class="empty" style="margin-top:18px">No users found.</section>')
        entry = session.scalar(select(JournalEntry).where(JournalEntry.id == journal_id, JournalEntry.user_id == user.id))
        if entry is None:
            return shell("Journal Entry", "journal", '<section class="empty" style="margin-top:18px">Journal entry not found.</section>', "Journal", user.name or user.email)
        lessons = "".join(f"<li>{escape(lesson)}</li>" for lesson in entry.key_lessons) or "<li>No key lessons recorded.</li>"
        body = f"""
          <section class="card" style="margin-top:16px">
            <div class="section-head">
              <div><div class="label">{escape(entry.type.value.replace("_", " "))}</div><h2 style="margin:4px 0 0">{escape(entry.title)}</h2></div>
              <span class="badge">{escape(entry.date.isoformat())}</span>
            </div>
            <div class="grid two-col">
              <div>
                <div class="label">Reflection</div>
                <p style="white-space:pre-wrap;line-height:1.65">{escape(plain_text_from_tiptap(entry.content))}</p>
              </div>
              <div>
                <div class="label">Context</div>
                <p class="muted">Mood: {entry.mood or "N/A"} · Market: {escape(entry.market_condition.value if entry.market_condition else "N/A")}</p>
                <div class="label">Key Lessons</div>
                <ul>{lessons}</ul>
                {f'<a class="pill" href="/trades/{escape(entry.trade_id)}">View Linked Trade</a>' if entry.trade_id else ""}
              </div>
            </div>
          </section>
        """
        return shell(entry.title, "journal", body, "Journal", user.name or user.email)
    finally:
        session.close()


ANALYTICS_TABS = (
    ("Overview", "/analytics", "overview"),
    ("Instruments", "/analytics/instruments", "instruments"),
    ("Setups", "/analytics/setups", "setups"),
    ("Time", "/analytics/time", "time"),
    ("Risk", "/analytics/risk", "risk"),
    ("Streaks", "/analytics/streaks", "streaks"),
)
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def analytics_nav(active: str, qs: str = "") -> str:
    return f'<div class="page-block">{filter_tabs([(label, href + qs, key == active) for label, href, key in ANALYTICS_TABS])}</div>'


def group_rows(groups: list[book.Group]) -> list[dict[str, object]]:
    return [
        {
            "key": group.key,
            "trades": group.stats.trades,
            "netPnl": group.stats.net_pnl,
            "winRate": group.stats.win_rate,
            "profitFactor": group.stats.profit_factor or Decimal("0"),
            "averageR": group.stats.expectancy_r or Decimal("0"),
        }
        for group in groups
    ]


def performance_table(rows: list[dict[str, object]], label: str) -> str:
    body = "".join(
        f"""<tr>
          <td>{escape(str(row["key"]))}</td>
          <td class="num">{row["trades"]}</td>
          <td class="num {tone(row["netPnl"])}">{money(row["netPnl"], signed=True)}</td>
          <td class="num">{number(row["winRate"], "%")}</td>
          <td class="num">{number(row["profitFactor"])}</td>
          <td class="num {tone(row["averageR"])}">{number(row["averageR"], "R")}</td>
        </tr>"""
        for row in rows
    ) or '<tr><td colspan="6" class="muted">No closed trades in this range.</td></tr>'
    return f"""
      <section class="card" style="margin-top:14px">
        <div class="card-title"><h2>By {escape(label.lower())}</h2><span class="meta">Ranked by expectancy</span></div>
        <div class="table-wrap"><table class="dense"><thead><tr><th>{escape(label)}</th><th class="num">Trades</th><th class="num">Net P&amp;L</th><th class="num">Win rate</th><th class="num">Profit factor</th><th class="num">Expectancy</th></tr></thead><tbody>{body}</tbody></table></div>
      </section>
    """


def daily_bars(daily: dict[date, Decimal]) -> str:
    rows = list(daily.items())[-30:]
    if not rows:
        return '<p class="muted">Daily P&amp;L bars appear once trades in this range are closed.</p>'
    max_abs = max(abs(value) for _, value in rows) or Decimal("1")
    return "".join(
        f"""<div class="daily-row" title="{day.isoformat()} {money(value, signed=True)}" style="margin:6px 0">
          <span class="small muted">{day.strftime("%b %d")}</span>
          <div class="bar-wrap"><div class="bar-fill {"pos" if value >= 0 else "neg"}" style="width:{abs(value) / max_abs * 100:.1f}%"></div></div>
          <span class="small num {tone(value)}" style="text-align:right">{money(value, signed=True)}</span>
        </div>"""
        for day, value in rows
    )


def heatmap(rows: list[dict[str, object]]) -> str:
    if not rows:
        return '<p class="muted">Heat map data appears once trades in this range are closed.</p>'
    max_abs = max(abs(Decimal(row["netPnl"])) for row in rows) or Decimal("1")
    cells = ""
    for row in rows:
        net_pnl = Decimal(row["netPnl"])
        strength = max(Decimal("0.15"), abs(net_pnl) / max_abs) * 100
        color = "var(--success)" if net_pnl >= 0 else "var(--danger)"
        cells += f"""<div class="card" style="padding:12px;background:color-mix(in srgb,{color} {strength * Decimal('0.35'):.0f}%,var(--bg-surface))">
          <div class="label">{escape(str(row["key"]))}</div>
          <div class="num {tone(net_pnl)}" style="font-weight:700">{money(net_pnl, signed=True)}</div>
          <div class="muted small">{row["trades"]} trades · {number(row["winRate"], "%")} win</div>
        </div>"""
    return f'<div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px">{cells}</div>'


def analytics_page(
    title: str,
    active: str,
    account_id: str | None,
    range_code: str | None,
    render,
) -> str:
    """Load the filtered book once and hand the closed trades in range to ``render``."""
    session = SessionLocal()
    try:
        user = current_user(session)
        if user is None:
            return no_user_page(title, "analytics")
        today = today_utc()
        period = book.resolve_period(range_code, today)
        accounts, selected, trades = load_book(session, user, account_id)
        current = closed_in(trades, period)
        scope_accounts = [selected] if selected else accounts
        flows = load_cash_flows(session, scope_accounts)
        opening = opening_balance(scope_accounts, trades, period, flows)
        curve = book.equity_curve(opening, current, cash_flows_in(flows, period))
        account_param = selected.id if selected else None
        qs = query_string({"account_id": account_param, "range": period.code})
        body = (
            filter_bar(f"/analytics/{active}" if active != "overview" else "/analytics", accounts, account_param, period)
            + analytics_nav(active, qs)
            + render(current, curve, selected, opening)
        )
        return shell(title, "analytics", body, selected.name if selected else "All accounts", user.name or user.email)
    except SQLAlchemyError as exc:
        logger.exception("analytics failed")
        return shell(title, "analytics", empty_state("Analytics unavailable", type(exc).__name__))
    finally:
        session.close()


def by_setup(trade: Trade) -> str:
    return trade.setup_tag or "Untagged"


def by_symbol(trade: Trade) -> str:
    return trade.instrument.symbol


@app.get("/analytics", response_class=HTMLResponse)
@app.get("/analytics/overview", response_class=HTMLResponse)
def analytics_overview(account_id: str | None = Query(default=None), range_code: str | None = Query(default=None, alias="range")) -> str:
    def render(current: list[Trade], curve: list[book.EquityPoint], selected: Account | None, opening: Decimal) -> str:
        return f"""
          {kpi_strip(book.stats(current), None, book.max_drawdown_pct(curve), drawdown_note(selected))}
          <section class="dash-grid">
            <div class="card"><div class="card-title"><h2>Equity curve</h2></div>{equity_chart(curve, opening)}</div>
            <div class="card"><div class="card-title"><h2>Edge by setup</h2></div>{edge_bars(book.by_expectancy(book.group_by(current, by_setup)))}</div>
          </section>
          {performance_table(group_rows(book.by_expectancy(book.group_by(current, by_symbol))), "Instrument")}
        """

    return analytics_page("Analytics", "overview", account_id, range_code, render)


@app.get("/analytics/instruments", response_class=HTMLResponse)
def analytics_instruments(account_id: str | None = Query(default=None), range_code: str | None = Query(default=None, alias="range")) -> str:
    return analytics_page(
        "By instrument", "instruments", account_id, range_code,
        lambda current, curve, selected, opening: performance_table(group_rows(book.by_expectancy(book.group_by(current, by_symbol))), "Instrument"),
    )


@app.get("/analytics/setups", response_class=HTMLResponse)
def analytics_setups(account_id: str | None = Query(default=None), range_code: str | None = Query(default=None, alias="range")) -> str:
    def render(current: list[Trade], curve: list[book.EquityPoint], selected: Account | None, opening: Decimal) -> str:
        groups = book.by_expectancy(book.group_by(current, by_setup))
        return f'<section class="card"><div class="card-title"><h2>Edge by setup</h2></div>{edge_bars(groups, limit=20)}</section>' + performance_table(group_rows(groups), "Setup")

    return analytics_page("By setup", "setups", account_id, range_code, render)


@app.get("/analytics/time", response_class=HTMLResponse)
def analytics_time(account_id: str | None = Query(default=None), range_code: str | None = Query(default=None, alias="range")) -> str:
    def render(current: list[Trade], curve: list[book.EquityPoint], selected: Account | None, opening: Decimal) -> str:
        weekday = sorted(book.group_by(current, lambda trade: localtime.local(trade.opened_at).strftime("%A")), key=lambda group: WEEKDAYS.index(group.key))
        hour = sorted(book.group_by(current, lambda trade: f"{localtime.local(trade.opened_at).hour:02d}:00"), key=lambda group: group.key)
        return f"""
          <section class="grid two-col">
            <div class="card"><div class="card-title"><h2>Day of week</h2></div>{heatmap(group_rows(weekday))}</div>
            <div class="card"><div class="card-title"><h2>Hour of day</h2><span class="meta">{escape(localtime.fmt(datetime.now(UTC), "%Z"))}</span></div>{heatmap(group_rows(hour))}</div>
          </section>
        """

    return analytics_page("By time", "time", account_id, range_code, render)


@app.get("/analytics/risk", response_class=HTMLResponse)
def analytics_risk(account_id: str | None = Query(default=None), range_code: str | None = Query(default=None, alias="range")) -> str:
    def render(current: list[Trade], curve: list[book.EquityPoint], selected: Account | None, opening: Decimal) -> str:
        daily = book.daily_pnl(current)
        stats = book.stats(current)
        return f"""
          <section class="kpi-row">
            {kpi("Max drawdown", number(book.max_drawdown_pct(curve), "%"), "negative" if book.max_drawdown_pct(curve) < 0 else "", drawdown_note(selected))}
            {kpi("Worst day", money(min(daily.values(), default=Decimal("0")), signed=True), "negative", f"{len([v for v in daily.values() if v < 0])} red days")}
            {kpi("Best day", money(max(daily.values(), default=Decimal("0")), signed=True), "positive", f"{len([v for v in daily.values() if v > 0])} green days")}
            {kpi("Worst trade", money(stats.worst_trade, signed=True), tone(stats.worst_trade), "Single trade")}
            {kpi("Best trade", money(stats.best_trade, signed=True), tone(stats.best_trade), "Single trade")}
          </section>
          <section class="dash-grid">
            <div class="card"><div class="card-title"><h2>Equity and drawdown</h2></div>{equity_chart(curve, opening)}</div>
            <div class="card"><div class="card-title"><h2>Daily P&amp;L</h2><span class="meta">Last 30 sessions</span></div>{daily_bars(daily)}</div>
          </section>
        """

    return analytics_page("Risk", "risk", account_id, range_code, render)


@app.get("/analytics/streaks", response_class=HTMLResponse)
def analytics_streaks(account_id: str | None = Query(default=None), range_code: str | None = Query(default=None, alias="range")) -> str:
    def render(current: list[Trade], curve: list[book.EquityPoint], selected: Account | None, opening: Decimal) -> str:
        streak = book.streaks(current)
        current_tone = "positive" if streak["currentKind"] == "win" else "negative" if streak["currentKind"] == "loss" else ""
        return f"""
          <section class="kpi-row" style="grid-template-columns:repeat(3,minmax(0,1fr))">
            {kpi("Longest win streak", str(streak["longestWinStreak"]), "positive", "Consecutive winners")}
            {kpi("Longest loss streak", str(streak["longestLossStreak"]), "negative", "Consecutive losers")}
            {kpi("Current streak", str(streak["currentStreak"]), current_tone, str(streak["currentKind"]).title())}
          </section>
          {performance_table(group_rows(book.by_expectancy(book.group_by(current, by_setup))), "Setup")}
        """

    return analytics_page("Streaks", "streaks", account_id, range_code, render)


def validate_upload(file: UploadFile) -> None:
    if not file.filename or "/" in file.filename or "\\" in file.filename:
        raise HTTPException(status_code=400, detail="Invalid file name")
    if file.content_type not in ALLOWED_UPLOAD_TYPES:
        raise HTTPException(status_code=400, detail="Unsupported upload type")


@app.get("/settings/uploads", response_class=HTMLResponse)
def uploads_page() -> str:
    session = SessionLocal()
    try:
        user = current_user(session)
        if user is None:
            return shell("Uploads", "settings", '<section class="empty" style="margin-top:18px">No users found. Run seed data first.</section>')
        trades = session.scalars(
            select(Trade).options(selectinload(Trade.instrument)).where(Trade.user_id == user.id).order_by(Trade.opened_at.desc()).limit(100)
        ).all()
        journals = session.scalars(
            select(JournalEntry).where(JournalEntry.user_id == user.id).order_by(JournalEntry.date.desc()).limit(100)
        ).all()
        trade_ids = [trade.id for trade in trades]
        journal_ids = [journal.id for journal in journals]
        if trade_ids and journal_ids:
            attachment_filter = Attachment.trade_id.in_(trade_ids) | Attachment.journal_entry_id.in_(journal_ids)
        elif trade_ids:
            attachment_filter = Attachment.trade_id.in_(trade_ids)
        elif journal_ids:
            attachment_filter = Attachment.journal_entry_id.in_(journal_ids)
        else:
            attachment_filter = Attachment.id == ""
        attachments = session.scalars(
            select(Attachment).where(attachment_filter).order_by(Attachment.created_at.desc()).limit(100)
        ).all()
        trade_options = '<option value="">No linked trade</option>' + "".join(
            f'<option value="{escape(trade.id)}">{escape(trade.instrument.symbol)} · {escape(localtime.fmt(trade.opened_at, "%Y-%m-%d"))}</option>'
            for trade in trades
        )
        journal_options = '<option value="">No linked journal</option>' + "".join(
            f'<option value="{escape(journal.id)}">{escape(journal.title)}</option>' for journal in journals
        )
        attachment_rows = "".join(
            f"""<tr><td><a class="pill" href="{escape(storage.display_url(row.url))}" target="_blank" rel="noopener">{escape(row.file_name)}</a></td><td>{row.file_size:,}</td><td>{escape(row.mime_type)}</td><td>{escape(localtime.fmt(row.created_at))}</td></tr>"""
            for row in attachments
        ) or '<tr><td colspan="4" class="muted">No uploads yet.</td></tr>'
        body = f"""
          <section class="grid two-col">
            <form class="card" style="margin-top:16px" method="post" action="/settings/uploads" enctype="multipart/form-data">
              <div class="section-head"><div><div class="label">Screenshots</div><h2 style="margin:4px 0 0">Upload Chart Image</h2></div><button class="primary" type="submit">Upload</button></div>
              <div class="field"><label>Trade</label><select name="trade_id">{trade_options}</select></div>
              <div class="field" style="margin-top:12px"><label>Journal</label><select name="journal_entry_id">{journal_options}</select></div>
              <div class="field" style="margin-top:12px"><label>Image</label><input type="file" name="file" accept="image/png,image/jpeg,image/webp" required /></div>
              <p class="muted small">PNG, JPEG or WebP, up to 10 MB. Screenshots are private to your account.</p>
            </form>
            <section class="card" style="margin-top:16px">
              <div class="section-head"><div><div class="label">Storage</div><h2 style="margin:4px 0 0">Attachment Rules</h2></div></div>
              <p class="muted">Attach screenshots to either a trade, a journal entry, or both. Annotation JSON is reserved on the model for later chart markup.</p>
              <div class="actions"><a class="pill" href="/journal">Journal</a><a class="pill" href="/trades">Trades</a></div>
            </section>
          </section>
          <section class="card" style="margin-top:16px">
            <div class="section-head"><div><div class="label">Uploads</div><h2 style="margin:4px 0 0">Recent Attachments</h2></div></div>
            <table><thead><tr><th>File</th><th>Size</th><th>Type</th><th>Uploaded</th></tr></thead><tbody>{attachment_rows}</tbody></table>
          </section>
        """
        return shell("Uploads", "settings", body, "Settings", user.name or user.email)
    finally:
        session.close()


@app.post("/settings/uploads")
async def upload_attachment(
    file: UploadFile = File(...),
    trade_id: str = Form(""),
    journal_entry_id: str = Form(""),
) -> RedirectResponse:
    session = SessionLocal()
    try:
        user = current_user(session)
        if user is None:
            raise HTTPException(status_code=400, detail="No demo user available")
        linked_trade_id = trade_id or None
        linked_journal_id = journal_entry_id or None
        if not linked_trade_id and not linked_journal_id:
            raise HTTPException(status_code=400, detail="Attach the screenshot to a trade or a journal entry")
        validate_upload(file)
        if linked_trade_id:
            trade = session.scalar(select(Trade).where(Trade.id == linked_trade_id, Trade.user_id == user.id))
            if trade is None:
                raise HTTPException(status_code=404, detail="Trade not found")
        if linked_journal_id:
            journal = session.scalar(select(JournalEntry).where(JournalEntry.id == linked_journal_id, JournalEntry.user_id == user.id))
            if journal is None:
                raise HTTPException(status_code=404, detail="Journal not found")
        data = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=400, detail="Upload is too large")
        # Trust the file's bytes, not the browser's content type.
        content_type = storage.sniff_image_type(data)
        if content_type is None:
            raise HTTPException(status_code=400, detail="Only PNG, JPEG or WebP images are supported")
        ref = storage.get_storage().save(user.id, data, content_type)
        session.add(
            Attachment(
                trade_id=linked_trade_id,
                journal_entry_id=linked_journal_id,
                url=ref,
                file_name=(file.filename or "screenshot")[:255],
                file_size=len(data),
                mime_type=content_type,
                annotation_data=None,
            )
        )
        session.commit()
        return RedirectResponse("/settings/uploads", status_code=status.HTTP_303_SEE_OTHER)
    finally:
        await file.close()
        session.close()


def report_nav(active: str) -> str:
    items = [
        ("Reports Hub", "/reports", "hub"),
        ("Performance", "/reports/performance", "performance"),
        ("Tax CSV", "/reports/tax-csv", "tax"),
        ("Prop Firm", "/reports/prop-firm", "prop"),
    ]
    return '<div class="actions" style="margin-top:16px">' + "".join(
        f'<a class="pill {"primary" if key == active else ""}" href="{href}">{label}</a>'
        for label, href, key in items
    ) + "</div>"


def closed_trade_rows(trades: list[Trade]) -> str:
    return "".join(
        f"""<tr>
          <td>{escape(trade.instrument.symbol)}</td>
          <td>{escape(localtime.fmt(trade.opened_at, "%Y-%m-%d"))}</td>
          <td>{escape(localtime.fmt(trade.closed_at, "%Y-%m-%d"))}</td>
          <td>{number(trade.metrics.total_quantity if trade.metrics else 0)}</td>
          <td>{money(trade.metrics.average_entry if trade.metrics else 0)}</td>
          <td>{money(trade.metrics.average_exit if trade.metrics else 0)}</td>
          <td class="{tone(trade.metrics.realized_pnl if trade.metrics else 0)}">{money(trade.metrics.realized_pnl if trade.metrics else 0)}</td>
          <td>{number(trade.metrics.r_multiple if trade.metrics else 0, "R")}</td>
        </tr>"""
        for trade in trades
    ) or '<tr><td colspan="8" class="muted">No closed trades available.</td></tr>'


@app.get("/reports", response_class=HTMLResponse)
def reports_hub() -> str:
    session = SessionLocal()
    try:
        user = current_user(session)
        if user is None:
            return shell("Reports", "reports", '<section class="empty" style="margin-top:18px">No users found. Run seed data first.</section>')
        metrics = summary(session, user.id)
        report_payload = performance_report_payload(session, user.id)
        body = f"""
          {report_nav("hub")}
          <section class="grid kpis">
            {kpi_card("Closed Trades", str(metrics["trades_count"]), "neutral", "Reportable trades")}
            {kpi_card("Net P&L", money(metrics["net_pnl"]), tone(metrics["net_pnl"]), "Tax period")}
            {kpi_card("Win Rate", number(metrics["win_rate"], "%"), "neutral", "Performance")}
            {kpi_card("Profit Factor", number(metrics["profit_factor"]), "neutral", "Performance")}
          </section>
          <section class="grid accounts">
            <article class="card"><div class="label">Performance Report</div><h2>Printable summary</h2><p class="muted">Includes account summary, setup breakdown, top wins, and top losses.</p><a class="pill primary" href="/reports/performance">Open Report</a></article>
            <article class="card"><div class="label">Tax CSV</div><h2>Closed trades export</h2><p class="muted">Compatible CSV fields for accounting workflows.</p><a class="pill primary" href="/reports/tax-csv">Download CSV</a></article>
            <article class="card"><div class="label">Prop Firm</div><h2>Drawdown and limits</h2><p class="muted">Shows prop account limits, daily loss, total drawdown, and targets.</p><a class="pill primary" href="/reports/prop-firm">Open Report</a></article>
          </section>
          <section class="card" style="margin-top:16px">
            <div class="section-head"><div><div class="label">Generation Status</div><h2 style="margin:4px 0 0">Reports Ready</h2></div><span class="badge">{escape(str(report_payload["status"]))}</span></div>
            <p class="muted">PDF rendering can be added on top of these report views; the data contracts are already present.</p>
          </section>
        """
        return shell("Reports", "reports", body, "Reports", user.name or user.email)
    finally:
        session.close()


@app.get("/reports/performance", response_class=HTMLResponse)
def performance_report() -> str:
    session = SessionLocal()
    try:
        user = current_user(session)
        if user is None:
            return shell("Performance Report", "reports", '<section class="empty" style="margin-top:18px">No users found.</section>')
        metrics = summary(session, user.id)
        setup_rows = grouped_performance(session, user.id, "setup")[:10]
        trades = session.scalars(
            select(Trade)
            .options(selectinload(Trade.metrics), selectinload(Trade.instrument))
            .join(TradeMetrics, TradeMetrics.trade_id == Trade.id)
            .where(Trade.user_id == user.id, Trade.status == TradeStatus.CLOSED)
            .order_by(TradeMetrics.realized_pnl.desc())
        ).all()
        top_wins = trades[:5]
        top_losses = list(reversed(trades[-5:]))
        generated = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
        body = f"""
          {report_nav("performance")}
          <section class="card" style="margin-top:16px">
            <div class="section-head"><div><div class="label">Performance Report</div><h2 style="margin:4px 0 0">All Accounts</h2></div><span class="badge">Generated {escape(generated)}</span></div>
            <section class="grid kpis">
              {kpi_card("Net P&L", money(metrics["net_pnl"]), tone(metrics["net_pnl"]), "Closed trades")}
              {kpi_card("Win Rate", number(metrics["win_rate"], "%"), "neutral", "Closed trades")}
              {kpi_card("Profit Factor", number(metrics["profit_factor"]), "neutral", "All setups")}
              {kpi_card("Avg R", number(metrics["average_r"], "R"), tone(metrics["average_r"]), "Risk normalized")}
            </section>
          </section>
          {performance_table(setup_rows, "Setup Breakdown")}
          <section class="grid two-col">
            <div class="card"><div class="section-head"><div><div class="label">Top Wins</div><h2 style="margin:4px 0 0">Best Trades</h2></div></div><table><thead><tr><th>Symbol</th><th>Open</th><th>Close</th><th>Qty</th><th>Entry</th><th>Exit</th><th>Net</th><th>R</th></tr></thead><tbody>{closed_trade_rows(top_wins)}</tbody></table></div>
            <div class="card"><div class="section-head"><div><div class="label">Top Losses</div><h2 style="margin:4px 0 0">Worst Trades</h2></div></div><table><thead><tr><th>Symbol</th><th>Open</th><th>Close</th><th>Qty</th><th>Entry</th><th>Exit</th><th>Net</th><th>R</th></tr></thead><tbody>{closed_trade_rows(top_losses)}</tbody></table></div>
          </section>
        """
        return shell("Performance Report", "reports", body, "Reports", user.name or user.email)
    finally:
        session.close()


@app.get("/reports/tax-csv")
def tax_csv_download() -> Response:
    session = SessionLocal()
    try:
        user = current_user(session)
        if user is None:
            raise HTTPException(status_code=400, detail="No demo user available")
        return Response(
            content=tax_report_csv(session, user.id),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="tradzlog-tax-report.csv"'},
        )
    finally:
        session.close()


@app.get("/reports/prop-firm", response_class=HTMLResponse)
def prop_firm_report() -> str:
    session = SessionLocal()
    try:
        user = current_user(session)
        if user is None:
            return shell("Prop Firm Report", "reports", '<section class="empty" style="margin-top:18px">No users found.</section>')
        accounts = session.scalars(
            select(Account).where(Account.user_id == user.id, Account.prop_firm_name.is_not(None), Account.archived_at.is_(None))
        ).all()
        cards = ""
        for account in accounts:
            metrics = summary(session, user.id, account.id)
            snapshots = session.scalars(
                select(AccountSnapshot).where(AccountSnapshot.account_id == account.id).order_by(AccountSnapshot.date.asc())
            ).all()
            max_drawdown = min((row.drawdown_from_peak for row in snapshots), default=Decimal("0"))
            daily_loss_utilisation = Decimal("0")
            if account.max_daily_loss and metrics["net_pnl"] < 0:
                daily_loss_utilisation = min(abs(Decimal(metrics["net_pnl"])) / account.max_daily_loss * Decimal("100"), Decimal("100"))
            total_loss_utilisation = Decimal("0")
            if account.max_total_loss and max_drawdown < 0:
                total_loss_utilisation = min(abs(max_drawdown) / account.max_total_loss * Decimal("100"), Decimal("100"))
            cards += f"""
              <article class="card">
                <div class="section-head"><div><div class="label">{escape(account.prop_firm_name or "Prop Firm")}</div><h2 style="margin:4px 0 0">{escape(account.name)}</h2></div><span class="badge">{escape(account.currency)}</span></div>
                <section class="grid" style="grid-template-columns:repeat(3,1fr);gap:12px">
                  <div><div class="label">Net P&L</div><div class="kpi {tone(metrics["net_pnl"])}">{money(metrics["net_pnl"])}</div></div>
                  <div><div class="label">Daily Limit</div><div class="kpi">{money(account.max_daily_loss)}</div></div>
                  <div><div class="label">Total Limit</div><div class="kpi">{money(account.max_total_loss)}</div></div>
                </section>
                <div style="margin-top:14px"><div class="section-head" style="margin-bottom:6px"><span class="label">Daily Loss Utilisation</span><span class="small muted">{number(daily_loss_utilisation, "%")}</span></div><div class="bar"><span style="width:{daily_loss_utilisation}%;background:var(--amber)"></span></div></div>
                <div style="margin-top:14px"><div class="section-head" style="margin-bottom:6px"><span class="label">Total Drawdown Utilisation</span><span class="small muted">{number(total_loss_utilisation, "%")}</span></div><div class="bar"><span style="width:{total_loss_utilisation}%;background:var(--red)"></span></div></div>
              </article>
            """
        if not cards:
            cards = '<section class="empty">No prop firm accounts found. Add prop firm metadata to an account to activate this report.</section>'
        body = f"{report_nav('prop')}<section class=\"grid accounts\">{cards}</section>"
        return shell("Prop Firm Report", "reports", body, "Reports", user.name or user.email)
    finally:
        session.close()


def coaching_nav(active: str) -> str:
    items = [
        ("Coach Dashboard", "/coaching", "dashboard"),
        ("Ask the Coach", "/coaching/chat", "chat"),
        ("Journal Prompts", "/coaching/prompts", "prompts"),
    ]
    return f'<div class="page-block">{filter_tabs([(label, href, key == active) for label, href, key in items])}</div>'


def latest_insights(session, user_id: str) -> list[AIInsight]:
    return list(
        session.scalars(
            select(AIInsight).where(AIInsight.user_id == user_id).order_by(AIInsight.generated_at.desc()).limit(10)
        ).all()
    )


def insight_cards(insights: list[AIInsight]) -> str:
    return insight_cards_html(insights)


@app.get("/coaching", response_class=HTMLResponse)
def coaching_dashboard() -> str:
    session = SessionLocal()
    try:
        user = current_user(session)
        if user is None:
            return shell("AI Coaching", "coaching", '<section class="empty" style="margin-top:18px">No users found. Run seed data first.</section>')
        metrics = summary(session, user.id)
        setups = grouped_performance(session, user.id, "setup")
        insights = latest_insights(session, user.id)
        setup_rows = "".join(
            f"""<tr><td>{escape(str(row["key"]))}</td><td class="num">{row["trades"]}</td><td class="num {tone(row["netPnl"])}">{money(row["netPnl"])}</td><td class="num">{number(row["winRate"], "%")}</td><td class="num">{number(row["averageR"], "R")}</td><td>{coach_quality_badge(row["averageR"], row["trades"])}</td></tr>"""
            for row in setups[:10]
        ) or '<tr><td colspan="6" class="muted">No setup scorecard data yet.</td></tr>'
        body = f"""
          {coaching_nav("dashboard")}
          <section class="grid kpis">
            {kpi_card("Net P&L", money(metrics["net_pnl"]), tone(metrics["net_pnl"]), "Coach context")}
            {kpi_card("Win Rate", number(metrics["win_rate"], "%"), "neutral", "Closed trades")}
            {kpi_card("Profit Factor", number(metrics["profit_factor"]), "neutral", "Book quality")}
            {kpi_card("Average R", number(metrics["average_r"], "R"), tone(metrics["average_r"]), "Expectancy")}
          </section>
          <section class="grid two-col">
            <section class="card" style="margin-top:16px">
              <div class="section-head"><div><div class="label">Pattern Analysis</div><h2 style="margin:4px 0 0">AI Insights</h2></div><form method="post" action="/coaching/generate"><button class="primary" type="submit">Generate</button></form></div>
              <div class="grid">{insight_cards_html(insights)}</div>
            </section>
            <section class="card" style="margin-top:16px">
              <div class="section-head"><div><div class="label">Setup Scorecard</div><h2 style="margin:4px 0 0">Edge Quality</h2></div></div>
              <table><thead><tr><th>Setup</th><th>Trades</th><th>Net</th><th>Win</th><th>Avg R</th><th>Coach Note</th></tr></thead><tbody>{setup_rows}</tbody></table>
            </section>
          </section>
        """
        return shell("AI Coaching", "coaching", body, "Coaching", user.name or user.email)
    finally:
        session.close()


@app.post("/coaching/generate")
def generate_coaching() -> RedirectResponse:
    session = SessionLocal()
    try:
        user = current_user(session)
        if user is None:
            raise HTTPException(status_code=400, detail="No demo user available")
        payload = build_coaching_payload(dict(summary(session, user.id)), grouped_performance(session, user.id, "setup"))
        insight = generate_coaching_insight(user.id, user.name or user.email, None, payload)
        session.add(insight)
        session.commit()
        return RedirectResponse("/coaching", status_code=status.HTTP_303_SEE_OTHER)
    finally:
        session.close()


def coach_answer(question: str, metrics: dict[str, object], setups: list[dict[str, object]], recent_trades: list[Trade]) -> str:
    best_setup = setups[0]["key"] if setups else "no setup yet"
    worst_setup = setups[-1]["key"] if setups else "no setup yet"
    recent_loss_count = len([trade for trade in recent_trades if trade.metrics and trade.metrics.realized_pnl < 0])
    return (
        f"Question: {question}\n\n"
        f"Your current data shows {metrics['trades_count']} closed trades, {money(metrics['net_pnl'])} net P&L, "
        f"{number(metrics['win_rate'], '%')} win rate, {number(metrics['profit_factor'])} profit factor, "
        f"and {number(metrics['average_r'], 'R')} average R.\n\n"
        f"Strongest current setup by net contribution: {best_setup}. Weakest or lowest-ranked setup: {worst_setup}. "
        f"In the latest 30 trades, {recent_loss_count} were losses. The immediate coaching focus is to compare the losing trades against setup quality, "
        "planned R, and whether the trade had a clear journal thesis before entry."
    )


@app.get("/coaching/chat", response_class=HTMLResponse)
def coaching_chat() -> str:
    session = SessionLocal()
    try:
        user = current_user(session)
        if user is None:
            return shell("Ask the Coach", "coaching", '<section class="empty" style="margin-top:18px">No users found.</section>')
        body = f"""
          {coaching_nav("chat")}
          <form class="card" style="margin-top:16px" method="post" action="/coaching/chat">
            <div class="section-head"><div><div class="label">Ask the Coach</div><h2 style="margin:4px 0 0">Data-grounded Q&A</h2></div><button class="primary" type="submit">Ask</button></div>
            <div class="field"><label>Question</label><textarea name="question" required placeholder="Why am I losing more on Tuesdays? Should I stop trading ES? What's killing my profit factor?"></textarea></div>
          </form>
        """
        return shell("Ask the Coach", "coaching", body, "Coaching", user.name or user.email)
    finally:
        session.close()


@app.post("/coaching/chat", response_class=HTMLResponse)
def coaching_chat_answer(question: str = Form(...)) -> str:
    session = SessionLocal()
    try:
        user = current_user(session)
        if user is None:
            raise HTTPException(status_code=400, detail="No demo user available")
        recent_trades = session.scalars(
            select(Trade)
            .options(selectinload(Trade.metrics))
            .where(Trade.user_id == user.id, Trade.status == TradeStatus.CLOSED)
            .order_by(Trade.closed_at.desc())
            .limit(30)
        ).all()
        answer = coach_answer(question, summary(session, user.id), grouped_performance(session, user.id, "setup"), list(recent_trades))
        body = f"""
          {coaching_nav("chat")}
          <section class="card" style="margin-top:16px">
            <div class="section-head"><div><div class="label">Coach Response</div><h2 style="margin:4px 0 0">Answer</h2></div><a class="pill" href="/coaching/chat">Ask Another</a></div>
            <p style="white-space:pre-wrap;line-height:1.65">{escape(answer)}</p>
          </section>
        """
        return shell("Ask the Coach", "coaching", body, "Coaching", user.name or user.email)
    finally:
        session.close()


@app.get("/coaching/prompts", response_class=HTMLResponse)
def coaching_prompts(prompt_date: date | None = Query(default=None)) -> str:
    session = SessionLocal()
    try:
        user = current_user(session)
        if user is None:
            return shell("Journal Prompts", "coaching", '<section class="empty" style="margin-top:18px">No users found.</section>')
        target_date = prompt_date or date.today()
        trades = session.scalars(
            select(Trade)
            .options(selectinload(Trade.metrics), selectinload(Trade.instrument))
            .where(Trade.user_id == user.id)
            .where(Trade.opened_at >= datetime.combine(target_date, datetime.min.time()).replace(tzinfo=UTC))
            .where(Trade.opened_at <= datetime.combine(target_date, datetime.max.time()).replace(tzinfo=UTC))
        ).all()
        loss_count = len([trade for trade in trades if trade.metrics and trade.metrics.realized_pnl < 0])
        win_count = len([trade for trade in trades if trade.metrics and trade.metrics.realized_pnl > 0])
        prompts = [
            f"You logged {len(trades)} trades on {target_date.isoformat()} with {win_count} wins and {loss_count} losses. What changed between your best and worst execution?",
            "Which trade most closely followed the plan you wrote before entry, and which one drifted from it?",
            "If you could enforce one rule tomorrow based on today's behavior, what would it be?",
        ]
        prompt_cards = "".join(f'<article class="card"><div class="label">Reflection Prompt</div><p>{escape(prompt)}</p></article>' for prompt in prompts)
        body = f"""
          {coaching_nav("prompts")}
          <form class="actions" style="margin-top:16px" method="get" action="/coaching/prompts">
            <input type="date" name="prompt_date" value="{target_date.isoformat()}" style="max-width:180px" />
            <button type="submit">Load Prompts</button>
            <a class="pill primary" href="/journal/new?entry_type=DAILY">Open Daily Journal</a>
          </form>
          <section class="grid accounts">{prompt_cards}</section>
        """
        return shell("Journal Prompts", "coaching", body, "Coaching", user.name or user.email)
    finally:
        session.close()


PLAN_DETAILS = {
    Plan.FREE: {"price": Decimal("0"), "label": "Free", "features": ["1 account", "Manual journaling", "Basic dashboard"]},
    Plan.PRO: {"price": Decimal("29"), "label": "Pro", "features": ["Unlimited trades", "Advanced analytics", "Reports", "Trade replay"]},
    Plan.ELITE: {"price": Decimal("79"), "label": "Elite", "features": ["AI coaching", "Mentor mode", "Community leaderboard", "Priority insights"]},
}


def current_subscription(session, user: User) -> BillingSubscription | None:
    return session.scalar(
        select(BillingSubscription)
        .where(BillingSubscription.user_id == user.id)
        .order_by(BillingSubscription.created_at.desc())
    )


def plan_cards(current_plan: Plan) -> str:
    cards = ""
    for plan, details in PLAN_DETAILS.items():
        features = "".join(f"<li>{escape(feature)}</li>" for feature in details["features"])
        action = "Current Plan" if plan == current_plan else f"Switch to {details['label']}"
        disabled = "disabled" if plan == current_plan else ""
        cards += f"""
          <article class="card">
            <div class="section-head"><div><div class="label">Plan</div><h2 style="margin:4px 0 0">{escape(details["label"])}</h2></div><div class="kpi">{money(details["price"])}</div></div>
            <ul>{features}</ul>
            <form method="post" action="/settings/billing/checkout">
              <input type="hidden" name="plan" value="{plan.value}" />
              <button class="{"primary" if plan != current_plan else ""}" type="submit" {disabled}>{escape(action)}</button>
            </form>
          </article>
        """
    return cards


@app.get("/settings/billing", response_class=HTMLResponse)
def billing_page() -> str:
    session = SessionLocal()
    try:
        user = current_user(session)
        if user is None:
            return shell("Billing", "settings", '<section class="empty" style="margin-top:18px">No users found. Run seed data first.</section>')
        subscription = current_subscription(session, user)
        invoices = session.scalars(
            select(BillingInvoice).where(BillingInvoice.user_id == user.id).order_by(BillingInvoice.created_at.desc()).limit(20)
        ).all()
        invoice_rows = "".join(
            f"""<tr><td>{escape(invoice.created_at.strftime("%Y-%m-%d"))}</td><td>{escape(invoice.status.value)}</td><td>{money(invoice.amount_paid)}</td><td>{escape(invoice.currency)}</td><td>{escape(invoice.provider_invoice_id or invoice.id[:8])}</td></tr>"""
            for invoice in invoices
        ) or '<tr><td colspan="5" class="muted">No invoices yet.</td></tr>'
        subscription_status = subscription.status.value if subscription else "LOCAL"
        body = f"""
          <section class="grid kpis">
            {kpi_card("Current Plan", user.plan.value, "positive" if user.plan != Plan.FREE else "neutral", "Plan enforcement")}
            {kpi_card("Subscription", subscription_status, "neutral", "Stripe-ready state")}
            {kpi_card("Billing Provider", subscription.provider if subscription else "LOCAL", "neutral", "Development mode")}
          </section>
          <section class="grid accounts">{plan_cards(user.plan)}</section>
          <section class="grid two-col">
            <section class="card" style="margin-top:16px">
              <div class="section-head"><div><div class="label">Billing Portal</div><h2 style="margin:4px 0 0">Subscription Controls</h2></div></div>
              <p class="muted">In production this opens Stripe Billing Portal. In local development it records plan state and invoices directly.</p>
              <form method="post" action="/settings/billing/portal"><button type="submit">Open Billing Portal</button></form>
            </section>
            <section class="card" style="margin-top:16px">
              <div class="section-head"><div><div class="label">Plan Enforcement</div><h2 style="margin:4px 0 0">Feature Gates</h2></div></div>
              <table><thead><tr><th>Feature</th><th>Required Plan</th><th>Status</th></tr></thead><tbody>
                <tr><td>Reports</td><td>PRO</td><td>{"Enabled" if user.plan in {Plan.PRO, Plan.ELITE} else "Locked"}</td></tr>
                <tr><td>AI Coaching</td><td>ELITE</td><td>{"Enabled" if user.plan == Plan.ELITE else "Locked"}</td></tr>
                <tr><td>Community</td><td>ELITE</td><td>{"Enabled" if user.plan == Plan.ELITE else "Locked"}</td></tr>
              </tbody></table>
            </section>
          </section>
          <section class="card" style="margin-top:16px">
            <div class="section-head"><div><div class="label">Invoices</div><h2 style="margin:4px 0 0">Invoice History</h2></div></div>
            <table><thead><tr><th>Date</th><th>Status</th><th>Paid</th><th>Currency</th><th>Invoice</th></tr></thead><tbody>{invoice_rows}</tbody></table>
          </section>
        """
        return shell("Billing", "settings", body, "Settings", user.name or user.email)
    finally:
        session.close()


@app.post("/settings/billing/checkout")
def billing_checkout(plan: Plan = Form(...)) -> RedirectResponse:
    session = SessionLocal()
    try:
        user = current_user(session)
        if user is None:
            raise HTTPException(status_code=400, detail="No demo user available")
        now = datetime.now(UTC)
        user.plan = plan
        subscription = BillingSubscription(
            user_id=user.id,
            plan=plan,
            status=BillingStatus.ACTIVE,
            provider="LOCAL",
            provider_customer_id=f"local_customer_{user.id[:8]}",
            provider_subscription_id=f"local_subscription_{uuid4().hex[:12]}",
            current_period_start=now,
            current_period_end=now.replace(year=now.year + 1) if plan != Plan.FREE else None,
            cancel_at_period_end=False,
        )
        session.add(subscription)
        amount = PLAN_DETAILS[plan]["price"]
        if amount > 0:
            session.flush()
            session.add(
                BillingInvoice(
                    user_id=user.id,
                    subscription_id=subscription.id,
                    provider_invoice_id=f"local_invoice_{uuid4().hex[:12]}",
                    amount_due=amount,
                    amount_paid=amount,
                    currency="USD",
                    paid_at=now,
                )
            )
        session.commit()
        return RedirectResponse("/settings/billing", status_code=status.HTTP_303_SEE_OTHER)
    finally:
        session.close()


@app.post("/settings/billing/portal")
def billing_portal() -> RedirectResponse:
    return RedirectResponse("/settings/billing", status_code=status.HTTP_303_SEE_OTHER)


def leaderboard_rows(session) -> str:
    profiles = session.scalars(
        select(LeaderboardProfile).where(LeaderboardProfile.is_public.is_(True)).order_by(LeaderboardProfile.created_at.desc())
    ).all()
    rows = ""
    for profile in profiles:
        metrics = summary(session, profile.user_id)
        score = metrics["profit_factor"] if profile.metric == LeaderboardMetric.PROFIT_FACTOR else metrics["average_r"]
        if profile.metric == LeaderboardMetric.WIN_RATE:
            score = metrics["win_rate"]
        rows += f"""<tr>
          <td>{escape(profile.display_name)}</td>
          <td>{escape(profile.metric.value.replace("_", " ").title())}</td>
          <td>{number(score, "%" if profile.metric == LeaderboardMetric.WIN_RATE else "")}</td>
          <td>{metrics["trades_count"]}</td>
          <td>{number(metrics["average_r"], "R")}</td>
          <td>{number(metrics["profit_factor"])}</td>
        </tr>"""
    return rows or '<tr><td colspan="6" class="muted">No public leaderboard profiles yet.</td></tr>'


@app.get("/community", response_class=HTMLResponse)
def community_page() -> str:
    session = SessionLocal()
    try:
        user = current_user(session)
        if user is None:
            return shell("Community", "community", '<section class="empty" style="margin-top:18px">No users found. Run seed data first.</section>')
        profile = session.scalar(select(LeaderboardProfile).where(LeaderboardProfile.user_id == user.id))
        shares = session.scalars(
            select(PublicTradeShare).where(PublicTradeShare.user_id == user.id, PublicTradeShare.revoked_at.is_(None)).order_by(PublicTradeShare.created_at.desc()).limit(20)
        ).all()
        share_rows = "".join(
            f"""<tr><td>{escape(share.title)}</td><td><a class="pill" href="/share/{escape(share.slug)}">/share/{escape(share.slug)}</a></td><td>{escape(localtime.fmt(share.created_at, "%Y-%m-%d"))}</td></tr>"""
            for share in shares
        ) or '<tr><td colspan="3" class="muted">No public trade shares yet.</td></tr>'
        profile_name = profile.display_name if profile else (user.name or "Demo Trader")
        metric_options = "".join(
            f'<option value="{metric.value}" {"selected" if profile and profile.metric == metric else ""}>{metric.value.replace("_", " ").title()}</option>'
            for metric in LeaderboardMetric
        )
        public_checked = "checked" if profile and profile.is_public else ""
        body = f"""
          <section class="grid two-col">
            <form class="card" style="margin-top:16px" method="post" action="/community/leaderboard">
              <div class="section-head"><div><div class="label">Leaderboard</div><h2 style="margin:4px 0 0">Opt-In Profile</h2></div><button class="primary" type="submit">Save</button></div>
              <div class="field"><label>Display Name</label><input name="display_name" value="{escape(profile_name)}" required /></div>
              <div class="field" style="margin-top:12px"><label>Ranking Metric</label><select name="metric">{metric_options}</select></div>
              <label class="small" style="display:flex;gap:8px;margin-top:12px"><input type="checkbox" name="is_public" value="1" {public_checked} style="width:auto" /> Show me on the public leaderboard</label>
            </form>
            <section class="card" style="margin-top:16px">
              <div class="section-head"><div><div class="label">Public Shares</div><h2 style="margin:4px 0 0">Trade Links</h2></div><a class="pill" href="/trades">Choose Trade</a></div>
              <table><thead><tr><th>Title</th><th>URL</th><th>Created</th></tr></thead><tbody>{share_rows}</tbody></table>
            </section>
          </section>
          <section class="card" style="margin-top:16px">
            <div class="section-head"><div><div class="label">Leaderboard</div><h2 style="margin:4px 0 0">Strategy Rankings</h2></div><span class="badge">R-multiple normalized</span></div>
            <table><thead><tr><th>Trader</th><th>Metric</th><th>Score</th><th>Trades</th><th>Avg R</th><th>Profit Factor</th></tr></thead><tbody>{leaderboard_rows(session)}</tbody></table>
          </section>
          <section class="card" style="margin-top:16px">
            <div class="section-head"><div><div class="label">Mentor Mode</div><h2 style="margin:4px 0 0">Read-only Review</h2></div><a class="pill primary" href="/community/mentor">Manage Mentor Access</a></div>
            <p class="muted">Grant a mentor read-only access and let them leave comments on individual trades.</p>
          </section>
        """
        return shell("Community", "community", body, "Community", user.name or user.email)
    finally:
        session.close()


@app.post("/community/leaderboard")
def save_leaderboard_profile(
    display_name: str = Form(...),
    metric: LeaderboardMetric = Form(...),
    is_public: str | None = Form(default=None),
) -> RedirectResponse:
    session = SessionLocal()
    try:
        user = current_user(session)
        if user is None:
            raise HTTPException(status_code=400, detail="No demo user available")
        profile = session.scalar(select(LeaderboardProfile).where(LeaderboardProfile.user_id == user.id))
        if profile is None:
            profile = LeaderboardProfile(user_id=user.id, display_name=display_name, metric=metric, is_public=bool(is_public))
            session.add(profile)
        else:
            profile.display_name = display_name
            profile.metric = metric
            profile.is_public = bool(is_public)
        session.commit()
        return RedirectResponse("/community", status_code=status.HTTP_303_SEE_OTHER)
    finally:
        session.close()


@app.post("/trades/{trade_id}/share")
def share_trade(trade_id: str) -> RedirectResponse:
    session = SessionLocal()
    try:
        user = current_user(session)
        if user is None:
            raise HTTPException(status_code=400, detail="No demo user available")
        trade = session.scalar(select(Trade).options(selectinload(Trade.instrument)).where(Trade.id == trade_id, Trade.user_id == user.id))
        if trade is None:
            raise HTTPException(status_code=404, detail="Trade not found")
        existing = session.scalar(select(PublicTradeShare).where(PublicTradeShare.trade_id == trade.id, PublicTradeShare.revoked_at.is_(None)))
        if existing is None:
            existing = PublicTradeShare(
                user_id=user.id,
                trade_id=trade.id,
                slug=f"{trade.instrument.symbol.lower()}-{uuid4().hex[:10]}",
                title=f"{trade.instrument.symbol} {trade.direction.value} trade",
                anonymized=True,
                show_r_multiple_only=True,
            )
            session.add(existing)
            session.commit()
        return RedirectResponse(f"/share/{existing.slug}", status_code=status.HTTP_303_SEE_OTHER)
    finally:
        session.close()


@app.get("/share/{slug}", response_class=HTMLResponse)
def public_share(slug: str) -> str:
    session = SessionLocal()
    try:
        share = session.scalar(select(PublicTradeShare).where(PublicTradeShare.slug == slug, PublicTradeShare.revoked_at.is_(None)))
        if share is None:
            return shell("Shared Trade", "community", '<section class="empty" style="margin-top:18px">Shared trade not found.</section>')
        trade = session.scalar(select(Trade).options(selectinload(Trade.metrics), selectinload(Trade.instrument)).where(Trade.id == share.trade_id))
        if trade is None:
            return shell("Shared Trade", "community", '<section class="empty" style="margin-top:18px">Trade not found.</section>')
        body = f"""
          <section class="card" style="margin-top:16px">
            <div class="section-head"><div><div class="label">Public Trade Share</div><h2 style="margin:4px 0 0">{escape(share.title)}</h2></div><span class="badge">Anonymized</span></div>
            <section class="grid kpis">
              {kpi_card("Symbol", trade.instrument.symbol, "neutral", trade.instrument.asset_class.value)}
              {kpi_card("Direction", trade.direction.value, "positive" if trade.direction == Direction.LONG else "negative", "Side")}
              {kpi_card("R Multiple", number(trade.metrics.r_multiple if trade.metrics else 0, "R"), tone(trade.metrics.r_multiple if trade.metrics else 0), "No dollar P&L shown")}
              {kpi_card("Setup", trade.setup_tag or "Unassigned", "neutral", "Shared context")}
            </section>
            <p class="muted">This public view hides account name and dollar P&L by default.</p>
          </section>
        """
        return shell("Shared Trade", "community", body, "Public Share", "Guest")
    finally:
        session.close()


@app.get("/community/mentor", response_class=HTMLResponse)
def mentor_page() -> str:
    session = SessionLocal()
    try:
        user = current_user(session)
        if user is None:
            return shell("Mentor Mode", "community", '<section class="empty" style="margin-top:18px">No users found.</section>')
        accesses = session.scalars(select(MentorAccess).where(MentorAccess.student_user_id == user.id).order_by(MentorAccess.created_at.desc())).all()
        trades = session.scalars(select(Trade).options(selectinload(Trade.instrument)).where(Trade.user_id == user.id).order_by(Trade.opened_at.desc()).limit(100)).all()
        comments = session.scalars(select(MentorComment).where(MentorComment.student_user_id == user.id).order_by(MentorComment.created_at.desc()).limit(50)).all()
        access_options = "".join(
            f'<option value="{escape(access.id)}">{escape(access.mentor_name or access.mentor_email)}</option>' for access in accesses if access.status == MentorAccessStatus.ACTIVE
        )
        trade_options = "".join(
            f'<option value="{escape(trade.id)}">{escape(trade.instrument.symbol)} · {escape(localtime.fmt(trade.opened_at, "%Y-%m-%d"))}</option>' for trade in trades
        )
        access_rows = "".join(
            f"""<tr><td>{escape(access.mentor_name or "")}</td><td>{escape(access.mentor_email)}</td><td><span class="badge">{escape(access.status.value)}</span></td><td>{'Yes' if access.can_view_journals else 'No'}</td><td>{'Yes' if access.can_comment else 'No'}</td></tr>"""
            for access in accesses
        ) or '<tr><td colspan="5" class="muted">No mentors have been granted access.</td></tr>'
        comment_rows = "".join(
            f"""<tr><td>{escape(localtime.fmt(comment.created_at))}</td><td>{escape(comment.body[:160])}</td><td>{escape(comment.trade_id or "")}</td></tr>"""
            for comment in comments
        ) or '<tr><td colspan="3" class="muted">No mentor comments yet.</td></tr>'
        body = f"""
          <section class="grid two-col">
            <form class="card" style="margin-top:16px" method="post" action="/community/mentor/access">
              <div class="section-head"><div><div class="label">Mentor Access</div><h2 style="margin:4px 0 0">Grant Read-only Access</h2></div><button class="primary" type="submit">Grant</button></div>
              <div class="field"><label>Mentor Name</label><input name="mentor_name" /></div>
              <div class="field" style="margin-top:12px"><label>Mentor Email</label><input type="email" name="mentor_email" required /></div>
              <label class="small" style="display:flex;gap:8px;margin-top:12px"><input type="checkbox" name="can_view_journals" value="1" checked style="width:auto" /> Can view journals</label>
              <label class="small" style="display:flex;gap:8px;margin-top:12px"><input type="checkbox" name="can_comment" value="1" checked style="width:auto" /> Can comment</label>
            </form>
            <form class="card" style="margin-top:16px" method="post" action="/community/mentor/comments">
              <div class="section-head"><div><div class="label">Mentor Comment</div><h2 style="margin:4px 0 0">Add Review Note</h2></div><button class="primary" type="submit">Comment</button></div>
              <div class="field"><label>Mentor</label><select name="mentor_access_id" required>{access_options}</select></div>
              <div class="field" style="margin-top:12px"><label>Trade</label><select name="trade_id" required>{trade_options}</select></div>
              <div class="field" style="margin-top:12px"><label>Comment</label><textarea name="body" required></textarea></div>
            </form>
          </section>
          <section class="card" style="margin-top:16px">
            <div class="section-head"><div><div class="label">Mentors</div><h2 style="margin:4px 0 0">Access List</h2></div></div>
            <table><thead><tr><th>Name</th><th>Email</th><th>Status</th><th>Journals</th><th>Comments</th></tr></thead><tbody>{access_rows}</tbody></table>
          </section>
          <section class="card" style="margin-top:16px">
            <div class="section-head"><div><div class="label">Comments</div><h2 style="margin:4px 0 0">Mentor Feedback</h2></div></div>
            <table><thead><tr><th>Created</th><th>Comment</th><th>Trade ID</th></tr></thead><tbody>{comment_rows}</tbody></table>
          </section>
        """
        return shell("Mentor Mode", "community", body, "Community", user.name or user.email)
    finally:
        session.close()


@app.post("/community/mentor/access")
def grant_mentor_access(
    mentor_email: str = Form(...),
    mentor_name: str = Form(""),
    can_view_journals: str | None = Form(default=None),
    can_comment: str | None = Form(default=None),
) -> RedirectResponse:
    session = SessionLocal()
    try:
        user = current_user(session)
        if user is None:
            raise HTTPException(status_code=400, detail="No demo user available")
        access = MentorAccess(
            student_user_id=user.id,
            mentor_email=mentor_email.lower(),
            mentor_name=mentor_name or None,
            status=MentorAccessStatus.ACTIVE,
            can_view_journals=bool(can_view_journals),
            can_comment=bool(can_comment),
        )
        session.add(access)
        session.commit()
        return RedirectResponse("/community/mentor", status_code=status.HTTP_303_SEE_OTHER)
    finally:
        session.close()


@app.post("/community/mentor/comments")
def create_mentor_comment(
    mentor_access_id: str = Form(...),
    trade_id: str = Form(...),
    body: str = Form(...),
) -> RedirectResponse:
    session = SessionLocal()
    try:
        user = current_user(session)
        if user is None:
            raise HTTPException(status_code=400, detail="No demo user available")
        access = session.scalar(
            select(MentorAccess).where(
                MentorAccess.id == mentor_access_id,
                MentorAccess.student_user_id == user.id,
                MentorAccess.status == MentorAccessStatus.ACTIVE,
                MentorAccess.can_comment.is_(True),
            )
        )
        trade = session.scalar(select(Trade).where(Trade.id == trade_id, Trade.user_id == user.id))
        if access is None or trade is None:
            raise HTTPException(status_code=404, detail="Mentor access or trade not found")
        session.add(MentorComment(mentor_access_id=access.id, student_user_id=user.id, trade_id=trade.id, body=body))
        session.commit()
        return RedirectResponse("/community/mentor", status_code=status.HTTP_303_SEE_OTHER)
    finally:
        session.close()
