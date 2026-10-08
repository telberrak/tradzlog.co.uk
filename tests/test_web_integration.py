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
        db.add(MentorAccess(student_user_id=user.id, mentor_email=f"{marker}@mentor.example", mentor_name=f"{marker}-mentor",
                            status=MentorAccessStatus.ACTIVE, can_view_journals=True, can_comment=True))
        db.commit()
        return {"trade_id": trade.id, "journal_id": journal.id, "account_id": account.id, "slug": share.slug}
    finally:
        db.close()


def test_users_never_see_each_others_data() -> None:
    import tradzlog_web.main as web_main

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

    from tradzlog_db.models import PublicTradeShare, Trade, TradeStatus

    db = SessionLocal()
    try:
        assert db.get(Trade, alice_ids["trade_id"]).status == TradeStatus.OPEN
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
