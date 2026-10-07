from __future__ import annotations

import logging
import shutil
from datetime import UTC, date, datetime
from decimal import Decimal
from html import escape
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import selectinload

from tradzlog_api.services.ai import build_coaching_payload, generate_coaching_insight
from tradzlog_api.services.analytics import (
    grouped_performance,
    rebuild_daily_stats,
    rebuild_equity_curve,
    streaks,
    summary,
)
from tradzlog_api.services.hardening import SECURITY_HEADERS, rate_limiter
from tradzlog_api.services.imports import parse_broker_csv
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
    BrokerSync,
    BrokerSyncStatus,
    BrokerSyncType,
    DailyStats,
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

app = FastAPI(title="TradzLog Web")
UPLOAD_ROOT = Path("var/uploads")
UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
app.mount("/uploads-local", StaticFiles(directory=str(UPLOAD_ROOT)), name="uploads-local")


WEB_SENSITIVE_POSTS = {
    "/coaching/generate",
    "/coaching/chat",
    "/settings/import/preview",
    "/settings/import/paste",
    "/settings/uploads",
    "/settings/billing/checkout",
    "/settings/billing/portal",
    "/community/leaderboard",
    "/community/mentor/access",
    "/community/mentor/comments",
}

WEB_SENSITIVE_POST_PREFIXES = ("/positions/", "/trades/")

UPLOAD_EXTENSIONS = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
ALLOWED_UPLOAD_TYPES = set(UPLOAD_EXTENSIONS)
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def is_sensitive_web_post(path: str) -> bool:
    return path in WEB_SENSITIVE_POSTS or any(path.startswith(prefix) for prefix in WEB_SENSITIVE_POST_PREFIXES)


@app.middleware("http")
async def web_security_headers(request: Request, call_next):
    request_id, started_at = start_request(request.headers.get("X-Request-ID"))
    client = request.client.host if request.client else "unknown"
    if request.method == "POST" and is_sensitive_web_post(request.url.path):
        result = rate_limiter.check(f"web:{request.url.path}:{client}", limit=30, window_seconds=60)
        if not result.allowed:
            response = Response(
                "Rate limit exceeded",
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

def money(value: Decimal | int | float | None) -> str:
    amount = Decimal(value or 0)
    sign = "-" if amount < 0 else ""
    return f"{sign}${abs(amount):,.2f}"


def number(value: Decimal | int | float | None, suffix: str = "") -> str:
    amount = Decimal(value or 0)
    return f"{amount:,.2f}{suffix}"


def tone(value: Decimal | int | float | None) -> str:
    amount = Decimal(value or 0)
    if amount > 0:
        return "positive"
    if amount < 0:
        return "negative"
    return "neutral"


def demo_user() -> tuple[User | None, str | None]:
    session = SessionLocal()
    try:
        user = session.scalar(select(User).order_by(User.created_at.asc()))
        return user, None
    except SQLAlchemyError as exc:
        return None, str(exc)
    finally:
        session.close()


def sparkline(points: list[Decimal]) -> str:
    if len(points) < 2:
        return empty_state(
            "No equity data yet",
            "Equity curve will appear after daily snapshots are generated.",
            icon="📈",
        )
    width = Decimal("900")
    height = Decimal("190")
    min_value = min(points)
    max_value = max(points)
    span = max(max_value - min_value, Decimal("1"))
    coords = []
    for index, value in enumerate(points):
        x = Decimal(index) / Decimal(len(points) - 1) * width
        y = height - ((value - min_value) / span * (height - Decimal("18"))) - Decimal("9")
        coords.append(f"{x:.2f},{y:.2f}")
    return f"""<svg class="spark" viewBox="0 0 900 190" role="img" aria-label="Equity curve">
      <line class="axis" x1="0" y1="160" x2="900" y2="160"></line>
      <polyline points="{' '.join(coords)}"></polyline>
    </svg>"""


def dashboard_data(account_id: str | None) -> dict[str, object]:
    session = SessionLocal()
    try:
        user = session.scalar(select(User).order_by(User.created_at.asc()))
        if user is None:
            return {"error": "No users found. Run `python prisma/seed.py` to load demo data."}
        accounts = session.scalars(
            select(Account).where(Account.user_id == user.id, Account.archived_at.is_(None)).order_by(Account.created_at)
        ).all()
        active_account = next((account for account in accounts if account.id == account_id), accounts[0] if accounts else None)
        metrics = summary(session, user.id, active_account.id if active_account else None)
        setup_rows = grouped_performance(session, user.id, "setup")[:6]
        recent_trades = session.scalars(
            select(Trade)
            .options(selectinload(Trade.metrics), selectinload(Trade.instrument))
            .where(Trade.user_id == user.id)
            .order_by(Trade.opened_at.desc())
            .limit(10)
        ).all()
        snapshots = []
        if active_account is not None:
            snapshots = session.scalars(
                select(AccountSnapshot)
                .where(AccountSnapshot.account_id == active_account.id)
                .order_by(AccountSnapshot.date.asc())
            ).all()
        return {
            "user": user,
            "accounts": accounts,
            "account": active_account,
            "summary": metrics,
            "setups": setup_rows,
            "recent_trades": recent_trades,
            "equity": [row.balance for row in snapshots],
        }
    except SQLAlchemyError as exc:
        return {"error": str(exc)}
    finally:
        session.close()




def render_dashboard(account_id: str | None = None) -> str:
    data = dashboard_data(account_id)
    if "error" in data:
        body = empty_state("Dashboard unavailable", str(data["error"]))
        return shell("Dashboard", "dashboard", body, show_range=False)
    user = data["user"]
    account = data["account"]
    metrics = data["summary"]
    account_tabs = filter_tabs(
        [("All accounts", "/dashboard", account is None)]
        + [
            (account_row.name, f"/dashboard?account_id={escape(account_row.id)}", account and account.id == account_row.id)
            for account_row in data["accounts"]
        ]
    )
    setup_rows = "".join(
        f"""<tr><td>{escape(str(row["key"]))}</td><td class="num">{row["trades"]}</td><td class="num {tone(row["netPnl"])}">{money(row["netPnl"])}</td><td class="num">{number(row["winRate"], "%")}</td><td class="num">{number(row["averageR"], "R")}</td></tr>"""
        for row in data["setups"]
    ) or '<tr><td colspan="5" class="muted">No setup data yet.</td></tr>'
    recent_rows = "".join(
        f"""<tr><td>{escape(trade.opened_at.strftime("%Y-%m-%d"))}</td><td>{escape(trade.instrument.symbol)}</td><td>{side_badge(trade.direction.value)}</td><td>{escape(trade.setup_tag or "Unassigned")}</td><td class="num {tone(trade.metrics.realized_pnl if trade.metrics else 0)}">{money(trade.metrics.realized_pnl if trade.metrics else 0)}</td><td class="num">{number(trade.metrics.r_multiple if trade.metrics else 0, "R")}</td></tr>"""
        for trade in data["recent_trades"]
    ) or '<tr><td colspan="6" class="muted">No trades logged yet.</td></tr>'
    body = f"""
      <div class="toolbar page-block">{account_tabs}</div>
      <section class="grid kpis page-block">
        {kpi_card("Net P&L", money(metrics["net_pnl"]), tone(metrics["net_pnl"]), "Selected account")}
        {kpi_card("Win Rate", number(metrics["win_rate"], "%"), "neutral", "Closed trades")}
        {kpi_card("Profit Factor", number(metrics["profit_factor"]), "neutral", "Gross winners / losers")}
        {kpi_card("Total Trades", str(metrics["trades_count"]), "neutral", "Closed positions")}
        {kpi_card("Avg R", number(metrics["average_r"], "R"), tone(metrics["average_r"]), "Expectancy proxy")}
        {kpi_card("Avg Hold", number(Decimal(metrics["average_hold_seconds"]) / Decimal("3600"), "h"), "neutral", "Time in trade")}
      </section>
      <section class="grid two-col">
        <div class="card">
          <div class="section-head"><div><div class="label">Equity Curve</div><h2 style="margin:4px 0 0">Balance Over Time</h2></div><span class="badge">Net P&L</span></div>
          {sparkline(data["equity"])}
        </div>
        <div class="card">
          <div class="section-head"><div><div class="label">Setup Scoreboard</div><h2 style="margin:4px 0 0">Best Edges</h2></div></div>
          <table><thead><tr><th>Setup</th><th>Trades</th><th>Net</th><th>Win</th><th>Avg R</th></tr></thead><tbody>{setup_rows}</tbody></table>
        </div>
      </section>
      <section class="card" style="margin-top:16px">
        <div class="section-head"><div><div class="label">Recent Trades</div><h2 style="margin:4px 0 0">Execution Feed</h2></div><a class="pill" href="/trades">View All</a></div>
        <table><thead><tr><th>Date</th><th>Symbol</th><th>Side</th><th>Setup</th><th>Net P&L</th><th>R</th></tr></thead><tbody>{recent_rows}</tbody></table>
      </section>
    """
    return shell("Dashboard", "dashboard", body, account.name if account else "All Accounts", user.name or user.email)


@app.get("/", response_class=HTMLResponse)
@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(account_id: str | None = Query(default=None)) -> str:
    return render_dashboard(account_id)


@app.get("/settings")
def settings_home() -> RedirectResponse:
    return RedirectResponse("/settings/import", status_code=status.HTTP_303_SEE_OTHER)


def portfolio_data() -> dict[str, object]:
    session = SessionLocal()
    try:
        user = session.scalar(select(User).order_by(User.created_at.asc()))
        if user is None:
            return {"error": "No users found. Run `python prisma/seed.py` to load demo data."}
        accounts = session.scalars(
            select(Account).where(Account.user_id == user.id, Account.archived_at.is_(None)).order_by(Account.created_at)
        ).all()
        return {
            "user": user,
            "summary": summary(session, user.id),
            "accounts": [
                {
                    "account": account,
                    "summary": summary(session, user.id, account.id),
                }
                for account in accounts
            ],
        }
    except SQLAlchemyError as exc:
        return {"error": str(exc)}
    finally:
        session.close()


@app.get("/dashboard/portfolio", response_class=HTMLResponse)
def portfolio() -> str:
    data = portfolio_data()
    if "error" in data:
        body = f'<section class="empty" style="margin-top:18px">{escape(str(data["error"]))}</section>'
        return shell("Portfolio", "portfolio", body)
    user = data["user"]
    metrics = data["summary"]
    account_cards = ""
    for item in data["accounts"]:
        account = item["account"]
        account_summary = item["summary"]
        utilisation = Decimal("0")
        if account.max_daily_loss and account_summary["net_pnl"] < 0:
            utilisation = min(abs(Decimal(account_summary["net_pnl"])) / account.max_daily_loss * Decimal("100"), Decimal("100"))
        account_cards += f"""
          <article class="card">
            <div class="section-head">
              <div><div class="label">{escape(account.account_type.value)}</div><h2 style="margin:4px 0 0">{escape(account.name)}</h2></div>
              <a class="pill" href="/dashboard?account_id={escape(account.id)}">Open</a>
            </div>
            <div class="grid" style="grid-template-columns:repeat(3,1fr);gap:12px">
              <div><div class="label">Net P&L</div><div class="kpi {tone(account_summary["net_pnl"])}">{money(account_summary["net_pnl"])}</div></div>
              <div><div class="label">Win Rate</div><div class="kpi">{number(account_summary["win_rate"], "%")}</div></div>
              <div><div class="label">Trades</div><div class="kpi">{account_summary["trades_count"]}</div></div>
            </div>
            <div style="margin-top:14px">
              <div class="section-head" style="margin-bottom:6px"><span class="label">Daily Loss Utilisation</span><span class="small muted">{number(utilisation, "%")}</span></div>
              <div class="bar"><span style="width:{utilisation}%"></span></div>
            </div>
          </article>
        """
    if not account_cards:
        account_cards = '<section class="empty">No trading accounts yet. Create one during onboarding to activate the portfolio view.</section>'
    body = f"""
      <section class="grid kpis">
        {kpi_card("Portfolio Net P&L", money(metrics["net_pnl"]), tone(metrics["net_pnl"]), "All accounts")}
        {kpi_card("Portfolio Win Rate", number(metrics["win_rate"], "%"), "neutral", "Closed trades")}
        {kpi_card("Profit Factor", number(metrics["profit_factor"]), "neutral", "All strategies")}
        {kpi_card("Total Trades", str(metrics["trades_count"]), "neutral", "Across accounts")}
        {kpi_card("Avg R", number(metrics["average_r"], "R"), tone(metrics["average_r"]), "Book expectancy")}
        {kpi_card("Avg Hold", number(Decimal(metrics["average_hold_seconds"]) / Decimal("3600"), "h"), "neutral", "Portfolio")}
      </section>
      <section class="grid accounts">{account_cards}</section>
    """
    return shell("Portfolio", "portfolio", body, "All Accounts", user.name or user.email)


def first_user(session) -> User | None:
    return session.scalar(select(User).order_by(User.created_at.asc()))


def trade_filters(status_filter: str | None, account_id: str | None) -> str:
    current = (status_filter or "ALL").upper()
    tabs = filter_tabs(
        [
            ("All", "/trades", current == "ALL"),
            ("Open", "/trades?status_filter=OPEN", current == "OPEN"),
            ("Closed", "/trades?status_filter=CLOSED", current == "CLOSED"),
            ("Cancelled", "/trades?status_filter=CANCELLED", current == "CANCELLED"),
        ]
    )
    extra = '<a class="btn btn-primary" href="/trades/new">Log Trade</a>'
    if account_id:
        extra += ' <span class="badge">Account filter active</span>'
    return f'<div class="toolbar page-block">{tabs}{extra}</div>'


def load_trade_page_data(status_filter: str | None, account_id: str | None) -> dict[str, object]:
    session = SessionLocal()
    try:
        user = first_user(session)
        if user is None:
            return {"error": "No users found. Run `python prisma/seed.py` to load demo data."}
        query = (
            select(Trade)
            .options(selectinload(Trade.metrics), selectinload(Trade.instrument), selectinload(Trade.account))
            .where(Trade.user_id == user.id)
            .order_by(Trade.opened_at.desc())
        )
        if status_filter:
            query = query.where(Trade.status == TradeStatus(status_filter))
        if account_id:
            query = query.where(Trade.account_id == account_id)
        trades = session.scalars(query.limit(200)).all()
        return {"user": user, "trades": trades}
    except (SQLAlchemyError, ValueError) as exc:
        return {"error": str(exc)}
    finally:
        session.close()


@app.get("/trades", response_class=HTMLResponse)
def trades(status_filter: str | None = Query(default=None), account_id: str | None = Query(default=None)) -> str:
    data = load_trade_page_data(status_filter, account_id)
    if "error" in data:
        return shell("Trades", "trades", f'<section class="empty" style="margin-top:18px">{escape(str(data["error"]))}</section>')
    rows = "".join(
        f"""<tr>
          <td>{escape(trade.opened_at.strftime("%Y-%m-%d %H:%M"))}</td>
          <td><a class="pill" href="/trades/{escape(trade.id)}">{escape(trade.instrument.symbol)}</a></td>
          <td>{side_badge(trade.direction.value)}</td>
          <td>{escape(trade.account.name)}</td>
          <td>{escape(trade.setup_tag or "Unassigned")}</td>
          <td>{money(trade.metrics.average_entry if trade.metrics else trade.planned_entry)}</td>
          <td>{money(trade.metrics.average_exit if trade.metrics else None)}</td>
          <td class="{tone(trade.metrics.realized_pnl if trade.metrics else 0)}">{money(trade.metrics.realized_pnl if trade.metrics else 0)}</td>
          <td>{number(trade.metrics.r_multiple if trade.metrics else 0, "R")}</td>
          <td>{status_badge(trade.status.value)}</td>
        </tr>"""
        for trade in data["trades"]
    ) or '<tr><td colspan="10" class="muted">No trades match these filters.</td></tr>'
    body = f"""
      {trade_filters(status_filter, account_id)}
      <section class="card" style="margin-top:16px">
        <div class="section-head"><div><div class="label">Trade History</div><h2 style="margin:4px 0 0">Closed and Open Trades</h2></div></div>
        <table><thead><tr><th>Opened</th><th>Symbol</th><th>Side</th><th>Account</th><th>Setup</th><th>Entry</th><th>Exit</th><th>Net P&L</th><th>R</th><th>Status</th></tr></thead><tbody>{rows}</tbody></table>
      </section>
    """
    user = data["user"]
    return shell("Trades", "trades", body, "Trade History", user.name or user.email)


def new_trade_form_data() -> dict[str, object]:
    session = SessionLocal()
    try:
        user = first_user(session)
        if user is None:
            return {"error": "No users found. Run `python prisma/seed.py` to load demo data."}
        accounts = session.scalars(select(Account).where(Account.user_id == user.id, Account.archived_at.is_(None))).all()
        instruments = session.scalars(select(Instrument).order_by(Instrument.asset_class, Instrument.symbol).limit(200)).all()
        return {"user": user, "accounts": accounts, "instruments": instruments}
    except SQLAlchemyError as exc:
        return {"error": str(exc)}
    finally:
        session.close()


@app.get("/trades/new", response_class=HTMLResponse)
def new_trade() -> str:
    data = new_trade_form_data()
    if "error" in data:
        return shell("New Trade", "trades", f'<section class="empty" style="margin-top:18px">{escape(str(data["error"]))}</section>')
    account_options = "".join(f'<option value="{escape(account.id)}">{escape(account.name)}</option>' for account in data["accounts"])
    instrument_options = "".join(
        f'<option value="{escape(instrument.id)}">{escape(instrument.symbol)} · {escape(instrument.asset_class.value)}</option>'
        for instrument in data["instruments"]
    )
    body = f"""
      <form class="card" style="margin-top:16px" method="post" action="/trades/new">
        <div class="section-head"><div><div class="label">Manual Entry</div><h2 style="margin:4px 0 0">Log a Trade</h2></div><button class="primary" type="submit">Save Trade</button></div>
        <div class="form-grid">
          <div class="field"><label>Account</label><select name="account_id" required>{account_options}</select></div>
          <div class="field"><label>Instrument</label><select name="instrument_id" required>{instrument_options}</select></div>
          <div class="field"><label>Direction</label><select name="direction"><option>LONG</option><option>SHORT</option></select></div>
          <div class="field"><label>Status</label><select name="trade_status"><option>OPEN</option><option>CLOSED</option></select></div>
          <div class="field"><label>Setup Tag</label><input name="setup_tag" placeholder="ORB, VWAP Reclaim" /></div>
          <div class="field"><label>Timeframe</label><select name="timeframe"><option>1m</option><option>5m</option><option>15m</option><option>1h</option><option>4h</option><option>D</option></select></div>
          <div class="field"><label>Opened At</label><input type="datetime-local" name="opened_at" required /></div>
          <div class="field"><label>Entry Price</label><input type="number" step="0.00000001" name="entry_price" required /></div>
          <div class="field"><label>Quantity</label><input type="number" step="0.00000001" name="quantity" required /></div>
          <div class="field"><label>Planned Stop</label><input type="number" step="0.00000001" name="planned_stop" /></div>
          <div class="field"><label>Planned Target</label><input type="number" step="0.00000001" name="planned_target" /></div>
          <div class="field"><label>Entry Fees</label><input type="number" step="0.01" name="entry_fees" value="0" /></div>
          <div class="field"><label>Closed At</label><input type="datetime-local" name="closed_at" /></div>
          <div class="field"><label>Exit Price</label><input type="number" step="0.00000001" name="exit_price" /></div>
          <div class="field"><label>Exit Fees</label><input type="number" step="0.01" name="exit_fees" value="0" /></div>
        </div>
        <div class="field" style="margin-top:14px"><label>Notes</label><textarea name="notes" placeholder="Short thesis, mistake flags, or context"></textarea></div>
      </form>
    """
    user = data["user"]
    return shell("New Trade", "trades", body, "Manual Entry", user.name or user.email)


@app.post("/trades/new")
def create_trade_from_form(
    account_id: str = Form(...),
    instrument_id: str = Form(...),
    direction: Direction = Form(...),
    trade_status: TradeStatus = Form(...),
    opened_at: datetime = Form(...),
    entry_price: Decimal = Form(...),
    quantity: Decimal = Form(...),
    setup_tag: str = Form(""),
    timeframe: str = Form(""),
    planned_stop: Decimal | None = Form(default=None),
    planned_target: Decimal | None = Form(default=None),
    entry_fees: Decimal = Form(default=Decimal("0")),
    closed_at: datetime | None = Form(default=None),
    exit_price: Decimal | None = Form(default=None),
    exit_fees: Decimal = Form(default=Decimal("0")),
    notes: str = Form(""),
) -> RedirectResponse:
    session = SessionLocal()
    try:
        user = first_user(session)
        if user is None:
            raise HTTPException(status_code=400, detail="No demo user available")
        account = session.scalar(select(Account).where(Account.id == account_id, Account.user_id == user.id))
        instrument = session.get(Instrument, instrument_id)
        if account is None or instrument is None:
            raise HTTPException(status_code=400, detail="Invalid account or instrument")
        normalized_closed_at = closed_at.replace(tzinfo=UTC) if closed_at and closed_at.tzinfo is None else closed_at
        normalized_opened_at = opened_at.replace(tzinfo=UTC) if opened_at.tzinfo is None else opened_at
        trade = Trade(
            account_id=account.id,
            user_id=user.id,
            instrument_id=instrument.id,
            direction=direction,
            status=trade_status,
            opened_at=normalized_opened_at,
            closed_at=normalized_closed_at if trade_status == TradeStatus.CLOSED else None,
            setup_tag=setup_tag or None,
            timeframe=timeframe or None,
            planned_entry=entry_price,
            planned_stop=planned_stop,
            planned_target=planned_target,
            planned_rr=compute_planned_rr(direction, entry_price, planned_stop, planned_target),
            commissions=Decimal("0"),
            notes=notes or None,
            mistake_flags=[],
            tags=[],
            is_reviewed=False,
        )
        trade.executions = [
            Execution(
                type=ExecutionType.ENTRY,
                executed_at=normalized_opened_at,
                price=entry_price,
                quantity=quantity,
                fees=entry_fees,
            )
        ]
        if trade_status == TradeStatus.CLOSED and normalized_closed_at is not None and exit_price is not None:
            trade.executions.append(
                Execution(
                    type=ExecutionType.EXIT,
                    executed_at=normalized_closed_at,
                    price=exit_price,
                    quantity=quantity,
                    fees=exit_fees,
                )
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
        user = first_user(session)
        if user is None:
            return shell("Trade Detail", "trades", '<section class="empty" style="margin-top:18px">No users found.</section>')
        trade = session.scalar(
            select(Trade)
            .options(selectinload(Trade.metrics), selectinload(Trade.instrument), selectinload(Trade.account), selectinload(Trade.executions))
            .where(Trade.id == trade_id, Trade.user_id == user.id)
        )
        if trade is None:
            return shell("Trade Detail", "trades", '<section class="empty" style="margin-top:18px">Trade not found.</section>', "Trade", user.name or user.email)
        execution_rows = "".join(
            f"""<tr><td>{escape(row.type.value)}</td><td>{escape(row.executed_at.strftime("%Y-%m-%d %H:%M"))}</td><td>{money(row.price)}</td><td>{number(row.quantity)}</td><td>{money(row.fees)}</td></tr>"""
            for row in trade.executions
        )
        metrics = trade.metrics
        body = f"""
          <section class="grid kpis">
            {kpi_card("Symbol", trade.instrument.symbol, "neutral", trade.instrument.asset_class.value)}
            {kpi_card("Net P&L", money(metrics.realized_pnl if metrics else 0), tone(metrics.realized_pnl if metrics else 0), "Realized")}
            {kpi_card("R Multiple", number(metrics.r_multiple if metrics else 0, "R"), tone(metrics.r_multiple if metrics and metrics.r_multiple else 0), "Risk normalized")}
            {kpi_card("Quantity", number(metrics.total_quantity if metrics else 0), "neutral", "Total entry size")}
            {kpi_card("Average Entry", money(metrics.average_entry if metrics else trade.planned_entry), "neutral", "Weighted")}
            {kpi_card("Average Exit", money(metrics.average_exit if metrics else None), "neutral", "Weighted")}
          </section>
          <section class="grid two-col">
            <div class="card">
              <div class="section-head"><div><div class="label">Trade Plan</div><h2 style="margin:4px 0 0">{escape(trade.setup_tag or "Unassigned Setup")}</h2></div><span class="badge">{escape(trade.status.value)}</span></div>
              <p class="muted">Account: {escape(trade.account.name)} · Direction: {escape(trade.direction.value)} · Timeframe: {escape(trade.timeframe or "N/A")}</p>
              <p>{escape(trade.notes or "No notes recorded.")}</p>
              <div class="actions"><a class="pill primary" href="/trades/{escape(trade.id)}/journal">Open Trade Journal</a></div>
            </div>
            <div class="card">
              <div class="section-head"><div><div class="label">Execution Breakdown</div><h2 style="margin:4px 0 0">Orders</h2></div></div>
              <table><thead><tr><th>Type</th><th>Time</th><th>Price</th><th>Qty</th><th>Fees</th></tr></thead><tbody>{execution_rows}</tbody></table>
            </div>
          </section>
        """
        return shell(f"{trade.instrument.symbol} Trade", "trades", body, trade.account.name, user.name or user.email)
    finally:
        session.close()


@app.get("/positions", response_class=HTMLResponse)
def positions() -> str:
    session = SessionLocal()
    try:
        user = first_user(session)
        if user is None:
            return shell("Positions", "positions", '<section class="empty" style="margin-top:18px">No users found. Run seed data first.</section>')
        trades = session.scalars(
            select(Trade)
            .options(selectinload(Trade.metrics), selectinload(Trade.instrument), selectinload(Trade.account))
            .where(Trade.user_id == user.id, Trade.status == TradeStatus.OPEN)
            .order_by(Trade.opened_at.desc())
        ).all()
        rows = ""
        total_risk = Decimal("0")
        for trade in trades:
            metrics = trade.metrics
            entry = metrics.average_entry if metrics else trade.planned_entry or Decimal("0")
            quantity = metrics.total_quantity if metrics else Decimal("0")
            risk = abs(entry - trade.planned_stop) * quantity if trade.planned_stop is not None else Decimal("0")
            total_risk += risk
            rows += f"""
              <tr>
                <td><a class="pill" href="/trades/{escape(trade.id)}">{escape(trade.instrument.symbol)}</a></td>
                <td>{escape(trade.account.name)}</td>
                <td>{side_badge(trade.direction.value)}</td>
                <td>{money(entry)}</td>
                <td>{number(quantity)}</td>
                <td>{money(risk)}</td>
                <td>{escape(trade.opened_at.strftime("%Y-%m-%d %H:%M"))}</td>
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
        body = f"""
          <section class="grid kpis">
            {kpi_card("Open Positions", str(len(trades)), "neutral", "Across accounts")}
            {kpi_card("Total Planned Risk", money(total_risk), "amber" if total_risk else "neutral", "Entry vs stop")}
            {kpi_card("Largest Risk", money(max([total_risk] + [Decimal("0")])), "neutral", "Current view")}
            {kpi_card("Quick Close", "Enabled", "positive", "Manual exit price")}
          </section>
          <section class="card" style="margin-top:16px">
            <div class="section-head"><div><div class="label">Open Positions</div><h2 style="margin:4px 0 0">Live Risk Board</h2></div><a class="pill primary" href="/trades/new">New Trade</a></div>
            <table><thead><tr><th>Symbol</th><th>Account</th><th>Side</th><th>Entry</th><th>Size</th><th>Risk</th><th>Opened</th><th>Action</th></tr></thead><tbody>{rows}</tbody></table>
          </section>
        """
        return shell("Positions", "positions", body, "Open Positions", user.name or user.email)
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
        user = first_user(session)
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
def journal_feed(entry_type: str | None = Query(default=None)) -> str:
    session = SessionLocal()
    try:
        user = first_user(session)
        if user is None:
            return shell("Journal", "journal", '<section class="empty" style="margin-top:18px">No users found. Run seed data first.</section>')
        query = select(JournalEntry).where(JournalEntry.user_id == user.id).order_by(JournalEntry.date.desc(), JournalEntry.created_at.desc())
        if entry_type:
            query = query.where(JournalEntry.type == JournalType(entry_type))
        entries = session.scalars(query.limit(100)).all()
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
          <div class="actions" style="margin-top:16px"><a class="pill primary" href="/journal/new">New Journal</a><a class="pill" href="/journal">All</a>{filters}</div>
          <section class="grid" style="margin-top:16px">{rows}</section>
        """
        return shell("Journal", "journal", body, "Journal Feed", user.name or user.email)
    except (SQLAlchemyError, ValueError) as exc:
        return shell("Journal", "journal", f'<section class="empty" style="margin-top:18px">{escape(str(exc))}</section>')
    finally:
        session.close()


@app.get("/journal/new", response_class=HTMLResponse)
def new_journal(trade_id: str | None = Query(default=None), entry_type: str = Query(default="DAILY")) -> str:
    session = SessionLocal()
    try:
        user = first_user(session)
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
              <div class="field"><label>Date</label><input type="date" name="entry_date" value="{date.today().isoformat()}" required /></div>
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
        user = first_user(session)
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
        user = first_user(session)
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


def analytics_nav(active: str) -> str:
    items = [
        ("Overview", "/analytics", "overview"),
        ("Instruments", "/analytics/instruments", "instruments"),
        ("Setups", "/analytics/setups", "setups"),
        ("Time", "/analytics/time", "time"),
        ("Risk", "/analytics/risk", "risk"),
        ("Streaks", "/analytics/streaks", "streaks"),
    ]
    return f'<div class="page-block">{filter_tabs([(label, href, key == active) for label, href, key in items])}</div>'


def performance_table(rows: list[dict[str, object]], label: str) -> str:
    body = "".join(
        f"""<tr>
          <td>{escape(str(row["key"]))}</td>
          <td>{row["trades"]}</td>
          <td class="{tone(row["netPnl"])}">{money(row["netPnl"])}</td>
          <td>{number(row["winRate"], "%")}</td>
          <td>{number(row["profitFactor"])}</td>
          <td class="{tone(row["averageR"])}">{number(row["averageR"], "R")}</td>
        </tr>"""
        for row in rows
    ) or '<tr><td colspan="6" class="muted">No analytics data yet.</td></tr>'
    return f"""
      <section class="card" style="margin-top:16px">
        <div class="section-head"><div><div class="label">Performance By</div><h2 style="margin:4px 0 0">{escape(label)}</h2></div></div>
        <table><thead><tr><th>Group</th><th>Trades</th><th>Net P&L</th><th>Win Rate</th><th>Profit Factor</th><th>Avg R</th></tr></thead><tbody>{body}</tbody></table>
      </section>
    """


def daily_bars(rows: list[DailyStats]) -> str:
    if not rows:
        return '<div class="empty">Daily P&L bars will appear after trades are closed.</div>'
    max_abs = max(abs(row.net_pnl) for row in rows) or Decimal("1")
    bars = "".join(
        f"""<div title="{row.date.isoformat()} {money(row.net_pnl)}" style="display:flex;align-items:center;gap:8px;margin:8px 0">
          <span class="small muted" style="width:76px">{row.date.strftime("%m-%d")}</span>
          <div class="bar" style="flex:1"><span style="width:{abs(row.net_pnl) / max_abs * Decimal("100")}%;background:var(--green)" class="{"positive" if row.net_pnl >= 0 else "negative"}"></span></div>
          <span class="small {tone(row.net_pnl)}" style="width:90px;text-align:right">{money(row.net_pnl)}</span>
        </div>"""
        for row in rows[-30:]
    )
    return bars


def heatmap(rows: list[dict[str, object]]) -> str:
    if not rows:
        return '<div class="empty">Heat map data will appear after enough trades are logged.</div>'
    max_abs = max(abs(Decimal(row["netPnl"])) for row in rows) or Decimal("1")
    cells = ""
    for row in rows:
        net_pnl = Decimal(row["netPnl"])
        alpha = max(Decimal("0.12"), abs(net_pnl) / max_abs * Decimal("0.55"))
        rgb = "0,217,126" if net_pnl >= 0 else "255,69,96"
        cells += f"""<div class="card" style="padding:12px;background:rgba({rgb},{alpha})">
          <div class="label">{escape(str(row["key"]))}</div>
          <div class="{tone(net_pnl)}" style="font-family:ui-monospace,Menlo,monospace">{money(net_pnl)}</div>
          <div class="muted small">{row["trades"]} trades · {number(row["winRate"], "%")} win</div>
        </div>"""
    return f'<div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px">{cells}</div>'


def analytics_data() -> dict[str, object]:
    session = SessionLocal()
    try:
        user = first_user(session)
        if user is None:
            return {"error": "No users found. Run `python prisma/seed.py` to load demo data."}
        accounts = session.scalars(select(Account).where(Account.user_id == user.id, Account.archived_at.is_(None))).all()
        daily = session.scalars(
            select(DailyStats)
            .where(DailyStats.account_id.in_([account.id for account in accounts]))
            .order_by(DailyStats.date.asc())
        ).all() if accounts else []
        snapshots = session.scalars(
            select(AccountSnapshot)
            .where(AccountSnapshot.account_id.in_([account.id for account in accounts]))
            .order_by(AccountSnapshot.date.asc())
        ).all() if accounts else []
        return {
            "user": user,
            "summary": summary(session, user.id),
            "setups": grouped_performance(session, user.id, "setup"),
            "instruments": grouped_performance(session, user.id, "instrument"),
            "weekday": grouped_performance(session, user.id, "weekday"),
            "hour": grouped_performance(session, user.id, "hour"),
            "streaks": streaks(session, user.id),
            "daily": daily,
            "snapshots": snapshots,
        }
    except SQLAlchemyError as exc:
        return {"error": str(exc)}
    finally:
        session.close()


@app.get("/analytics", response_class=HTMLResponse)
@app.get("/analytics/overview", response_class=HTMLResponse)
def analytics_overview() -> str:
    data = analytics_data()
    if "error" in data:
        return shell("Analytics", "analytics", f'<section class="empty" style="margin-top:18px">{escape(str(data["error"]))}</section>')
    metrics = data["summary"]
    body = f"""
      {analytics_nav("overview")}
      <section class="grid kpis">
        {kpi_card("Net P&L", money(metrics["net_pnl"]), tone(metrics["net_pnl"]), "All accounts")}
        {kpi_card("Win Rate", number(metrics["win_rate"], "%"), "neutral", "Closed trades")}
        {kpi_card("Profit Factor", number(metrics["profit_factor"]), "neutral", "Book quality")}
        {kpi_card("Average R", number(metrics["average_r"], "R"), tone(metrics["average_r"]), "Risk normalized")}
        {kpi_card("Trades", str(metrics["trades_count"]), "neutral", "Sample size")}
        {kpi_card("Avg Hold", number(Decimal(metrics["average_hold_seconds"]) / Decimal("3600"), "h"), "neutral", "Closed trades")}
      </section>
      <section class="grid two-col">
        <div class="card"><div class="section-head"><div><div class="label">Equity</div><h2 style="margin:4px 0 0">Combined Curve</h2></div></div>{sparkline([row.balance for row in data["snapshots"]])}</div>
        <div class="card"><div class="section-head"><div><div class="label">Daily P&L</div><h2 style="margin:4px 0 0">Last 30 Sessions</h2></div></div>{daily_bars(data["daily"])}</div>
      </section>
      {performance_table(data["setups"][:8], "Setup Tag")}
    """
    user = data["user"]
    return shell("Analytics", "analytics", body, "Analytics", user.name or user.email)


@app.get("/analytics/instruments", response_class=HTMLResponse)
def analytics_instruments() -> str:
    data = analytics_data()
    if "error" in data:
        return shell("Analytics", "analytics", f'<section class="empty" style="margin-top:18px">{escape(str(data["error"]))}</section>')
    user = data["user"]
    return shell("By Instrument", "analytics", analytics_nav("instruments") + performance_table(data["instruments"], "Instrument"), "Analytics", user.name or user.email)


@app.get("/analytics/setups", response_class=HTMLResponse)
def analytics_setups() -> str:
    data = analytics_data()
    if "error" in data:
        return shell("Analytics", "analytics", f'<section class="empty" style="margin-top:18px">{escape(str(data["error"]))}</section>')
    user = data["user"]
    return shell("By Setup", "analytics", analytics_nav("setups") + performance_table(data["setups"], "Setup Tag"), "Analytics", user.name or user.email)


@app.get("/analytics/time", response_class=HTMLResponse)
def analytics_time() -> str:
    data = analytics_data()
    if "error" in data:
        return shell("Analytics", "analytics", f'<section class="empty" style="margin-top:18px">{escape(str(data["error"]))}</section>')
    body = f"""
      {analytics_nav("time")}
      <section class="grid two-col">
        <div class="card"><div class="section-head"><div><div class="label">Day of Week</div><h2 style="margin:4px 0 0">Session Heat Map</h2></div></div>{heatmap(data["weekday"])}</div>
        <div class="card"><div class="section-head"><div><div class="label">Hour of Day</div><h2 style="margin:4px 0 0">Timing Edge</h2></div></div>{heatmap(data["hour"])}</div>
      </section>
    """
    user = data["user"]
    return shell("By Time", "analytics", body, "Analytics", user.name or user.email)


@app.get("/analytics/risk", response_class=HTMLResponse)
def analytics_risk() -> str:
    data = analytics_data()
    if "error" in data:
        return shell("Analytics", "analytics", f'<section class="empty" style="margin-top:18px">{escape(str(data["error"]))}</section>')
    snapshots = data["snapshots"]
    max_drawdown = min((row.drawdown_pct for row in snapshots), default=Decimal("0"))
    worst_day = min((row.net_pnl for row in data["daily"]), default=Decimal("0"))
    best_day = max((row.net_pnl for row in data["daily"]), default=Decimal("0"))
    body = f"""
      {analytics_nav("risk")}
      <section class="grid kpis">
        {kpi_card("Max Drawdown", number(max_drawdown, "%"), "negative" if max_drawdown < 0 else "neutral", "Underwater")}
        {kpi_card("Worst Day", money(worst_day), tone(worst_day), "Daily stats")}
        {kpi_card("Best Day", money(best_day), tone(best_day), "Daily stats")}
        {kpi_card("Tracked Days", str(len(data["daily"])), "neutral", "Computed")}
      </section>
      <section class="grid two-col">
        <div class="card"><div class="section-head"><div><div class="label">Drawdown</div><h2 style="margin:4px 0 0">Equity Underwater</h2></div></div>{sparkline([row.drawdown_pct for row in snapshots])}</div>
        <div class="card"><div class="section-head"><div><div class="label">Distribution</div><h2 style="margin:4px 0 0">Daily P&L</h2></div></div>{daily_bars(data["daily"])}</div>
      </section>
    """
    user = data["user"]
    return shell("Risk Analytics", "analytics", body, "Analytics", user.name or user.email)


@app.get("/analytics/streaks", response_class=HTMLResponse)
def analytics_streaks() -> str:
    data = analytics_data()
    if "error" in data:
        return shell("Analytics", "analytics", f'<section class="empty" style="margin-top:18px">{escape(str(data["error"]))}</section>')
    streak_data = data["streaks"]
    body = f"""
      {analytics_nav("streaks")}
      <section class="grid kpis">
        {kpi_card("Longest Win Streak", str(streak_data["longestWinStreak"]), "positive", "Closed trades")}
        {kpi_card("Longest Loss Streak", str(streak_data["longestLossStreak"]), "negative", "Closed trades")}
        {kpi_card("Current Streak", str(streak_data["currentStreak"]), "neutral", str(streak_data["currentKind"]).title())}
      </section>
      {performance_table(data["setups"][:10], "Setup Stability")}
    """
    user = data["user"]
    return shell("Streaks", "analytics", body, "Analytics", user.name or user.email)


def account_options(accounts: list[Account]) -> str:
    return "".join(f'<option value="{escape(account.id)}">{escape(account.name)} · {escape(account.broker)}</option>' for account in accounts)


@app.get("/settings/import", response_class=HTMLResponse)
def import_settings() -> str:
    session = SessionLocal()
    try:
        user = first_user(session)
        if user is None:
            return shell("Import", "settings", '<section class="empty" style="margin-top:18px">No users found. Run seed data first.</section>')
        accounts = session.scalars(select(Account).where(Account.user_id == user.id, Account.archived_at.is_(None))).all()
        account_ids = [account.id for account in accounts]
        history = session.scalars(
            select(BrokerSync)
            .where(BrokerSync.account_id.in_(account_ids))
            .order_by(BrokerSync.created_at.desc())
            .limit(25)
        ).all() if account_ids else []
        history_rows = "".join(
            f"""<tr><td>{escape(row.created_at.strftime("%Y-%m-%d %H:%M"))}</td><td>{escape(row.broker)}</td><td>{escape(row.sync_type.value)}</td><td><span class="badge">{escape(row.status.value)}</span></td><td>{escape(row.error_message or "")}</td></tr>"""
            for row in history
        ) or '<tr><td colspan="5" class="muted">No imports have been run yet.</td></tr>'
        body = f"""
          <section class="grid two-col">
            <form class="card" style="margin-top:16px" method="post" action="/settings/import/preview" enctype="multipart/form-data">
              <div class="section-head"><div><div class="label">Broker CSV</div><h2 style="margin:4px 0 0">Upload and Preview</h2></div><button class="primary" type="submit">Parse CSV</button></div>
              <div class="field"><label>Account</label><select name="account_id" required>{account_options(accounts)}</select></div>
              <div class="field" style="margin-top:12px"><label>Broker Format</label><select name="broker_format"><option>Generic CSV</option><option>IBKR Activity Statement</option><option>TD Ameritrade</option><option>TradeStation</option><option>NinjaTrader</option><option>MT4/MT5</option></select></div>
              <div class="field" style="margin-top:12px"><label>CSV File</label><input type="file" name="file" accept=".csv,text/csv" required /></div>
              <p class="muted small">Supported columns include symbol, datetime, side, price, quantity, fees, and order id. Common broker aliases are normalized automatically.</p>
            </form>
            <section class="card" style="margin-top:16px">
              <div class="section-head"><div><div class="label">Manual Bulk Entry</div><h2 style="margin:4px 0 0">Spreadsheet Paste</h2></div></div>
              <form method="post" action="/settings/import/paste">
                <div class="field"><label>Account</label><select name="account_id" required>{account_options(accounts)}</select></div>
                <div class="field" style="margin-top:12px"><label>Tab-Separated Rows</label><textarea name="rows" placeholder="symbol\tdatetime\tside\tprice\tquantity\tfees"></textarea></div>
                <button style="margin-top:12px" type="submit">Preview Paste</button>
              </form>
            </section>
          </section>
          <section class="card" style="margin-top:16px">
            <div class="section-head"><div><div class="label">Import History</div><h2 style="margin:4px 0 0">Broker Syncs</h2></div></div>
            <table><thead><tr><th>Created</th><th>Broker</th><th>Type</th><th>Status</th><th>Error</th></tr></thead><tbody>{history_rows}</tbody></table>
          </section>
        """
        return shell("Import", "settings", body, "Settings", user.name or user.email)
    finally:
        session.close()


@app.post("/settings/import/preview", response_class=HTMLResponse)
async def import_preview(
    account_id: str = Form(...),
    broker_format: str = Form("Generic CSV"),
    file: UploadFile = File(...),
) -> str:
    session = SessionLocal()
    try:
        user = first_user(session)
        if user is None:
            raise HTTPException(status_code=400, detail="No demo user available")
        account = session.scalar(select(Account).where(Account.id == account_id, Account.user_id == user.id))
        if account is None:
            raise HTTPException(status_code=404, detail="Account not found")
        raw = await file.read()
        rows = parse_broker_csv(raw)
        broker_ids = [row.broker_id for row in rows if row.broker_id]
        existing_broker_ids = set(
            session.scalars(select(Execution.broker_id).where(Execution.broker_id.in_(broker_ids))).all()
        ) if broker_ids else set()
        sync = BrokerSync(
            account_id=account.id,
            broker=broker_format or account.broker,
            sync_type=BrokerSyncType.CSV,
            last_sync_at=datetime.now(UTC),
            status=BrokerSyncStatus.SUCCESS,
            error_message=None,
        )
        session.add(sync)
        session.commit()
        preview_rows = "".join(
            f"""<tr><td>{escape(row.symbol)}</td><td>{escape(row.executed_at.strftime("%Y-%m-%d %H:%M"))}</td><td>{escape(row.side or "N/A")}</td><td>{money(row.price)}</td><td>{number(row.quantity)}</td><td>{money(row.fees)}</td><td>{escape(row.broker_id or "")}</td><td>{"Duplicate" if row.broker_id in existing_broker_ids else "New"}</td></tr>"""
            for row in rows[:100]
        ) or '<tr><td colspan="8" class="muted">No valid execution rows were found.</td></tr>'
        body = f"""
          <div class="actions" style="margin-top:16px"><a class="pill" href="/settings/import">Back to Import</a><span class="badge">{len(rows)} matched rows</span><span class="badge">{len(existing_broker_ids)} duplicate broker IDs</span></div>
          <section class="card" style="margin-top:16px">
            <div class="section-head"><div><div class="label">Preview</div><h2 style="margin:4px 0 0">{escape(file.filename or "CSV Import")}</h2></div><span class="badge">Sync {escape(sync.id[:8])}</span></div>
            <table><thead><tr><th>Symbol</th><th>Time</th><th>Side</th><th>Price</th><th>Qty</th><th>Fees</th><th>Broker ID</th><th>Status</th></tr></thead><tbody>{preview_rows}</tbody></table>
          </section>
        """
        return shell("Import Preview", "settings", body, account.name, user.name or user.email)
    except Exception as exc:
        if "account" in locals():
            session.add(
                BrokerSync(
                    account_id=account.id,
                    broker=broker_format,
                    sync_type=BrokerSyncType.CSV,
                    last_sync_at=datetime.now(UTC),
                    status=BrokerSyncStatus.FAILED,
                    error_message=str(exc),
                )
            )
            session.commit()
        raise
    finally:
        session.close()


@app.post("/settings/import/paste")
def paste_preview(account_id: str = Form(...), rows: str = Form("")) -> RedirectResponse:
    del account_id, rows
    return RedirectResponse("/settings/import", status_code=status.HTTP_303_SEE_OTHER)


def safe_upload_name(content_type: str | None) -> str:
    # Derive the extension from the validated MIME type, never the client filename,
    # so StaticFiles can't be tricked into serving e.g. an uploaded .html page.
    return f"{uuid4().hex}{UPLOAD_EXTENSIONS[content_type or '']}"


def validate_upload(file: UploadFile) -> None:
    if not file.filename or "/" in file.filename or "\\" in file.filename:
        raise HTTPException(status_code=400, detail="Invalid file name")
    if file.content_type not in ALLOWED_UPLOAD_TYPES:
        raise HTTPException(status_code=400, detail="Unsupported upload type")


@app.get("/settings/uploads", response_class=HTMLResponse)
def uploads_page() -> str:
    session = SessionLocal()
    try:
        user = first_user(session)
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
            f'<option value="{escape(trade.id)}">{escape(trade.instrument.symbol)} · {escape(trade.opened_at.strftime("%Y-%m-%d"))}</option>'
            for trade in trades
        )
        journal_options = '<option value="">No linked journal</option>' + "".join(
            f'<option value="{escape(journal.id)}">{escape(journal.title)}</option>' for journal in journals
        )
        attachment_rows = "".join(
            f"""<tr><td><a class="pill" href="{escape(row.url)}" target="_blank">{escape(row.file_name)}</a></td><td>{row.file_size:,}</td><td>{escape(row.mime_type)}</td><td>{escape(row.created_at.strftime("%Y-%m-%d %H:%M"))}</td></tr>"""
            for row in attachments
        ) or '<tr><td colspan="4" class="muted">No uploads yet.</td></tr>'
        body = f"""
          <section class="grid two-col">
            <form class="card" style="margin-top:16px" method="post" action="/settings/uploads" enctype="multipart/form-data">
              <div class="section-head"><div><div class="label">Screenshots</div><h2 style="margin:4px 0 0">Upload Chart Image</h2></div><button class="primary" type="submit">Upload</button></div>
              <div class="field"><label>Trade</label><select name="trade_id">{trade_options}</select></div>
              <div class="field" style="margin-top:12px"><label>Journal</label><select name="journal_entry_id">{journal_options}</select></div>
              <div class="field" style="margin-top:12px"><label>Image</label><input type="file" name="file" accept="image/png,image/jpeg,image/webp" required /></div>
              <p class="muted small">Files are stored locally for development and recorded in the attachment table. Cloudflare R2 can replace this storage layer later.</p>
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
        user = first_user(session)
        if user is None:
            raise HTTPException(status_code=400, detail="No demo user available")
        linked_trade_id = trade_id or None
        linked_journal_id = journal_entry_id or None
        validate_upload(file)
        if linked_trade_id:
            trade = session.scalar(select(Trade).where(Trade.id == linked_trade_id, Trade.user_id == user.id))
            if trade is None:
                raise HTTPException(status_code=404, detail="Trade not found")
        if linked_journal_id:
            journal = session.scalar(select(JournalEntry).where(JournalEntry.id == linked_journal_id, JournalEntry.user_id == user.id))
            if journal is None:
                raise HTTPException(status_code=404, detail="Journal not found")
        stored_name = safe_upload_name(file.content_type)
        target = UPLOAD_ROOT / stored_name
        with target.open("wb") as handle:
            shutil.copyfileobj(file.file, handle)
        if target.stat().st_size > MAX_UPLOAD_BYTES:
            target.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail="Upload is too large")
        attachment = Attachment(
            trade_id=linked_trade_id,
            journal_entry_id=linked_journal_id,
            url=f"/uploads-local/{stored_name}",
            file_name=file.filename or stored_name,
            file_size=target.stat().st_size,
            mime_type=file.content_type or "application/octet-stream",
            annotation_data=None,
        )
        session.add(attachment)
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
          <td>{escape(trade.opened_at.strftime("%Y-%m-%d"))}</td>
          <td>{escape(trade.closed_at.strftime("%Y-%m-%d") if trade.closed_at else "")}</td>
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
        user = first_user(session)
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
        user = first_user(session)
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
        user = first_user(session)
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
        user = first_user(session)
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
        user = first_user(session)
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
        user = first_user(session)
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
        user = first_user(session)
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
        user = first_user(session)
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
        user = first_user(session)
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
        user = first_user(session)
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
        user = first_user(session)
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
        user = first_user(session)
        if user is None:
            return shell("Community", "community", '<section class="empty" style="margin-top:18px">No users found. Run seed data first.</section>')
        profile = session.scalar(select(LeaderboardProfile).where(LeaderboardProfile.user_id == user.id))
        shares = session.scalars(
            select(PublicTradeShare).where(PublicTradeShare.user_id == user.id, PublicTradeShare.revoked_at.is_(None)).order_by(PublicTradeShare.created_at.desc()).limit(20)
        ).all()
        share_rows = "".join(
            f"""<tr><td>{escape(share.title)}</td><td><a class="pill" href="/share/{escape(share.slug)}">/share/{escape(share.slug)}</a></td><td>{escape(share.created_at.strftime("%Y-%m-%d"))}</td></tr>"""
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
        user = first_user(session)
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
        user = first_user(session)
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
        user = first_user(session)
        if user is None:
            return shell("Mentor Mode", "community", '<section class="empty" style="margin-top:18px">No users found.</section>')
        accesses = session.scalars(select(MentorAccess).where(MentorAccess.student_user_id == user.id).order_by(MentorAccess.created_at.desc())).all()
        trades = session.scalars(select(Trade).options(selectinload(Trade.instrument)).where(Trade.user_id == user.id).order_by(Trade.opened_at.desc()).limit(100)).all()
        comments = session.scalars(select(MentorComment).where(MentorComment.student_user_id == user.id).order_by(MentorComment.created_at.desc()).limit(50)).all()
        access_options = "".join(
            f'<option value="{escape(access.id)}">{escape(access.mentor_name or access.mentor_email)}</option>' for access in accesses if access.status == MentorAccessStatus.ACTIVE
        )
        trade_options = "".join(
            f'<option value="{escape(trade.id)}">{escape(trade.instrument.symbol)} · {escape(trade.opened_at.strftime("%Y-%m-%d"))}</option>' for trade in trades
        )
        access_rows = "".join(
            f"""<tr><td>{escape(access.mentor_name or "")}</td><td>{escape(access.mentor_email)}</td><td><span class="badge">{escape(access.status.value)}</span></td><td>{'Yes' if access.can_view_journals else 'No'}</td><td>{'Yes' if access.can_comment else 'No'}</td></tr>"""
            for access in accesses
        ) or '<tr><td colspan="5" class="muted">No mentors have been granted access.</td></tr>'
        comment_rows = "".join(
            f"""<tr><td>{escape(comment.created_at.strftime("%Y-%m-%d %H:%M"))}</td><td>{escape(comment.body[:160])}</td><td>{escape(comment.trade_id or "")}</td></tr>"""
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
        user = first_user(session)
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
        user = first_user(session)
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
