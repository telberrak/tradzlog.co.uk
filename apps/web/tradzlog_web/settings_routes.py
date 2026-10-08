"""Trading accounts and instruments, managed from the website."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from html import escape
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Query, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from tradzlog_db.models import Account, AccountType, AssetClass, Instrument
from tradzlog_db.session import SessionLocal
from tradzlog_web.auth import current_user
from tradzlog_web.components import money, plain_number
from tradzlog_web.ui import shell

router = APIRouter()

ACCOUNT_TYPES = {AccountType.LIVE: "Live", AccountType.PAPER: "Paper", AccountType.PROP_FIRM: "Prop firm"}
ASSET_CLASSES = {
    AssetClass.STOCK: "Stock",
    AssetClass.FUTURES: "Futures",
    AssetClass.FOREX: "Forex",
    AssetClass.OPTIONS: "Options",
    AssetClass.CRYPTO: "Crypto",
    AssetClass.COMMODITY: "Commodity",
}


class FormProblem(ValueError):
    pass


def notice(message: str, error: str) -> str:
    if error:
        return f'<p class="form-error" role="alert">{escape(error)}</p>'
    return f'<p class="form-ok">{escape(message)}</p>' if message else ""


def back(path: str, message: str = "", error: str = "") -> RedirectResponse:
    query = f"?error={quote(error)}" if error else (f"?message={quote(message)}" if message else "")
    return RedirectResponse(path + query, status_code=status.HTTP_303_SEE_OTHER)


def text(value: str, label: str, max_length: int, required: bool = True) -> str | None:
    value = (value or "").strip()
    if not value:
        if required:
            raise FormProblem(f"{label} is required.")
        return None
    if len(value) > max_length:
        raise FormProblem(f"{label} must be at most {max_length} characters.")
    return value


def amount(value: str, label: str, required: bool = False, positive: bool = False) -> Decimal | None:
    value = (value or "").replace(",", "").strip()
    if not value:
        if required:
            raise FormProblem(f"{label} is required.")
        return None
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise FormProblem(f"{label} must be a number.") from None
    if not number.is_finite() or number < 0 or (positive and number == 0):
        raise FormProblem(f"{label} must be {'more than' if positive else 'at least'} 0.")
    return number


def choice(value: str, options: dict, label: str):
    for option in options:
        if option.value == value:
            return option
    raise FormProblem(f"Choose a valid {label.lower()}.")


def options_html(options: dict, selected=None) -> str:
    return "".join(
        f'<option value="{option.value}" {"selected" if option == selected else ""}>{escape(label)}</option>'
        for option, label in options.items()
    )


# --------------------------------------------------------------------- trading accounts


def account_fields(account: Account | None = None) -> str:
    def value(attr: str) -> str:
        current = getattr(account, attr, None) if account else None
        return "" if current is None else escape(str(current))

    return f"""
      <div class="form-row">
        <div class="field"><label>Name</label><input name="name" maxlength="120" required value="{value('name')}" placeholder="IBKR Live" /></div>
        <div class="field"><label>Broker</label><input name="broker" maxlength="120" required value="{value('broker')}" placeholder="Interactive Brokers" /></div>
      </div>
      <div class="form-row">
        <div class="field"><label>Type</label><select name="account_type">{options_html(ACCOUNT_TYPES, account.account_type if account else None)}</select></div>
        <div class="field"><label>Currency</label><input name="currency" maxlength="8" required value="{value('currency') or 'USD'}" /></div>
        <div class="field"><label>Starting balance</label><input name="starting_balance" type="number" step="any" min="0" required value="{value('starting_balance')}" /></div>
      </div>
      <details {"open" if account and account.account_type == AccountType.PROP_FIRM else ""}>
        <summary class="hint">Prop firm rules (optional)</summary>
        <div class="form-row" style="margin-top:10px">
          <div class="field"><label>Prop firm</label><input name="prop_firm_name" maxlength="120" value="{value('prop_firm_name')}" placeholder="FTMO" /></div>
          <div class="field"><label>Max daily loss</label><input name="max_daily_loss" type="number" step="any" min="0" value="{value('max_daily_loss')}" /></div>
          <div class="field"><label>Max total loss</label><input name="max_total_loss" type="number" step="any" min="0" value="{value('max_total_loss')}" /></div>
          <div class="field"><label>Daily profit target</label><input name="daily_profit_target" type="number" step="any" min="0" value="{value('daily_profit_target')}" /></div>
        </div>
      </details>"""


def read_account_form(form: dict[str, str]) -> dict[str, object]:
    currency = text(form["currency"], "Currency", 8).upper()
    if not currency.isalpha() or len(currency) < 3:
        raise FormProblem("Currency must be a 3-letter code like USD or GBP.")
    return {
        "name": text(form["name"], "Name", 120),
        "broker": text(form["broker"], "Broker", 120),
        "account_type": choice(form["account_type"], ACCOUNT_TYPES, "Account type"),
        "currency": currency,
        "starting_balance": amount(form["starting_balance"], "Starting balance", required=True, positive=True),
        "prop_firm_name": text(form["prop_firm_name"], "Prop firm", 120, required=False),
        "max_daily_loss": amount(form["max_daily_loss"], "Max daily loss"),
        "max_total_loss": amount(form["max_total_loss"], "Max total loss"),
        "daily_profit_target": amount(form["daily_profit_target"], "Daily profit target"),
    }


class AccountForm:
    """The account form's fields, as a FastAPI dependency (shared by create and update)."""

    def __init__(
        self,
        name: str = Form(""),
        broker: str = Form(""),
        account_type: str = Form(""),
        currency: str = Form(""),
        starting_balance: str = Form(""),
        prop_firm_name: str = Form(""),
        max_daily_loss: str = Form(""),
        max_total_loss: str = Form(""),
        daily_profit_target: str = Form(""),
    ) -> None:
        self.fields = {
            "name": name, "broker": broker, "account_type": account_type, "currency": currency,
            "starting_balance": starting_balance, "prop_firm_name": prop_firm_name, "max_daily_loss": max_daily_loss,
            "max_total_loss": max_total_loss, "daily_profit_target": daily_profit_target,
        }


def owned_account(db, user_id: str, account_id: str) -> Account | None:
    return db.scalar(select(Account).where(Account.id == account_id, Account.user_id == user_id))


@router.get("/settings/accounts", response_class=HTMLResponse)
def accounts_page(message: str = Query(default=""), error: str = Query(default=""), edit: str = Query(default="")) -> str:
    db = SessionLocal()
    try:
        user = current_user(db)
        accounts = db.scalars(select(Account).where(Account.user_id == user.id).order_by(Account.created_at)).all()
        active = [account for account in accounts if account.archived_at is None]
        archived = [account for account in accounts if account.archived_at is not None]

        def row(account: Account) -> str:
            rules = (
                f"{escape(account.prop_firm_name or 'Prop firm')}: max daily {money(account.max_daily_loss)}, max total {money(account.max_total_loss)}"
                if account.account_type == AccountType.PROP_FIRM else ""
            )
            if account.id == edit:
                return f"""<tr><td colspan="6"><form method="post" action="/settings/accounts/{escape(account.id)}" style="display:grid;gap:12px">
                  {account_fields(account)}
                  <div class="actions"><button class="btn btn-primary" type="submit">Save changes</button><a class="btn" href="/settings/accounts">Cancel</a></div>
                </form></td></tr>"""
            action = (
                f'<a class="btn btn-sm" href="/settings/accounts?edit={escape(account.id)}">Edit</a>'
                f'<form method="post" action="/settings/accounts/{escape(account.id)}/archive" style="margin:0"><button class="btn btn-sm" type="submit">Archive</button></form>'
                if account.archived_at is None
                else f'<form method="post" action="/settings/accounts/{escape(account.id)}/restore" style="margin:0"><button class="btn btn-sm" type="submit">Restore</button></form>'
            )
            return f"""<tr><td><b>{escape(account.name)}</b><div class="hint" style="margin:0">{rules}</div></td>
              <td>{escape(ACCOUNT_TYPES[account.account_type])}</td><td>{escape(account.broker)}</td><td>{escape(account.currency)}</td>
              <td class="num">{money(account.starting_balance)}</td><td><div class="actions">{action}</div></td></tr>"""

        head = "<thead><tr><th>Account</th><th>Type</th><th>Broker</th><th>Currency</th><th class=\"num\">Starting balance</th><th></th></tr></thead>"
        active_rows = "".join(row(account) for account in active) or '<tr><td colspan="6" class="muted">No trading accounts yet. Create your first one below.</td></tr>'
        archived_html = (
            f"""<details class="card" style="margin-top:14px"><summary><b>Archived accounts ({len(archived)})</b></summary>
              <div class="table-wrap" style="margin-top:10px"><table class="dense">{head}<tbody>{''.join(row(a) for a in archived)}</tbody></table></div></details>"""
            if archived else ""
        )
        body = f"""
          {notice(message, error)}
          <section class="card">
            <div class="card-title"><h2>Trading accounts</h2><span class="meta">Archived accounts keep their trades but leave the account picker</span></div>
            <div class="table-wrap"><table class="dense">{head}<tbody>{active_rows}</tbody></table></div>
          </section>
          {archived_html}
          <form class="card" method="post" action="/settings/accounts" style="margin-top:14px;display:grid;gap:12px">
            <div class="card-title"><h2>New trading account</h2></div>
            {account_fields()}
            <div><button class="btn btn-primary" type="submit">Create account</button></div>
          </form>"""
        return shell("Trading accounts", "accounts", body, "Settings", user.name or user.email)
    finally:
        db.close()


@router.post("/settings/accounts")
def create_account(form: AccountForm = Depends()) -> RedirectResponse:
    db = SessionLocal()
    try:
        user = current_user(db)
        try:
            values = read_account_form(form.fields)
        except FormProblem as problem:
            return back("/settings/accounts", error=str(problem))
        db.add(Account(user_id=user.id, **values))
        db.commit()
        return back("/settings/accounts", message=f"Created {values['name']}.")
    finally:
        db.close()



@router.post("/settings/accounts/{account_id}")
def update_account(account_id: str, form: AccountForm = Depends()) -> RedirectResponse:
    db = SessionLocal()
    try:
        user = current_user(db)
        account = owned_account(db, user.id, account_id)
        if account is None:
            return back("/settings/accounts", error="Account not found.")
        try:
            values = read_account_form(form.fields)
        except FormProblem as problem:
            return back("/settings/accounts", error=str(problem))
        for key, value in values.items():
            setattr(account, key, value)
        db.commit()
        return back("/settings/accounts", message=f"Saved {account.name}.")
    finally:
        db.close()


@router.post("/settings/accounts/{account_id}/archive")
def archive_account(account_id: str) -> RedirectResponse:
    return set_archived(account_id, archived=True)


@router.post("/settings/accounts/{account_id}/restore")
def restore_account(account_id: str) -> RedirectResponse:
    return set_archived(account_id, archived=False)


def set_archived(account_id: str, archived: bool) -> RedirectResponse:
    db = SessionLocal()
    try:
        user = current_user(db)
        account = owned_account(db, user.id, account_id)
        if account is None:
            return back("/settings/accounts", error="Account not found.")
        account.archived_at = datetime.now(UTC) if archived else None
        db.commit()
        return back("/settings/accounts", message=f"{'Archived' if archived else 'Restored'} {account.name}.")
    finally:
        db.close()


# --------------------------------------------------------------------- instruments


@router.get("/settings/instruments", response_class=HTMLResponse)
def instruments_page(message: str = Query(default=""), error: str = Query(default="")) -> str:
    db = SessionLocal()
    try:
        user = current_user(db)
        instruments = db.scalars(select(Instrument).order_by(Instrument.asset_class, Instrument.symbol)).all()
        rows = "".join(
            f"""<tr><td><b>{escape(row.symbol)}</b></td><td>{escape(row.name)}</td><td>{escape(ASSET_CLASSES[row.asset_class])}</td>
              <td class="num">{escape(plain_number(row.point_value or 1))}</td><td>{escape(row.currency)}</td><td>{escape(row.exchange or '')}</td></tr>"""
            for row in instruments
        ) or '<tr><td colspan="6" class="muted">No instruments yet. Add the symbols you trade below.</td></tr>'
        body = f"""
          {notice(message, error)}
          <section class="card">
            <div class="card-title"><h2>Instruments</h2><span class="meta">Point value = profit for a 1.00 price move on 1 unit (stocks 1, ES 50, NQ 20, standard forex lot 100000)</span></div>
            <div class="table-wrap"><table class="dense"><thead><tr><th>Symbol</th><th>Name</th><th>Type</th><th class="num">Point value</th><th>Currency</th><th>Exchange</th></tr></thead><tbody>{rows}</tbody></table></div>
          </section>
          <form class="card" method="post" action="/settings/instruments" style="margin-top:14px;display:grid;gap:12px">
            <div class="card-title"><h2>Add an instrument</h2></div>
            <div class="form-row">
              <div class="field"><label>Symbol</label><input name="symbol" maxlength="32" required placeholder="AAPL" /></div>
              <div class="field"><label>Name</label><input name="name" maxlength="160" required placeholder="Apple Inc." /></div>
              <div class="field"><label>Type</label><select name="asset_class">{options_html(ASSET_CLASSES)}</select></div>
            </div>
            <div class="form-row">
              <div class="field"><label>Point value</label><input name="point_value" type="number" step="any" min="0" value="1" required /></div>
              <div class="field"><label>Tick size (optional)</label><input name="tick_size" type="number" step="any" min="0" /></div>
              <div class="field"><label>Currency</label><input name="currency" maxlength="8" value="USD" required /></div>
              <div class="field"><label>Exchange (optional)</label><input name="exchange" maxlength="80" placeholder="NASDAQ" /></div>
            </div>
            <div><button class="btn btn-primary" type="submit">Add instrument</button></div>
          </form>"""
        return shell("Instruments", "accounts", body, "Settings", user.name or user.email)
    finally:
        db.close()


@router.post("/settings/instruments")
def create_instrument(
    symbol: str = Form(""),
    name: str = Form(""),
    asset_class: str = Form(""),
    point_value: str = Form("1"),
    tick_size: str = Form(""),
    currency: str = Form("USD"),
    exchange: str = Form(""),
) -> RedirectResponse:
    db = SessionLocal()
    try:
        current_user(db)
        try:
            instrument = Instrument(
                symbol=text(symbol, "Symbol", 32).upper(),
                name=text(name, "Name", 160),
                asset_class=choice(asset_class, ASSET_CLASSES, "Type"),
                point_value=amount(point_value, "Point value", required=True, positive=True),
                tick_size=amount(tick_size, "Tick size"),
                currency=text(currency, "Currency", 8).upper(),
                exchange=text(exchange, "Exchange", 80, required=False),
            )
        except FormProblem as problem:
            return back("/settings/instruments", error=str(problem))
        db.add(instrument)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return back("/settings/instruments", error=f"{instrument.symbol} already exists as a {ASSET_CLASSES[instrument.asset_class].lower()} instrument.")
        return back("/settings/instruments", message=f"Added {instrument.symbol}.")
    finally:
        db.close()
