"""End-to-end web tests against a real PostgreSQL database.

They run when DATABASE_URL points at a reachable database (CI's Postgres service, or locally inside
the Docker Compose network) and are skipped otherwise; set REQUIRE_DB_TESTS=1 to fail instead of
skipping. The schema is brought to the latest migration first. Every test uses fresh, unique
emails, so the database never needs cleaning.
"""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError

from tradzlog_db.session import SessionLocal, engine

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = {"Origin": "http://testserver"}


def database_ready() -> bool:
    try:
        with engine.connect() as connection:
            connection.execute(text("select 1"))
    except OperationalError:
        if os.getenv("REQUIRE_DB_TESTS") == "1":
            raise
        return False
    return True


pytestmark = pytest.mark.skipif(not database_ready(), reason="no reachable PostgreSQL (DATABASE_URL)")


@pytest.fixture(scope="module", autouse=True)
def migrated() -> None:
    from alembic import command
    from alembic.config import Config

    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")


@pytest.fixture(autouse=True)
def fresh_rate_limits(monkeypatch) -> None:
    """Each test gets its own limiter: the suite signs up more users per minute than the real limit allows."""
    import tradzlog_web.main as web_main
    from tradzlog_api.services.hardening import InMemoryRateLimiter

    monkeypatch.setattr(web_main, "rate_limiter", InMemoryRateLimiter())


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    import tradzlog_web.main as web_main

    with TestClient(web_main.app, base_url="http://testserver") as test_client:
        yield test_client


def new_client():
    from fastapi.testclient import TestClient

    import tradzlog_web.main as web_main

    return TestClient(web_main.app, base_url="http://testserver")


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([0-9a-f]+)"', html)
    assert match, "page has no CSRF field"
    return match.group(1)


def sign_up(client, name: str = "Trader") -> str:
    email = f"{uuid4().hex[:12]}@example.com"
    response = client.post(
        "/signup",
        data={"name": name, "email": email, "password": "correct-horse-1", "password2": "correct-horse-1"},
        headers=ORIGIN,
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    assert response.headers["location"] == "/dashboard"
    return email


# --------------------------------------------------------------------- sign-in flows


def test_visitors_are_sent_to_sign_in(client) -> None:
    assert client.get("/dashboard", follow_redirects=False).headers["location"] == "/login"
    response = client.get("/trades?status_filter=OPEN", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=/trades%3Fstatus_filter%3DOPEN"
    assert client.get("/login").status_code == 200
    assert client.get("/livez").status_code == 200
    landing = client.get("/", follow_redirects=False)
    assert landing.status_code == 200 and 'href="/signup"' in landing.text


def test_sign_up_sign_out_and_sign_in(client) -> None:
    email = sign_up(client, "Ada Trader")
    cookie = client.cookies.get("tz_session")
    assert cookie and len(cookie) > 30
    page = client.get("/dashboard")
    assert page.status_code == 200 and "Ada Trader" in page.text

    out = client.post("/logout", data={"csrf_token": csrf_from(page.text)}, follow_redirects=False)
    assert out.status_code == 303 and out.headers["location"] == "/login"
    assert client.get("/dashboard", follow_redirects=False).status_code == 303

    wrong = client.post("/login", data={"email": email, "password": "nope-nope-1"}, headers=ORIGIN)
    assert wrong.status_code == 400 and "Email or password is incorrect" in wrong.text
    right = client.post(
        "/login",
        data={"email": email.upper(), "password": "correct-horse-1", "next": "/analytics"},
        headers=ORIGIN,
        follow_redirects=False,
    )
    assert right.status_code == 303 and right.headers["location"] == "/analytics"


@pytest.mark.parametrize("target", ["//evil.example/x", "https://evil.example/", "/\\evil.example", "javascript:alert(1)"])
def test_sign_in_never_redirects_off_site(client, target: str) -> None:
    email = sign_up(client)
    client.cookies.clear()
    response = client.post(
        "/login", data={"email": email, "password": "correct-horse-1", "next": target}, headers=ORIGIN, follow_redirects=False
    )
    assert response.headers["location"] == "/dashboard"


def test_forms_need_csrf_token_and_same_origin(client) -> None:
    email = sign_up(client)
    assert client.post("/logout", follow_redirects=False).status_code == 403
    assert client.post("/logout", data={"csrf_token": "0" * 64}, follow_redirects=False).status_code == 403
    client.cookies.clear()
    cross_site = client.post(
        "/login", data={"email": email, "password": "correct-horse-1"}, headers={"Origin": "https://evil.example"}
    )
    assert cross_site.status_code == 403
    assert client.post("/login", data={"email": email, "password": "correct-horse-1"}).status_code == 403  # no Origin
    # Signed-out POSTs to app forms go to sign-in, not to the handler.
    assert client.post("/trades/new", data={}, follow_redirects=False).headers["location"] == "/login"


def test_password_change_signs_out_other_devices(client) -> None:
    email = sign_up(client)
    with new_client() as laptop:
        laptop.post("/login", data={"email": email, "password": "correct-horse-1"}, headers=ORIGIN)
        assert laptop.get("/dashboard", follow_redirects=False).status_code == 200

        page = client.get("/settings/security")
        assert page.status_code == 200 and "This device" in page.text
        changed = client.post(
            "/settings/security/password",
            data={"csrf_token": csrf_from(page.text), "current": "correct-horse-1", "new": "battery-staple-2", "new2": "battery-staple-2"},
            follow_redirects=False,
        )
        assert "message=" in changed.headers["location"]
        assert client.get("/dashboard", follow_redirects=False).status_code == 200  # this device stays signed in
        assert laptop.get("/dashboard", follow_redirects=False).status_code == 303  # the other one is out
    client.cookies.clear()
    assert client.post("/login", data={"email": email, "password": "battery-staple-2"}, headers=ORIGIN, follow_redirects=False).status_code == 303


def test_invite_only_sign_up(client, monkeypatch) -> None:
    from tradzlog_api.config import settings

    monkeypatch.setattr(settings, "registration_open", False)
    monkeypatch.setattr(settings, "registration_invite_code", "beta-invite-123")
    form = {"email": f"{uuid4().hex[:10]}@example.com", "password": "correct-horse-1", "password2": "correct-horse-1"}
    assert "Invite code" in client.get("/signup").text
    assert client.post("/signup", data={**form, "invite_code": "wrong"}, headers=ORIGIN).status_code == 403
    ok = client.post("/signup", data={**form, "invite_code": "beta-invite-123"}, headers=ORIGIN, follow_redirects=False)
    assert ok.status_code == 303


# --------------------------------------------------------------------- isolation


def seed_private_book(email: str, marker: str) -> dict[str, str]:
    """Give the user an account, a trade, a journal, a screenshot record, a share and a mentor."""
    from tradzlog_db.models import (
        Account,
        AccountType,
        AssetClass,
        Attachment,
        CashTransaction,
        CashTransactionType,
        Direction,
        Execution,
        ExecutionType,
        Instrument,
        JournalEntry,
        JournalType,
        MentorAccess,
        MentorAccessStatus,
        PublicTradeShare,
        Trade,
        TradeMetrics,
        TradeStatus,
        User,
    )

    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.email == email))
        account = Account(user_id=user.id, name=f"{marker}-account", broker=f"{marker}-broker", currency="USD",
                          starting_balance=Decimal("10000"), account_type=AccountType.LIVE)
        instrument = Instrument(symbol=f"Z{uuid4().hex[:6].upper()}", name="Isolation test", asset_class=AssetClass.STOCK, point_value=Decimal("1"))
        db.add_all([account, instrument])
        db.flush()
        opened = datetime.now(UTC) - timedelta(days=1)
        trade = Trade(account_id=account.id, user_id=user.id, instrument_id=instrument.id, direction=Direction.LONG,
                      status=TradeStatus.OPEN, opened_at=opened, setup_tag=f"{marker}-setup", notes=f"{marker}-notes",
                      planned_entry=Decimal("100"), commissions=Decimal("0"), mistake_flags=[], tags=[], is_reviewed=False)
        trade.executions = [Execution(type=ExecutionType.ENTRY, executed_at=opened, price=Decimal("100"), quantity=Decimal("10"), fees=Decimal("0"))]
        db.add(trade)
        db.flush()
        db.add(TradeMetrics(trade_id=trade.id, average_entry=Decimal("100"), total_quantity=Decimal("10"), realized_pnl=Decimal("0")))
        journal = JournalEntry(user_id=user.id, type=JournalType.DAILY, trade_id=trade.id, date=opened.date(),
                               title=f"{marker}-journal", content={"type": "doc", "content": []}, key_lessons=[f"{marker}-lesson"])
        db.add(journal)
        db.flush()
        db.add(Attachment(trade_id=trade.id, url=f"local:attachments/{user.id}/{marker}.png", file_name=f"{marker}-chart.png",
                          file_size=10, mime_type="image/png"))
        share = PublicTradeShare(user_id=user.id, trade_id=trade.id, slug=f"{marker}-share", title=f"{marker}-share-title")
        db.add(share)
        cash = CashTransaction(user_id=user.id, account_id=account.id, type=CashTransactionType.DEPOSIT, amount=Decimal("2500"),
                               occurred_on=opened.date(), note=f"{marker}-deposit")
        db.add(cash)
        db.add(MentorAccess(student_user_id=user.id, mentor_email=f"{marker}@mentor.example", mentor_name=f"{marker}-mentor",
                            status=MentorAccessStatus.ACTIVE, can_view_journals=True, can_comment=True))
        db.commit()
        return {"trade_id": trade.id, "journal_id": journal.id, "account_id": account.id, "slug": share.slug, "cash_id": cash.id}
    finally:
        db.close()


def test_users_never_see_each_others_data(monkeypatch) -> None:
    import tradzlog_web.main as web_main
    from tradzlog_api.config import settings

    # Crawl the switched-off features too, so they are already safe when they are switched on.
    monkeypatch.setattr(settings, "feature_community", True)
    monkeypatch.setattr(settings, "feature_billing", True)

    marker = f"secret{uuid4().hex[:8]}"
    with new_client() as alice, new_client() as bob:
        alice_ids = seed_private_book(sign_up(alice, "Alice"), marker)
        sign_up(bob, "Bob")

        visited = 0
        for route in web_main.app.routes:
            path = getattr(route, "path", "")
            if "GET" not in (getattr(route, "methods", None) or set()) or path.startswith("/share/"):
                continue  # public trade shares are public by design
            url = path.replace("{trade_id}", alice_ids["trade_id"]).replace("{journal_id}", alice_ids["journal_id"])
            if "{" in url:
                continue
            for query in ("", f"?account_id={alice_ids['account_id']}&range=ALL", f"?day={datetime.now(UTC).date() - timedelta(days=1)}"):
                response = bob.get(url + query, follow_redirects=True)
                visited += 1
                assert marker not in response.text, f"{url + query} showed Alice's data to Bob"
                unprotected = re.findall(r'<form[^>]*method="post"[^>]*>(?!<input type="hidden" name="csrf_token")', response.text, re.I)
                assert not unprotected, f"{url} has POST forms without a CSRF token: {unprotected}"
        assert visited > 40

        # Bob can't act on Alice's trade either.
        page = bob.get("/dashboard")
        token = csrf_from(page.text)
        bob.post(f"/positions/{alice_ids['trade_id']}/close", data={"csrf_token": token, "exit_price": "1", "quantity": "10", "fees": "0"})
        bob.post(f"/trades/{alice_ids['trade_id']}/share", data={"csrf_token": token})
        bob.post(f"/transactions/{alice_ids['cash_id']}/delete", data={"csrf_token": token})
        bob.post("/transactions", data={"csrf_token": token, "account_id": alice_ids["account_id"], "type": "WITHDRAWAL",
                                        "amount": "1", "occurred_on": "2026-01-01", "note": ""})

    from tradzlog_db.models import CashTransaction, PublicTradeShare, Trade, TradeStatus

    db = SessionLocal()
    try:
        assert db.get(Trade, alice_ids["trade_id"]).status == TradeStatus.OPEN
        assert db.get(CashTransaction, alice_ids["cash_id"]) is not None
        assert db.scalar(select(CashTransaction).where(CashTransaction.account_id == alice_ids["account_id"],
                                                       CashTransaction.id != alice_ids["cash_id"])) is None
        shares = db.scalars(select(PublicTradeShare).where(PublicTradeShare.trade_id == alice_ids["trade_id"])).all()
        assert [share.slug for share in shares] == [alice_ids["slug"]]
    finally:
        db.close()


def test_manage_trading_accounts_and_instruments(client) -> None:
    sign_up(client, "Manager")
    page = client.get("/trades/new")
    assert "Create a trading account first" in page.text
    token = csrf_from(page.text)

    created = client.post("/settings/accounts", data={
        "csrf_token": token, "name": "FTMO 100k", "broker": "FTMO", "account_type": "PROP_FIRM", "currency": "usd",
        "starting_balance": "100,000", "prop_firm_name": "FTMO", "max_daily_loss": "5000", "max_total_loss": "10000",
        "daily_profit_target": "",
    }, follow_redirects=False)
    assert "message=" in created.headers["location"]
    listing = client.get("/settings/accounts")
    assert "FTMO 100k" in listing.text and "$100,000.00" in listing.text and "USD" in listing.text

    bad = client.post("/settings/accounts", data={
        "csrf_token": token, "name": "", "broker": "x", "account_type": "LIVE", "currency": "USD", "starting_balance": "-5",
        "prop_firm_name": "", "max_daily_loss": "", "max_total_loss": "", "daily_profit_target": "",
    }, follow_redirects=False)
    assert "error=" in bad.headers["location"]

    # Instruments are a catalogue shared by all users, so the hint only shows on an empty catalogue.
    from tradzlog_db.models import Instrument

    db = SessionLocal()
    try:
        catalogue_empty = db.scalar(select(Instrument.id).limit(1)) is None
    finally:
        db.close()
    if catalogue_empty:
        assert "Add the instruments you trade" in client.get("/trades/new").text
    symbol = f"T{uuid4().hex[:5].upper()}"
    form = {"csrf_token": token, "symbol": symbol.lower(), "name": "Test", "asset_class": "FUTURES", "point_value": "50",
            "tick_size": "0.25", "currency": "USD", "exchange": "CME"}
    assert "message=" in client.post("/settings/instruments", data=form, follow_redirects=False).headers["location"]
    assert "already exists" in client.post("/settings/instruments", data=form).text
    assert symbol in client.get("/trades/new").text  # the log-trade form is now usable


def test_users_cannot_change_each_others_accounts() -> None:
    from tradzlog_db.models import Account

    with new_client() as alice, new_client() as bob:
        alice_ids = seed_private_book(sign_up(alice, "Alice"), f"acct{uuid4().hex[:6]}")
        sign_up(bob, "Bob")
        token = csrf_from(bob.get("/dashboard").text)
        fields = {"csrf_token": token, "name": "hijacked", "broker": "x", "account_type": "LIVE", "currency": "USD",
                  "starting_balance": "1", "prop_firm_name": "", "max_daily_loss": "", "max_total_loss": "", "daily_profit_target": ""}
        assert "error=" in bob.post(f"/settings/accounts/{alice_ids['account_id']}", data=fields, follow_redirects=False).headers["location"]
        assert "error=" in bob.post(f"/settings/accounts/{alice_ids['account_id']}/archive", data={"csrf_token": token}, follow_redirects=False).headers["location"]
        assert "hijacked" not in bob.get("/settings/accounts").text

    db = SessionLocal()
    try:
        account = db.get(Account, alice_ids["account_id"])
        assert account.name != "hijacked" and account.archived_at is None
    finally:
        db.close()


def test_profile_timezone_drives_dates_and_form_times(client) -> None:
    from tradzlog_db.models import Execution, Trade, User

    email = sign_up(client, "Londoner")
    token = csrf_from(client.get("/settings/profile").text)
    assert "Europe/London" in client.get("/settings/profile").text
    bad = client.post("/settings/profile", data={"csrf_token": token, "name": "Londoner", "timezone": "Mars/Base"}, follow_redirects=False)
    assert "error=" in bad.headers["location"]
    saved = client.post("/settings/profile", data={"csrf_token": token, "name": "Lon Doner", "timezone": "Europe/London"}, follow_redirects=False)
    assert "message=" in saved.headers["location"]

    # An account and an instrument, then a trade typed in London time just after midnight.
    client.post("/settings/accounts", data={"csrf_token": token, "name": "Main", "broker": "B", "account_type": "LIVE", "currency": "USD",
        "starting_balance": "1000", "prop_firm_name": "", "max_daily_loss": "", "max_total_loss": "", "daily_profit_target": ""})
    symbol = f"L{uuid4().hex[:5].upper()}"
    client.post("/settings/instruments", data={"csrf_token": token, "symbol": symbol, "name": "Tz test", "asset_class": "STOCK",
        "point_value": "1", "tick_size": "", "currency": "USD", "exchange": ""})
    form_page = client.get("/trades/new").text
    assert "Opened (Europe/London)" in form_page
    account_id = re.search(r'name="account_id" required><option value="([^"]+)"', form_page).group(1)
    instrument_id = re.search(rf'<option value="([^"]+)" data-pv="[^"]*">{symbol} ', form_page).group(1)
    created = client.post("/trades/new", data={"csrf_token": token, "account_id": account_id, "instrument_id": instrument_id,
        "direction": "LONG", "opened_at": "2026-07-01T00:10", "entry_price": "10", "quantity": "1", "planned_stop": "", "planned_target": "",
        "entry_fees": "0", "exit_price": "11", "closed_at": "2026-07-01T00:30", "exit_fees": "0", "setup_tag": "TZ", "timeframe": "", "notes": ""},
        follow_redirects=False)
    assert created.status_code == 303, created.text

    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.email == email))
        assert user.timezone == "Europe/London" and user.name == "Lon Doner"
        trade = db.scalar(select(Trade).where(Trade.user_id == user.id))
        assert trade.closed_at == datetime(2026, 6, 30, 23, 30, tzinfo=UTC)  # stored in UTC
        assert db.scalar(select(Execution.executed_at).where(Execution.trade_id == trade.id).order_by(Execution.executed_at)) == datetime(2026, 6, 30, 23, 10, tzinfo=UTC)
    finally:
        db.close()

    # Shown and grouped on the London day.
    assert symbol in client.get("/journal?day=2026-07-01").text
    assert symbol not in client.get("/journal?day=2026-06-30").text
    assert "Jul 1 00:10" in client.get("/trades?range=ALL").text


# --------------------------------------------------------------------- broker import

FIXTURE_DIR = ROOT / "tests" / "fixtures" / "imports"


def account_with_csrf(client, name: str = "IBKR") -> tuple[str, str]:
    token = csrf_from(client.get("/dashboard").text)
    client.post("/settings/accounts", data={"csrf_token": token, "name": name, "broker": "Interactive Brokers", "account_type": "LIVE",
        "currency": "USD", "starting_balance": "50000", "prop_firm_name": "", "max_daily_loss": "", "max_total_loss": "", "daily_profit_target": ""})
    page = client.get("/settings/import").text
    account_id = re.search(r'name="account_id" required><option value="([^"]+)"', page).group(1)
    return account_id, csrf_from(page)


def upload(client, token: str, account_id: str, name: str, content: bytes, tz: str = "America/New_York") -> str:
    response = client.post("/settings/import/preview", data={"csrf_token": token, "account_id": account_id, "file_format": "auto", "timezone": tz},
                           files={"file": (name, content, "text/csv")}, follow_redirects=False)
    assert response.status_code == 303, response.text
    location = response.headers["location"]
    assert "error=" not in location, location
    return location.rsplit("/", 1)[1]


def test_broker_import_preview_confirm_reimport_and_undo(client) -> None:
    from tradzlog_db.models import Instrument, Trade, TradeStatus

    email = sign_up(client, "Importer")
    account_id, token = account_with_csrf(client)
    raw = (FIXTURE_DIR / "ibkr_flex_trades.csv").read_bytes()

    first = upload(client, token, account_id, "flex.csv", raw)
    preview = client.get(f"/settings/import/{first}").text
    assert "Waiting for confirmation" in preview and "Import 6 fills into IBKR" in preview
    assert "Interactive Brokers (Flex Query: Trades)" in preview and "1 non-trade rows skipped" in preview
    db = SessionLocal()
    try:
        assert db.scalar(select(Trade.id).join(Trade.account).where(Trade.account_id == account_id)) is None  # preview writes nothing
    finally:
        db.close()

    done = client.post(f"/settings/import/{first}/confirm", data={"csrf_token": token}, follow_redirects=False)
    assert "message=" in done.headers["location"]
    summary = client.get(f"/settings/import/{first}").text
    assert "Imported" in summary and "Undo this import" in summary

    db = SessionLocal()
    try:
        trades = {t.instrument.symbol: t for t in db.scalars(select(Trade).where(Trade.account_id == account_id)).all()}
        assert set(trades) == {"AAPL", "ESH6", "EURUSD"}
        assert trades["AAPL"].status == TradeStatus.CLOSED and trades["AAPL"].metrics.realized_pnl == Decimal("347.0000")
        assert trades["ESH6"].direction.value == "SHORT" and trades["ESH6"].metrics.realized_pnl == Decimal("991.6000")
        assert trades["EURUSD"].status == TradeStatus.OPEN
        assert db.scalar(select(Instrument.point_value).where(Instrument.symbol == "ESH6")) == Decimal("50")
    finally:
        db.close()

    # The same file again: everything is a duplicate, nothing to import.
    again = upload(client, token, account_id, "flex-again.csv", raw)
    page = client.get(f"/settings/import/{again}").text
    assert "Nothing new to import" in page and "6 already imported" in page
    assert "message=" in client.post(f"/settings/import/{again}/discard", data={"csrf_token": token}, follow_redirects=False).headers["location"]

    # A later file closes the forex position the first import left open.
    second = upload(client, token, account_id, "close-fx.csv", b"Symbol,Date/Time,Side,Quantity,Price,Commission\nEURUSD,2026-03-05 10:00,SELL,0.2,1.0900,2\n")
    assert "1 continued" in client.get(f"/settings/import/{second}").text
    client.post(f"/settings/import/{second}/confirm", data={"csrf_token": token})
    db = SessionLocal()
    try:
        fx = db.scalar(select(Trade).join(Instrument).where(Trade.account_id == account_id, Instrument.symbol == "EURUSD"))
        assert fx.status == TradeStatus.CLOSED and fx.metrics.realized_pnl == Decimal("96.0000")  # 0.005 x 0.2 lots x 100000 - 4
    finally:
        db.close()

    # Undo goes newest first.
    refused = client.post(f"/settings/import/{first}/undo", data={"csrf_token": token}, follow_redirects=False)
    assert "Undo+the+newer" in refused.headers["location"] or "Undo%20the%20newer" in refused.headers["location"]
    client.post(f"/settings/import/{second}/undo", data={"csrf_token": token})
    db = SessionLocal()
    try:
        fx = db.scalar(select(Trade).join(Instrument).where(Trade.account_id == account_id, Instrument.symbol == "EURUSD"))
        assert fx.status == TradeStatus.OPEN and fx.closed_at is None  # reopened
    finally:
        db.close()
    client.post(f"/settings/import/{first}/undo", data={"csrf_token": token})
    db = SessionLocal()
    try:
        assert db.scalars(select(Trade).where(Trade.account_id == account_id)).all() == []
    finally:
        db.close()

    # Someone else can't see or act on these imports.
    with new_client() as other:
        sign_up(other, "Other")
        other_token = csrf_from(other.get("/dashboard").text)
        assert "Import not found" in other.get(f"/settings/import/{first}").text
        assert "error=" in other.post(f"/settings/import/{first}/undo", data={"csrf_token": other_token}, follow_redirects=False).headers["location"]
        assert email not in other.get("/settings/import").text


def test_import_reports_unreadable_rows_and_rejects_bad_files(client) -> None:
    sign_up(client)
    account_id, token = account_with_csrf(client, "Generic")
    batch = upload(client, token, account_id, "mixed.csv", b"Ticker,Date/Time,Qty,Price\nQQQ,2026-03-02 10:00,10,400\nQQQ,never,5,1\n")
    page = client.get(f"/settings/import/{batch}").text
    assert "1 rows couldn" in page and "Row 3" in page and "Import 1 fills" in page
    bad = client.post("/settings/import/preview", data={"csrf_token": token, "account_id": account_id, "file_format": "auto", "timezone": "UTC"},
                      files={"file": ("x.csv", b"hello,world\n", "text/csv")}, follow_redirects=False)
    assert "error=" in bad.headers["location"]


def test_options_import_and_undo_removes_created_instruments(client) -> None:
    from tradzlog_db.models import Instrument, Trade

    sign_up(client, "Options")
    account_id, token = account_with_csrf(client, "IBKR options")
    root = f"Q{uuid4().hex[:3].upper()}"
    occ = f"{root:<6}261008P00755000"
    raw = (f"Symbol,Date/Time,Quantity,Price,Commission,TradeID,Realized P/L\n"
           f"{occ},2026-10-06 10:00:00,1,3.13,-0.75,{root}1,0\n"
           f"{occ},2026-10-06 12:00:00,1,1.39,-0.75,{root}2,0\n"
           f"{occ},2026-10-07 10:00:00,-2,1.09,-1.10,{root}3,-236.60\n").encode()

    batch = upload(client, token, account_id, "options.csv", raw, tz="UTC")
    client.post(f"/settings/import/{batch}/confirm", data={"csrf_token": token})
    summary = client.get(f"/settings/import/{batch}").text
    assert "matches" in summary  # TradzLog P&L equals the broker's -236.60

    db = SessionLocal()
    try:
        instrument = db.scalar(select(Instrument).where(Instrument.symbol == occ))
        assert instrument.asset_class.value == "OPTIONS" and instrument.point_value == Decimal("100")
        trade = db.scalar(select(Trade).where(Trade.account_id == account_id))
        assert trade.metrics.realized_pnl == Decimal("-236.6000")  # (1.09 - 2.26) x 2 x 100 - 2.60 fees
        # Simulate an import made before instrument ids were recorded: undo must use the symbol fallback.
        from tradzlog_db.models import BrokerSync
        record = db.get(BrokerSync, batch)
        record.summary = {key: value for key, value in record.summary.items() if key != "instrument_ids_created"}
        db.commit()
    finally:
        db.close()

    undone = client.post(f"/settings/import/{batch}/undo", data={"csrf_token": token}, follow_redirects=False)
    assert "1%20unused%20instruments%20removed" in undone.headers["location"]
    db = SessionLocal()
    try:
        assert db.scalar(select(Instrument).where(Instrument.symbol == occ)) is None
    finally:
        db.close()

    # Re-importing creates it again, correctly.
    again = upload(client, token, account_id, "options.csv", raw, tz="UTC")
    assert "Import 3 fills" in client.get(f"/settings/import/{again}").text


def test_tastytrade_import_with_expiration(client) -> None:
    from tradzlog_db.models import Instrument, Trade, TradeStatus

    sign_up(client, "Tasty")
    account_id, token = account_with_csrf(client, "tastytrade")
    raw = (FIXTURE_DIR / "tastytrade_transactions.csv").read_bytes()
    batch = upload(client, token, account_id, "tasty.csv", raw, tz="UTC")
    preview = client.get(f"/settings/import/{batch}").text
    assert "tastytrade (History: Transactions)" in preview and "EXPIRE/ASSIGN" in preview and "Import 5 fills" in preview
    client.post(f"/settings/import/{batch}/confirm", data={"csrf_token": token})
    db = SessionLocal()
    try:
        trades = {t.instrument.symbol.split()[0]: t for t in db.scalars(select(Trade).join(Instrument).where(Trade.account_id == account_id)).all()}
        assert trades["SPY"].metrics.realized_pnl == Decimal("88.7200")   # (5.10 - 4.20) x 100 - 1.28 fees
        assert trades["IWM"].status == TradeStatus.CLOSED and trades["IWM"].metrics.realized_pnl == Decimal("297.7200")  # expired worthless
        assert trades["MESZ6"].status == TradeStatus.OPEN
    finally:
        db.close()


def test_ambiguous_dates_are_flagged_in_the_preview(client) -> None:
    sign_up(client)
    account_id, token = account_with_csrf(client, "Dates")
    batch = upload(client, token, account_id, "dates.csv", b"Symbol,Date/Time,Side,Quantity,Price\nAAPL,03/04/2026 10:00,BUY,1,100\n", tz="UTC")
    assert "could be read either way" in client.get(f"/settings/import/{batch}").text


def test_uploads_read_by_an_older_importer_cannot_be_confirmed(client) -> None:
    from tradzlog_db.models import BrokerSync, Trade

    sign_up(client)
    account_id, token = account_with_csrf(client, "Stale")
    batch = upload(client, token, account_id, "a.csv", b"Symbol,Date/Time,Side,Quantity,Price\nAAPL,2026-03-02 10:00,BUY,1,100\n", tz="UTC")
    db = SessionLocal()
    try:
        record = db.get(BrokerSync, batch)
        record.payload = {**record.payload, "parser_version": 1}
        db.commit()
    finally:
        db.close()
    assert "older version of the importer" in client.get(f"/settings/import/{batch}").text
    refused = client.post(f"/settings/import/{batch}/confirm", data={"csrf_token": token}, follow_redirects=False)
    assert "error=" in refused.headers["location"]
    db = SessionLocal()
    try:
        assert db.scalar(select(Trade.id).where(Trade.account_id == account_id)) is None
    finally:
        db.close()


def test_commission_per_side_for_files_without_fees(client) -> None:
    from tradzlog_db.models import Execution, Trade

    sign_up(client, "Futures")
    account_id, token = account_with_csrf(client, "NinjaTrader")
    raw = (FIXTURE_DIR / "ninjatrader_position_history.csv").read_bytes()
    response = client.post("/settings/import/preview",
                           data={"csrf_token": token, "account_id": account_id, "file_format": "auto", "timezone": "UTC", "commission_per_side": "0.50"},
                           files={"file": ("nt.csv", raw, "text/csv")}, follow_redirects=False)
    batch = response.headers["location"].rsplit("/", 1)[1]
    preview = client.get(f"/settings/import/{batch}").text
    assert "Commission added: <b>$4.00</b>" in preview  # 0.50 x (1+1+1+1+2+2) contract sides
    client.post(f"/settings/import/{batch}/confirm", data={"csrf_token": token})
    summary = client.get(f"/settings/import/{batch}").text
    assert "of commission you entered" in summary and "matches" in summary
    db = SessionLocal()
    try:
        fees = db.scalars(select(Execution.fees).join(Trade).where(Trade.account_id == account_id)).all()
        assert sum(fees) == Decimal("4.0000")
    finally:
        db.close()
    bad = client.post("/settings/import/preview",
                      data={"csrf_token": token, "account_id": account_id, "file_format": "auto", "timezone": "UTC", "commission_per_side": "-1"},
                      files={"file": ("nt.csv", raw, "text/csv")}, follow_redirects=False)
    assert "error=" in bad.headers["location"]


# --------------------------------------------------------------------- your data (export and deletion)


def test_export_then_delete_account(client) -> None:
    import io
    import json
    import zipfile

    from tradzlog_api.services import storage
    from tradzlog_db.models import Account, Attachment, Instrument, JournalEntry, Trade, User, WebSession

    marker = f"gdpr{uuid4().hex[:8]}"
    email = sign_up(client, "Leaver")
    ids = seed_private_book(email, marker)
    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.email == email))
        user_id = user.id
        ref = db.scalar(select(Attachment.url).where(Attachment.trade_id == ids["trade_id"]))
        instrument_id = db.get(Trade, ids["trade_id"]).instrument_id
    finally:
        db.close()
    screenshot = storage.LOCAL_ROOT / ref.removeprefix("local:")
    screenshot.parent.mkdir(parents=True, exist_ok=True)
    screenshot.write_bytes(b"\x89PNG\r\n\x1a\n" + marker.encode())

    with new_client() as bystander:
        bystander_email = sign_up(bystander, "Stays")
        bystander_ids = seed_private_book(bystander_email, f"keep{uuid4().hex[:8]}")

    page = client.get("/settings/data")
    assert "Download ZIP" in page.text and "Delete my account" in page.text
    token = csrf_from(page.text)

    export = client.post("/settings/data/export", data={"csrf_token": token})
    assert export.status_code == 200 and export.headers["content-type"] == "application/zip"
    assert "attachment; filename=\"tradzlog-export-" in export.headers["content-disposition"]
    archive = zipfile.ZipFile(io.BytesIO(export.content))
    document = json.loads(archive.read("tradzlog-export.json"))
    tables = document["tables"]
    assert tables["users"][0]["email"] == email and "hashed_password" not in tables["users"][0]
    assert tables["web_sessions"] and all("token_hash" not in row for row in tables["web_sessions"])
    assert [row["id"] for row in tables["trades"]] == [ids["trade_id"]]
    assert tables["trades"][0]["notes"] == f"{marker}-notes" and tables["journal_entries"][0]["title"] == f"{marker}-journal"
    assert len(tables["executions"]) == 1 and tables["executions"][0]["price"] == "100.00000000"
    attachment_id = tables["attachments"][0]["id"]
    assert archive.read(f"screenshots/{attachment_id}.png") == screenshot.read_bytes()
    assert f"{marker}-account" in archive.read("csv/accounts.csv").decode()
    assert "keep" not in json.dumps(tables)  # nothing of the other user

    # Refused without the confirmation word or with the wrong password; nothing is deleted.
    for form, problem in (({"password": "correct-horse-1", "confirm": "yes"}, "Type DELETE"),
                          ({"password": "wrong-password", "confirm": "DELETE"}, "password is incorrect")):
        refused = client.post("/settings/data/delete", data={"csrf_token": token, **form}, follow_redirects=True)
        assert problem in refused.text
    assert screenshot.exists()

    deleted = client.post("/settings/data/delete", data={"csrf_token": token, "password": "correct-horse-1", "confirm": "delete"},
                          follow_redirects=False)
    assert deleted.headers["location"] == "/account-deleted"
    assert "Your account has been deleted" in client.get("/account-deleted").text
    assert client.get("/dashboard", follow_redirects=False).status_code == 303  # signed out
    assert not screenshot.exists()

    db = SessionLocal()
    try:
        assert db.get(User, user_id) is None
        for model, column in ((Account, Account.user_id), (Trade, Trade.user_id), (JournalEntry, JournalEntry.user_id), (WebSession, WebSession.user_id)):
            assert db.scalar(select(model).where(column == user_id)) is None, model.__name__
        assert db.scalar(select(Attachment).where(Attachment.trade_id == ids["trade_id"])) is None
        assert db.get(Instrument, instrument_id) is not None  # hand-made instruments stay in the shared catalogue
        assert db.get(Trade, bystander_ids["trade_id"]) is not None
    finally:
        db.close()

    signin = client.post("/login", data={"email": email, "password": "correct-horse-1", "next": "/"}, headers=ORIGIN, follow_redirects=False)
    assert signin.status_code != 303


def test_deleting_an_account_removes_instruments_only_its_imports_created(client) -> None:
    from tradzlog_db.models import Instrument

    sign_up(client, "Importer")
    account_id, token = account_with_csrf(client)
    symbol = f"Q{uuid4().hex[:5].upper()}"
    csv_text = (f"symbol,side,quantity,price,time,fees\n{symbol},BUY,10,5.00,2026-03-02 10:00:00,0\n"
                f"{symbol},SELL,10,6.00,2026-03-02 11:00:00,0\n")
    batch = upload(client, token, account_id, "fills.csv", csv_text.encode(), tz="UTC")
    confirmed = client.post(f"/settings/import/{batch}/confirm", data={"csrf_token": token}, follow_redirects=False)
    assert confirmed.status_code == 303
    db = SessionLocal()
    try:
        assert db.scalar(select(Instrument).where(Instrument.symbol == symbol)) is not None
    finally:
        db.close()

    client.post("/settings/data/delete", data={"csrf_token": token, "password": "correct-horse-1", "confirm": "DELETE"})
    db = SessionLocal()
    try:
        assert db.scalar(select(Instrument).where(Instrument.symbol == symbol)) is None
    finally:
        db.close()


# --------------------------------------------------------------------- onboarding


def test_first_run_guide_tracks_progress_and_can_be_hidden(client) -> None:

    sign_up(client, "Newcomer")
    first = client.get("/dashboard").text
    assert "Get started with TradzLog" in first and "0 of 3 steps done" in first
    assert "Add a trading account first" in first and "Equity curve" not in first  # no empty charts
    token = csrf_from(first)

    client.post("/settings/profile", data={"csrf_token": token, "name": "Newcomer", "timezone": "Europe/London"})
    account_with_csrf(client)
    page = client.get("/dashboard").text
    assert "2 of 3 steps done" in page and 'href="/settings/import"' in page and "Import from your broker" in page

    hidden = client.post("/onboarding/dismiss", data={"csrf_token": token}, follow_redirects=False)
    assert hidden.headers["location"] == "/dashboard"
    after = client.get("/dashboard").text
    assert "Get started with TradzLog" not in after and "No trades yet" in after


def test_first_run_guide_finishes_itself_when_every_step_is_done(client) -> None:
    from tradzlog_db.models import User

    email = sign_up(client, "Finisher")
    token = csrf_from(client.get("/dashboard").text)
    client.post("/settings/profile", data={"csrf_token": token, "name": "Finisher", "timezone": "Europe/London"})
    seed_private_book(email, f"done{uuid4().hex[:6]}")  # account, trade and journal entry
    page = client.get("/dashboard").text
    assert "Get started with TradzLog" not in page and "Equity curve" in page
    db = SessionLocal()
    try:
        assert db.scalar(select(User).where(User.email == email)).onboarded_at is not None
    finally:
        db.close()


# --------------------------------------------------------------------- transactions, positions filter, custom dates


def test_deposits_and_withdrawals(client) -> None:
    sign_up(client, "Saver")
    account_id, token = account_with_csrf(client)  # starting balance 50,000
    empty = client.get("/transactions").text
    assert "No deposits or withdrawals in this view." in empty

    for kind, value, day in (("DEPOSIT", "10,000", "2026-02-01"), ("WITHDRAWAL", "2500.50", "2026-03-01")):
        saved = client.post("/transactions", data={"csrf_token": token, "account_id": account_id, "type": kind, "amount": value,
                                                   "occurred_on": day, "note": f"{kind.lower()} note"}, follow_redirects=False)
        assert "message=" in saved.headers["location"], saved.headers["location"]
    page = client.get("/transactions").text
    assert "+$10,000.00" in page and "-$2,500.50" in page and "+$7,499.50" in page and "withdrawal note" in page

    for form in ({"amount": "0"}, {"amount": "-5"}, {"amount": "abc"}, {"occurred_on": "not-a-date"}, {"type": "GIFT"}):
        bad = {"csrf_token": token, "account_id": account_id, "type": "DEPOSIT", "amount": "1", "occurred_on": "2026-02-01", "note": "", **form}
        assert "error=" in client.post("/transactions", data=bad, follow_redirects=False).headers["location"], form

    february = client.get("/transactions?range=2026-02-01..2026-02-28").text
    assert "+$10,000.00" in february and "-$2,500.50" not in february

    # The dashboard balance includes the cash flows: 50,000 + 10,000 - 2,500.50.
    from tradzlog_db.models import AssetClass, Direction, Instrument, Trade, TradeMetrics, TradeStatus, User

    db = SessionLocal()
    try:
        user = db.scalar(select(User).join(User.accounts).where(User.accounts.any(id=account_id)))
        instrument = Instrument(symbol=f"C{uuid4().hex[:6].upper()}", name="Cash test", asset_class=AssetClass.STOCK, point_value=Decimal("1"))
        db.add(instrument)
        db.flush()
        closed = datetime(2026, 3, 2, 15, tzinfo=UTC)
        trade = Trade(account_id=account_id, user_id=user.id, instrument_id=instrument.id, direction=Direction.LONG, status=TradeStatus.CLOSED,
                      opened_at=closed - timedelta(hours=1), closed_at=closed, commissions=Decimal("0"), mistake_flags=[], tags=[], is_reviewed=False)
        db.add(trade)
        db.flush()
        db.add(TradeMetrics(trade_id=trade.id, average_entry=Decimal("10"), total_quantity=Decimal("1"), realized_pnl=Decimal("1")))
        db.commit()
    finally:
        db.close()
    dashboard = client.get("/dashboard?range=ALL").text
    assert "$57,500.50" in dashboard  # 50,000 + 10,000 - 2,500.50 + 1 = 57,500.50

    from tradzlog_db.models import CashTransaction

    db = SessionLocal()
    try:
        withdrawal_id = db.scalar(select(CashTransaction.id).where(CashTransaction.account_id == account_id, CashTransaction.amount == Decimal("2500.5")))
    finally:
        db.close()
    removed = client.post(f"/transactions/{withdrawal_id}/delete", data={"csrf_token": token, "back": "https://evil.example/"}, follow_redirects=False)
    assert removed.headers["location"].startswith("/transactions?")
    assert "-$2,500.50" not in client.get("/transactions").text


def test_positions_can_be_filtered_by_account(client) -> None:
    email = sign_up(client, "Holder")
    first = seed_private_book(email, f"posa{uuid4().hex[:6]}")
    second = seed_private_book(email, f"posb{uuid4().hex[:6]}")
    everything = client.get("/positions").text
    assert everything.count('href="/trades/') >= 2
    only_first = client.get(f"/positions?account_id={first['account_id']}").text
    assert f'/trades/{first["trade_id"]}' in only_first and f'/trades/{second["trade_id"]}' not in only_first
    assert f'<option value="{first["account_id"]}" selected>' in only_first


def test_from_to_dates_become_a_custom_range(client) -> None:
    sign_up(client, "Ranger")
    account_with_csrf(client)  # the dashboard shows filters once there is an account
    for path in ("/dashboard", "/trades", "/analytics", "/analytics/time"):
        response = client.get(f"{path}?account_id=x&range=1M&from=2026-03-31&to=2026-01-01", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == f"{path}?account_id=x&range=2026-01-01..2026-03-31"
        page = client.get(response.headers["location"]).text
        assert 'name="from" value="2026-01-01"' in page and 'name="to" value="2026-03-31"' in page
    cleared = client.get("/trades?range=1M&from=&to=", follow_redirects=False)
    assert cleared.headers["location"] == "/trades?range=1M"


def test_portfolio_shows_balance_with_cash_flows(client) -> None:
    sign_up(client, "Portfolio")
    account_id, token = account_with_csrf(client)  # starting balance 50,000
    assert "Started at $50,000.00" in client.get("/dashboard/portfolio").text
    client.post("/transactions", data={"csrf_token": token, "account_id": account_id, "type": "DEPOSIT", "amount": "1000",
                                       "occurred_on": "2026-02-01", "note": ""})
    page = client.get("/dashboard/portfolio").text
    assert "$51,000.00" in page and "+$1,000.00" in page


def test_journal_write_list_and_read(client) -> None:
    email = sign_up(client, "Writer")
    ids = seed_private_book(email, f"jr{uuid4().hex[:6]}")
    token = csrf_from(client.get("/journal/new").text)
    created = client.post("/journal/new", data={"csrf_token": token, "entry_type": "TRADE_REVIEW", "entry_date": "2026-03-04",
                                                "title": "Faded the open", "content": "Waited for the pullback.\nGood patience.",
                                                "trade_id": ids["trade_id"], "mood": "4", "market_condition": "",
                                                "key_lessons": "wait for confirmation; size down"}, follow_redirects=False)
    detail = client.get(created.headers["location"]).text
    assert "Faded the open" in detail and "Waited for the pullback." in detail and "4 · Good" in detail
    assert "wait for confirmation" in detail and f'href="/trades/{ids["trade_id"]}"' in detail
    feed = client.get("/journal").text
    assert "Faded the open" in feed and "Trade review" in feed
    assert "Faded the open" not in client.get("/journal?entry_type=DAILY").text
    with new_client() as other:
        sign_up(other, "Other")
        assert other.get(created.headers["location"]).status_code == 404


def test_position_risk_uses_the_contract_point_value(client) -> None:
    from tradzlog_db.models import Instrument, Trade

    email = sign_up(client, "Futures")
    ids = seed_private_book(email, f"fut{uuid4().hex[:6]}")  # long 10 @ 100
    db = SessionLocal()
    try:
        trade = db.get(Trade, ids["trade_id"])
        trade.planned_stop = Decimal("98")
        db.get(Instrument, trade.instrument_id).point_value = Decimal("50")
        db.commit()
    finally:
        db.close()
    page = client.get("/positions").text
    assert "$1,000.00" in page  # (100 - 98) x 10 x 50
