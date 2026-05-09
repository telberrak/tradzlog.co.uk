import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from tradzlog_api.deps import current_user, db_session
from tradzlog_api.security import (
    create_access_token,
    generate_one_time_token,
    hash_one_time_token,
    hash_password,
    verify_password,
)
from tradzlog_api.services.analytics import (
    grouped_performance,
    rebuild_daily_stats,
    rebuild_equity_curve,
    streaks,
    summary,
)
from tradzlog_api.services.enqueue import (
    enqueue_ai_pattern_insight,
    enqueue_performance_report,
    enqueue_tax_report,
)
from tradzlog_api.services.hardening import (
    SECURITY_HEADERS,
    check_database,
    check_redis,
    rate_limiter,
)
from tradzlog_api.services.imports import parse_broker_csv
from tradzlog_api.services.metrics import compute_planned_rr, compute_trade_metrics
from tradzlog_api.services.observability import finish_request, init_observability, start_request
from tradzlog_api.services.risk import evaluate_rules
from tradzlog_db.models import (
    Account,
    AccountSnapshot,
    AIInsight,
    Attachment,
    AuthToken,
    AuthTokenPurpose,
    BrokerSync,
    BrokerSyncStatus,
    BrokerSyncType,
    Direction,
    Execution,
    ExecutionType,
    Instrument,
    JournalEntry,
    RuleBreach,
    Trade,
    TradeMetrics,
    TradeStatus,
    TradingRule,
    User,
)
from tradzlog_types.schemas import (
    AccountCreate,
    AccountRead,
    AccountUpdate,
    AnalyticsSummary,
    CloseTradeRequest,
    ExecutionCreate,
    ExecutionRead,
    ForgotPasswordRequest,
    InstrumentCreate,
    InstrumentRead,
    JournalCreate,
    JournalRead,
    LoginRequest,
    MagicLinkRequest,
    RegisterRequest,
    ResetPasswordRequest,
    RuleCreate,
    RuleRead,
    TokenExchangeRequest,
    TokenResponse,
    TradeCreate,
    TradeMetricsRead,
    TradeRead,
    TradeUpdate,
    UserRead,
)

init_observability()
logger = logging.getLogger("tradzlog.api")

app = FastAPI(title="TradzLog API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


SENSITIVE_POST_PATHS = {
    "/api/auth/login",
    "/api/auth/register",
    "/api/auth/verify-email",
    "/api/auth/forgot-password",
    "/api/auth/reset-password",
    "/api/auth/magic-link",
    "/api/auth/magic-link/consume",
    "/api/ai/insights/generate",
    "/api/ai/chat",
    "/api/import/csv",
    "/api/uploads/screenshot",
    "/api/reports/performance-pdf",
    "/api/reports/tax-csv",
    "/api/reports/prop-firm-pdf",
}


@app.middleware("http")
async def security_and_rate_limit(request: Request, call_next):
    request_id, started_at = start_request(request.headers.get("X-Request-ID"))
    client = request.client.host if request.client else "unknown"
    if request.method == "POST" and request.url.path in SENSITIVE_POST_PATHS:
        result = rate_limiter.check(f"api:{request.url.path}:{client}", limit=20, window_seconds=60)
        if not result.allowed:
            retry_after = str(
                max(int((result.reset_at - datetime.now(UTC)).total_seconds()), 1)
            )
            response = Response(
                content='{"detail":"Rate limit exceeded"}',
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                media_type="application/json",
                headers={
                    "Retry-After": retry_after,
                    "X-Request-ID": request_id,
                },
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


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def root() -> dict[str, str]:
    return {
        "name": "TradzLog API",
        "status": "ok",
        "docs": "/docs",
        "web": "http://localhost:8001",
    }


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/readyz")
def readyz(session: Session = Depends(db_session)) -> dict[str, str]:
    return {"status": "ok", "database": check_database(session), "redis": check_redis()}


@app.get("/livez")
def livez() -> dict[str, str]:
    return {"status": "ok"}



def create_auth_token(
    session: Session,
    user: User,
    purpose: AuthTokenPurpose,
    expires_in: timedelta,
) -> str:
    raw_token = generate_one_time_token()
    session.add(
        AuthToken(
            user_id=user.id,
            purpose=purpose,
            token_hash=hash_one_time_token(raw_token),
            expires_at=datetime.now(UTC) + expires_in,
        )
    )
    return raw_token


def consume_auth_token(session: Session, raw_token: str, purpose: AuthTokenPurpose) -> AuthToken:
    token = session.scalar(
        select(AuthToken).where(
            AuthToken.token_hash == hash_one_time_token(raw_token),
            AuthToken.purpose == purpose,
        )
    )
    now = datetime.now(UTC)
    if token is None or token.used_at is not None or token.expires_at <= now:
        raise HTTPException(status_code=400, detail="Invalid or expired token")
    token.used_at = now
    return token


@app.post("/api/auth/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, session: Session = Depends(db_session)) -> User:
    existing = session.scalar(select(User).where(User.email == payload.email.lower()))
    if existing is not None:
        raise HTTPException(status_code=409, detail="Email is already registered")
    user = User(
        email=payload.email.lower(),
        name=payload.name,
        hashed_password=hash_password(payload.password),
    )
    session.add(user)
    session.flush()
    create_auth_token(session, user, AuthTokenPurpose.EMAIL_VERIFICATION, timedelta(hours=24))
    session.commit()
    session.refresh(user)
    return user


@app.post("/api/auth/login", response_model=TokenResponse)
def login(payload: LoginRequest, session: Session = Depends(db_session)) -> TokenResponse:
    user = session.scalar(select(User).where(User.email == payload.email.lower()))
    if user is None or user.hashed_password is None or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return TokenResponse(access_token=create_access_token(user.id))


@app.post("/api/auth/verify-email", response_model=TokenResponse)
def verify_email(payload: TokenExchangeRequest, session: Session = Depends(db_session)) -> TokenResponse:
    auth_token = consume_auth_token(session, payload.token, AuthTokenPurpose.EMAIL_VERIFICATION)
    user = session.get(User, auth_token.user_id)
    if user is None:
        raise HTTPException(status_code=400, detail="Invalid token")
    user.email_verified_at = datetime.now(UTC)
    session.commit()
    return TokenResponse(access_token=create_access_token(user.id))


@app.post("/api/auth/magic-link")
def request_magic_link(payload: MagicLinkRequest, session: Session = Depends(db_session)) -> dict[str, object]:
    user = session.scalar(select(User).where(User.email == payload.email.lower()))
    if user is not None:
        token = create_auth_token(session, user, AuthTokenPurpose.MAGIC_LINK, timedelta(minutes=15))
        session.commit()
        return {"ok": True, "delivery": "email", "devToken": token}
    return {"ok": True, "delivery": "email"}


@app.post("/api/auth/magic-link/consume", response_model=TokenResponse)
def consume_magic_link(payload: TokenExchangeRequest, session: Session = Depends(db_session)) -> TokenResponse:
    auth_token = consume_auth_token(session, payload.token, AuthTokenPurpose.MAGIC_LINK)
    user = session.get(User, auth_token.user_id)
    if user is None:
        raise HTTPException(status_code=400, detail="Invalid token")
    session.commit()
    return TokenResponse(access_token=create_access_token(user.id))


@app.post("/api/auth/logout")
def logout() -> dict[str, bool]:
    return {"ok": True}


@app.post("/api/auth/forgot-password")
def forgot_password(payload: ForgotPasswordRequest, session: Session = Depends(db_session)) -> dict[str, object]:
    user = session.scalar(select(User).where(User.email == payload.email.lower()))
    if user is not None:
        token = create_auth_token(session, user, AuthTokenPurpose.PASSWORD_RESET, timedelta(hours=1))
        session.commit()
        return {"ok": True, "delivery": "email", "devToken": token}
    return {"ok": True, "delivery": "email"}


@app.post("/api/auth/reset-password")
def reset_password(payload: ResetPasswordRequest, session: Session = Depends(db_session)) -> dict[str, bool]:
    auth_token = consume_auth_token(session, payload.token, AuthTokenPurpose.PASSWORD_RESET)
    user = session.get(User, auth_token.user_id)
    if user is None:
        raise HTTPException(status_code=400, detail="Invalid token")
    user.hashed_password = hash_password(payload.password)
    session.commit()
    return {"ok": True}


def owned_account(session: Session, user_id: str, account_id: str) -> Account:
    account = session.scalar(select(Account).where(Account.id == account_id, Account.user_id == user_id))
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return account


@app.get("/api/accounts", response_model=list[AccountRead])
def list_accounts(user: User = Depends(current_user), session: Session = Depends(db_session)) -> list[Account]:
    return list(
        session.scalars(
            select(Account)
            .where(Account.user_id == user.id, Account.archived_at.is_(None))
            .order_by(Account.created_at)
        ).all()
    )


@app.post("/api/accounts", response_model=AccountRead, status_code=status.HTTP_201_CREATED)
def create_account(
    payload: AccountCreate,
    user: User = Depends(current_user),
    session: Session = Depends(db_session),
) -> Account:
    account = Account(user_id=user.id, **payload.model_dump())
    session.add(account)
    session.commit()
    session.refresh(account)
    return account


@app.get("/api/accounts/{account_id}", response_model=AccountRead)
def get_account(
    account_id: str,
    user: User = Depends(current_user),
    session: Session = Depends(db_session),
) -> Account:
    return owned_account(session, user.id, account_id)


@app.patch("/api/accounts/{account_id}", response_model=AccountRead)
def update_account(
    account_id: str,
    payload: AccountUpdate,
    user: User = Depends(current_user),
    session: Session = Depends(db_session),
) -> Account:
    account = owned_account(session, user.id, account_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(account, key, value)
    session.commit()
    session.refresh(account)
    return account


@app.delete("/api/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_account(
    account_id: str,
    user: User = Depends(current_user),
    session: Session = Depends(db_session),
) -> None:
    account = owned_account(session, user.id, account_id)
    account.archived_at = datetime.now(UTC)
    session.commit()


@app.get("/api/instruments", response_model=list[InstrumentRead])
def search_instruments(
    q: str = "",
    user: User = Depends(current_user),
    session: Session = Depends(db_session),
) -> list[Instrument]:
    del user
    query = select(Instrument).order_by(Instrument.symbol).limit(50)
    if q:
        like = f"%{q}%"
        query = (
            select(Instrument)
            .where(or_(Instrument.symbol.ilike(like), Instrument.name.ilike(like)))
            .order_by(Instrument.symbol)
            .limit(50)
        )
    return list(session.scalars(query).all())


@app.post("/api/instruments", response_model=InstrumentRead, status_code=status.HTTP_201_CREATED)
def create_instrument(
    payload: InstrumentCreate,
    user: User = Depends(current_user),
    session: Session = Depends(db_session),
) -> Instrument:
    del user
    instrument = Instrument(**payload.model_dump())
    session.add(instrument)
    session.commit()
    session.refresh(instrument)
    return instrument


def load_trade(session: Session, user_id: str, trade_id: str) -> Trade:
    trade = session.scalar(
        select(Trade)
        .options(selectinload(Trade.executions), selectinload(Trade.metrics), selectinload(Trade.instrument))
        .where(Trade.id == trade_id, Trade.user_id == user_id)
    )
    if trade is None:
        raise HTTPException(status_code=404, detail="Trade not found")
    return trade


def persist_metrics(session: Session, trade: Trade) -> None:
    computed = compute_trade_metrics(trade, list(trade.executions), trade.instrument)
    metrics = trade.metrics
    if metrics is None:
        metrics = TradeMetrics(
            trade_id=trade.id,
            average_entry=computed.average_entry,
            total_quantity=computed.total_quantity,
            realized_pnl=computed.net_pnl,
        )
        session.add(metrics)
    metrics.average_entry = computed.average_entry
    metrics.average_exit = computed.average_exit
    metrics.total_quantity = computed.total_quantity
    metrics.realized_pnl = computed.net_pnl
    metrics.pnl_percent = computed.pnl_percent
    metrics.r_multiple = computed.r_multiple
    metrics.holding_period_seconds = computed.holding_period_seconds


def recompute_trade_side_effects(session: Session, trade: Trade) -> None:
    persist_metrics(session, trade)
    session.flush()
    if trade.status == TradeStatus.CLOSED:
        account = owned_account(session, trade.user_id, trade.account_id)
        rebuild_daily_stats(session, trade.account_id)
        rebuild_equity_curve(session, account)
    evaluate_rules(session, trade)


@app.get("/api/trades", response_model=list[TradeRead])
def list_trades(
    account_id: str | None = None,
    status_filter: TradeStatus | None = Query(default=None, alias="status"),
    page: int = 1,
    limit: int = 50,
    user: User = Depends(current_user),
    session: Session = Depends(db_session),
) -> list[Trade]:
    query = (
        select(Trade)
        .options(selectinload(Trade.executions), selectinload(Trade.metrics))
        .where(Trade.user_id == user.id)
        .order_by(Trade.opened_at.desc())
    )
    if account_id:
        query = query.where(Trade.account_id == account_id)
    if status_filter:
        query = query.where(Trade.status == status_filter)
    return list(session.scalars(query.offset((page - 1) * limit).limit(limit)).all())


@app.post("/api/trades", response_model=TradeRead, status_code=status.HTTP_201_CREATED)
def create_trade(
    payload: TradeCreate,
    user: User = Depends(current_user),
    session: Session = Depends(db_session),
) -> Trade:
    account = session.scalar(select(Account).where(Account.id == payload.account_id, Account.user_id == user.id))
    instrument = session.get(Instrument, payload.instrument_id)
    if account is None or instrument is None:
        raise HTTPException(status_code=400, detail="Invalid account or instrument")
    direction = Direction(payload.direction.value)
    data = payload.model_dump(exclude={"executions", "direction"})
    trade = Trade(
        user_id=user.id,
        direction=direction,
        planned_rr=compute_planned_rr(
            direction,
            payload.planned_entry,
            payload.planned_stop,
            payload.planned_target,
        ),
        **data,
    )
    trade.executions = [
        Execution(type=ExecutionType(row.type.value), **row.model_dump(exclude={"type"}))
        for row in payload.executions
    ]
    session.add(trade)
    session.flush()
    session.refresh(trade, ["executions", "instrument"])
    recompute_trade_side_effects(session, trade)
    session.commit()
    return load_trade(session, user.id, trade.id)


@app.get("/api/trades/{trade_id}", response_model=TradeRead)
def get_trade(
    trade_id: str,
    user: User = Depends(current_user),
    session: Session = Depends(db_session),
) -> Trade:
    return load_trade(session, user.id, trade_id)


@app.patch("/api/trades/{trade_id}", response_model=TradeRead)
def update_trade(
    trade_id: str,
    payload: TradeUpdate,
    user: User = Depends(current_user),
    session: Session = Depends(db_session),
) -> Trade:
    trade = load_trade(session, user.id, trade_id)
    data = payload.model_dump(exclude_unset=True)
    if "direction" in data:
        data["direction"] = Direction(data["direction"].value)
    for key, value in data.items():
        setattr(trade, key, value)
    trade.planned_rr = compute_planned_rr(trade.direction, trade.planned_entry, trade.planned_stop, trade.planned_target)
    recompute_trade_side_effects(session, trade)
    session.commit()
    return load_trade(session, user.id, trade_id)


@app.delete("/api/trades/{trade_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_trade(
    trade_id: str,
    user: User = Depends(current_user),
    session: Session = Depends(db_session),
) -> None:
    trade = load_trade(session, user.id, trade_id)
    session.delete(trade)
    session.commit()


@app.post("/api/trades/{trade_id}/close", response_model=TradeRead)
def close_trade(
    trade_id: str,
    payload: CloseTradeRequest,
    user: User = Depends(current_user),
    session: Session = Depends(db_session),
) -> Trade:
    trade = load_trade(session, user.id, trade_id)
    trade.status = TradeStatus.CLOSED
    trade.closed_at = payload.exit_datetime
    trade.executions.append(
        Execution(
            type=ExecutionType.EXIT,
            executed_at=payload.exit_datetime,
            price=payload.exit_price,
            quantity=payload.quantity,
            fees=payload.fees,
        )
    )
    recompute_trade_side_effects(session, trade)
    session.commit()
    return load_trade(session, user.id, trade_id)


@app.post("/api/trades/{trade_id}/executions", response_model=ExecutionRead, status_code=status.HTTP_201_CREATED)
def add_execution(
    trade_id: str,
    payload: ExecutionCreate,
    user: User = Depends(current_user),
    session: Session = Depends(db_session),
) -> Execution:
    trade = load_trade(session, user.id, trade_id)
    execution = Execution(
        trade_id=trade.id,
        type=ExecutionType(payload.type.value),
        **payload.model_dump(exclude={"type"}),
    )
    session.add(execution)
    session.flush()
    session.refresh(trade, ["executions"])
    recompute_trade_side_effects(session, trade)
    session.commit()
    session.refresh(execution)
    return execution


@app.get("/api/trades/{trade_id}/metrics", response_model=TradeMetricsRead)
def get_trade_metrics(
    trade_id: str,
    user: User = Depends(current_user),
    session: Session = Depends(db_session),
) -> TradeMetrics:
    trade = load_trade(session, user.id, trade_id)
    if trade.metrics is None:
        raise HTTPException(status_code=404, detail="Metrics not available")
    return trade.metrics


@app.get("/api/journals", response_model=list[JournalRead])
def list_journals(
    type: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    trade_id: str | None = None,
    user: User = Depends(current_user),
    session: Session = Depends(db_session),
) -> list[JournalEntry]:
    query = select(JournalEntry).where(JournalEntry.user_id == user.id).order_by(JournalEntry.date.desc())
    if type:
        query = query.where(JournalEntry.type == type)
    if date_from:
        query = query.where(JournalEntry.date >= date_from)
    if date_to:
        query = query.where(JournalEntry.date <= date_to)
    if trade_id:
        query = query.where(JournalEntry.trade_id == trade_id)
    return list(session.scalars(query).all())


@app.post("/api/journals", response_model=JournalRead, status_code=status.HTTP_201_CREATED)
def create_journal(
    payload: JournalCreate,
    user: User = Depends(current_user),
    session: Session = Depends(db_session),
) -> JournalEntry:
    journal = JournalEntry(user_id=user.id, **payload.model_dump())
    session.add(journal)
    session.commit()
    session.refresh(journal)
    return journal


def load_journal(session: Session, user_id: str, journal_id: str) -> JournalEntry:
    journal = session.scalar(
        select(JournalEntry).where(JournalEntry.id == journal_id, JournalEntry.user_id == user_id)
    )
    if journal is None:
        raise HTTPException(status_code=404, detail="Journal not found")
    return journal


@app.get("/api/journals/{journal_id}", response_model=JournalRead)
def get_journal(
    journal_id: str,
    user: User = Depends(current_user),
    session: Session = Depends(db_session),
) -> JournalEntry:
    return load_journal(session, user.id, journal_id)


@app.patch("/api/journals/{journal_id}", response_model=JournalRead)
def update_journal(
    journal_id: str,
    payload: JournalCreate,
    user: User = Depends(current_user),
    session: Session = Depends(db_session),
) -> JournalEntry:
    journal = load_journal(session, user.id, journal_id)
    for key, value in payload.model_dump().items():
        setattr(journal, key, value)
    session.commit()
    session.refresh(journal)
    return journal


@app.delete("/api/journals/{journal_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_journal(
    journal_id: str,
    user: User = Depends(current_user),
    session: Session = Depends(db_session),
) -> None:
    journal = load_journal(session, user.id, journal_id)
    session.delete(journal)
    session.commit()


@app.get("/api/analytics/summary", response_model=AnalyticsSummary)
def analytics_summary(
    account_id: str | None = None,
    user: User = Depends(current_user),
    session: Session = Depends(db_session),
) -> dict[str, Decimal | int]:
    if account_id:
        owned_account(session, user.id, account_id)
    return summary(session, user.id, account_id)


@app.get("/api/analytics/equity-curve")
def equity_curve(
    account_id: str,
    user: User = Depends(current_user),
    session: Session = Depends(db_session),
) -> list[dict[str, object]]:
    owned_account(session, user.id, account_id)
    rows = session.scalars(
        select(AccountSnapshot)
        .where(AccountSnapshot.account_id == account_id)
        .order_by(AccountSnapshot.date)
    ).all()
    return [
        {"date": row.date.isoformat(), "balance": row.balance, "drawdownPct": row.drawdown_pct}
        for row in rows
    ]


@app.get("/api/analytics/by-setup")
def analytics_by_setup(user: User = Depends(current_user), session: Session = Depends(db_session)) -> list[dict[str, object]]:
    return grouped_performance(session, user.id, "setup")


@app.get("/api/analytics/by-instrument")
def analytics_by_instrument(user: User = Depends(current_user), session: Session = Depends(db_session)) -> list[dict[str, object]]:
    return grouped_performance(session, user.id, "instrument")


@app.get("/api/analytics/by-time")
def analytics_by_time(user: User = Depends(current_user), session: Session = Depends(db_session)) -> dict[str, list[dict[str, object]]]:
    return {
        "weekday": grouped_performance(session, user.id, "weekday"),
        "hour": grouped_performance(session, user.id, "hour"),
    }


@app.get("/api/analytics/streaks")
def analytics_streaks(user: User = Depends(current_user), session: Session = Depends(db_session)) -> dict[str, int | str]:
    return streaks(session, user.id)


@app.get("/api/analytics/drawdown")
def analytics_drawdown(
    account_id: str,
    user: User = Depends(current_user),
    session: Session = Depends(db_session),
) -> list[dict[str, object]]:
    return equity_curve(account_id, user, session)


@app.get("/api/positions")
def open_positions(user: User = Depends(current_user), session: Session = Depends(db_session)) -> list[dict[str, object]]:
    trades = session.scalars(
        select(Trade)
        .options(selectinload(Trade.metrics), selectinload(Trade.instrument))
        .where(Trade.user_id == user.id, Trade.status == TradeStatus.OPEN)
        .order_by(Trade.opened_at.desc())
    ).all()
    return [
        {
            "tradeId": trade.id,
            "accountId": trade.account_id,
            "symbol": trade.instrument.symbol,
            "direction": trade.direction,
            "entryPrice": trade.metrics.average_entry if trade.metrics else trade.planned_entry,
            "size": trade.metrics.total_quantity if trade.metrics else Decimal("0"),
            "risk": (
                abs((trade.metrics.average_entry if trade.metrics else trade.planned_entry or Decimal("0")) - trade.planned_stop)
                * (trade.metrics.total_quantity if trade.metrics else Decimal("0"))
                if trade.planned_stop is not None
                else Decimal("0")
            ),
            "openedAt": trade.opened_at,
        }
        for trade in trades
    ]


@app.get("/api/dashboard/portfolio")
def portfolio_dashboard(user: User = Depends(current_user), session: Session = Depends(db_session)) -> dict[str, object]:
    accounts = session.scalars(select(Account).where(Account.user_id == user.id, Account.archived_at.is_(None))).all()
    return {
        "summary": summary(session, user.id),
        "accounts": [
            {
                "id": account.id,
                "name": account.name,
                "broker": account.broker,
                "currency": account.currency,
                "startingBalance": account.starting_balance,
                "summary": summary(session, user.id, account.id),
            }
            for account in accounts
        ],
    }


@app.get("/api/rules", response_model=list[RuleRead])
def list_rules(user: User = Depends(current_user), session: Session = Depends(db_session)) -> list[TradingRule]:
    return list(session.scalars(select(TradingRule).where(TradingRule.user_id == user.id)).all())


@app.post("/api/rules", response_model=RuleRead, status_code=status.HTTP_201_CREATED)
def create_rule(
    payload: RuleCreate,
    user: User = Depends(current_user),
    session: Session = Depends(db_session),
) -> TradingRule:
    rule = TradingRule(user_id=user.id, **payload.model_dump())
    session.add(rule)
    session.commit()
    session.refresh(rule)
    return rule


@app.patch("/api/rules/{rule_id}", response_model=RuleRead)
def update_rule(
    rule_id: str,
    payload: RuleCreate,
    user: User = Depends(current_user),
    session: Session = Depends(db_session),
) -> TradingRule:
    rule = session.scalar(select(TradingRule).where(TradingRule.id == rule_id, TradingRule.user_id == user.id))
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    for key, value in payload.model_dump().items():
        setattr(rule, key, value)
    session.commit()
    session.refresh(rule)
    return rule


@app.delete("/api/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rule(
    rule_id: str,
    user: User = Depends(current_user),
    session: Session = Depends(db_session),
) -> None:
    rule = session.scalar(select(TradingRule).where(TradingRule.id == rule_id, TradingRule.user_id == user.id))
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    session.delete(rule)
    session.commit()


@app.get("/api/rules/breaches")
def list_breaches(user: User = Depends(current_user), session: Session = Depends(db_session)) -> list[dict[str, object]]:
    rows = session.scalars(
        select(RuleBreach).where(RuleBreach.user_id == user.id).order_by(RuleBreach.breached_at.desc())
    ).all()
    return [{"id": row.id, "ruleId": row.rule_id, "value": row.value, "acknowledged": row.acknowledged} for row in rows]


@app.get("/api/ai/insights")
def list_ai_insights(user: User = Depends(current_user), session: Session = Depends(db_session)) -> list[dict[str, object]]:
    rows = session.scalars(select(AIInsight).where(AIInsight.user_id == user.id).order_by(AIInsight.generated_at.desc())).all()
    return [{"id": row.id, "type": row.type, "title": row.title, "content": row.content} for row in rows]


@app.post("/api/ai/insights/generate")
def generate_ai_insights(user: User = Depends(current_user), session: Session = Depends(db_session)) -> dict[str, object]:
    del session
    job = enqueue_ai_pattern_insight(user.id)
    return {"status": "QUEUED", "queue": job.queue, "jobId": job.job_id}


@app.post("/api/ai/chat")
def ai_chat(user: User = Depends(current_user), session: Session = Depends(db_session)) -> dict[str, object]:
    data = summary(session, user.id)
    return {
        "message": (
            f"Your current book shows {data['trades_count']} trades, {data['net_pnl']} net P&L, "
            f"{data['win_rate']}% win rate, and {data['profit_factor']} profit factor."
        )
    }


@app.get("/api/ai/journal-prompts")
def ai_journal_prompts(date: str, user: User = Depends(current_user)) -> list[str]:
    del user
    return [
        f"What changed in your execution quality on {date}?",
        "Which trade best matched your written plan, and why?",
        "What single rule would have improved today's outcome?",
    ]


@app.post("/api/import/csv")
async def import_csv(
    file: UploadFile,
    account_id: str,
    user: User = Depends(current_user),
    session: Session = Depends(db_session),
) -> dict[str, object]:
    account = owned_account(session, user.id, account_id)
    rows = parse_broker_csv(await file.read())
    sync = BrokerSync(
        account_id=account.id,
        broker=account.broker,
        sync_type=BrokerSyncType.CSV,
        last_sync_at=datetime.now(UTC),
        status=BrokerSyncStatus.SUCCESS,
    )
    session.add(sync)
    session.commit()
    return {
        "userId": user.id,
        "syncId": sync.id,
        "fileName": file.filename,
        "status": "PREVIEW_READY",
        "matchedRows": len(rows),
        "unmatchedRows": 0,
        "preview": [row.__dict__ for row in rows[:25]],
    }


@app.get("/api/import/history")
def import_history(user: User = Depends(current_user), session: Session = Depends(db_session)) -> list[dict[str, object]]:
    account_ids = select(Account.id).where(Account.user_id == user.id)
    rows = session.scalars(
        select(BrokerSync).where(BrokerSync.account_id.in_(account_ids)).order_by(BrokerSync.created_at.desc())
    ).all()
    return [
        {
            "id": row.id,
            "accountId": row.account_id,
            "broker": row.broker,
            "syncType": row.sync_type,
            "lastSyncAt": row.last_sync_at,
            "status": row.status,
            "errorMessage": row.error_message,
        }
        for row in rows
    ]


@app.post("/api/uploads/screenshot")
def upload_screenshot(file: UploadFile, user: User = Depends(current_user)) -> dict[str, object]:
    if not file.filename or "/" in file.filename or "\\" in file.filename:
        raise HTTPException(status_code=400, detail="Invalid file name")
    if file.content_type not in {"image/png", "image/jpeg", "image/webp"}:
        raise HTTPException(status_code=400, detail="Unsupported screenshot type")
    return {"userId": user.id, "fileName": file.filename, "url": f"r2://pending/{file.filename}"}


@app.delete("/api/uploads/{upload_id}")
def delete_upload(
    upload_id: str,
    user: User = Depends(current_user),
    session: Session = Depends(db_session),
) -> dict[str, object]:
    attachment = session.get(Attachment, upload_id)
    if attachment is None:
        raise HTTPException(status_code=404, detail="Upload not found")
    if attachment.trade_id:
        load_trade(session, user.id, attachment.trade_id)
    elif attachment.journal_entry_id:
        load_journal(session, user.id, attachment.journal_entry_id)
    else:
        raise HTTPException(status_code=404, detail="Upload not found")
    session.delete(attachment)
    session.commit()
    return {"userId": user.id, "uploadId": upload_id, "deleted": True}


@app.post("/api/reports/performance-pdf")
def performance_report(user: User = Depends(current_user), session: Session = Depends(db_session)) -> dict[str, object]:
    del session
    job = enqueue_performance_report(user.id)
    return {"status": "QUEUED", "queue": job.queue, "jobId": job.job_id}


@app.post("/api/reports/tax-csv")
def tax_report(user: User = Depends(current_user), session: Session = Depends(db_session)) -> dict[str, object]:
    del session
    job = enqueue_tax_report(user.id)
    return {"status": "QUEUED", "queue": job.queue, "jobId": job.job_id}


@app.post("/api/reports/prop-firm-pdf")
def prop_firm_report(user: User = Depends(current_user), session: Session = Depends(db_session)) -> dict[str, object]:
    accounts = session.scalars(select(Account).where(Account.user_id == user.id)).all()
    prop_accounts = [account for account in accounts if account.prop_firm_name]
    return {
        "status": "READY",
        "accounts": [
            {
                "accountId": account.id,
                "propFirmName": account.prop_firm_name,
                "maxDailyLoss": account.max_daily_loss,
                "maxTotalLoss": account.max_total_loss,
                "dailyProfitTarget": account.daily_profit_target,
            }
            for account in prop_accounts
        ],
    }
