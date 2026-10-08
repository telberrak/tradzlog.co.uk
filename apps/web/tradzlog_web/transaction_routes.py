"""Transactions: deposits into and withdrawals from trading accounts.

They move an account's balance (dashboard equity curve, opening balances) but are never P&L, so
win rate, expectancy and drawdown ignore them (see book.equity_curve).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from html import escape

from fastapi import APIRouter, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from tradzlog_db.models import Account, CashTransaction, CashTransactionType
from tradzlog_db.session import SessionLocal
from tradzlog_web import book, localtime
from tradzlog_web.auth import current_user
from tradzlog_web.components import filter_bar, kpi, money, query_string, tone
from tradzlog_web.settings_routes import FormProblem, amount, back, choice, notice, owned_account, text
from tradzlog_web.ui import empty_state, shell

router = APIRouter()

TYPES = {CashTransactionType.DEPOSIT: "Deposit", CashTransactionType.WITHDRAWAL: "Withdrawal"}


def transaction_form(accounts: list[Account], selected: Account | None) -> str:
    options = "".join(
        f'<option value="{escape(account.id)}" {"selected" if selected and account.id == selected.id else ""}>{escape(account.name)}</option>'
        for account in accounts
    )
    kinds = "".join(f'<option value="{kind.value}">{label}</option>' for kind, label in TYPES.items())
    return f"""<form class="card" method="post" action="/transactions" style="display:grid;gap:12px;margin-bottom:14px">
      <div class="card-title"><h2>Record a deposit or withdrawal</h2></div>
      <div class="form-grid" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px">
        <div class="field"><label for="t-account">Account</label><select id="t-account" name="account_id" required>{options}</select></div>
        <div class="field"><label for="t-type">Type</label><select id="t-type" name="type">{kinds}</select></div>
        <div class="field"><label for="t-amount">Amount</label><input id="t-amount" name="amount" inputmode="decimal" placeholder="1,000.00" required /></div>
        <div class="field"><label for="t-date">Date</label><input id="t-date" name="occurred_on" type="date" value="{localtime.today().isoformat()}" required /></div>
        <div class="field" style="grid-column:1/-1"><label for="t-note">Note (optional)</label><input id="t-note" name="note" maxlength="255" placeholder="e.g. monthly top-up, payout" /></div>
      </div>
      <div><button class="btn btn-primary" type="submit">Save transaction</button></div>
    </form>"""


@router.get("/transactions", response_class=HTMLResponse)
def transactions_page(
    account_id: str | None = Query(default=None),
    range_code: str | None = Query(default=None, alias="range"),
    message: str = Query(default=""),
    error: str = Query(default=""),
) -> str:
    db = SessionLocal()
    try:
        user = current_user(db)
        accounts = list(db.scalars(
            select(Account).where(Account.user_id == user.id, Account.archived_at.is_(None)).order_by(Account.created_at)
        ).all())
        if not accounts:
            body = notice(message, error) + empty_state(
                "No trading accounts yet", "Deposits and withdrawals belong to a trading account. Create one first.",
                "/settings/accounts", "Add a trading account")
            return shell("Transactions", "transactions", body, "All accounts", user.name or user.email)
        selected = next((account for account in accounts if account.id == account_id), None)
        period = book.resolve_period(range_code or "ALL", localtime.today())
        scope = [selected.id] if selected else [account.id for account in accounts]
        rows = [
            row for row in db.scalars(
                select(CashTransaction).options(selectinload(CashTransaction.account))
                .where(CashTransaction.user_id == user.id, CashTransaction.account_id.in_(scope))
                .order_by(CashTransaction.occurred_on.desc(), CashTransaction.created_at.desc())
            ).all()
            if period.contains(row.occurred_on)
        ]
        deposits = sum((row.amount for row in rows if row.type == CashTransactionType.DEPOSIT), Decimal("0"))
        withdrawals = sum((row.amount for row in rows if row.type == CashTransactionType.WITHDRAWAL), Decimal("0"))
        back_to = escape("/transactions" + query_string({"account_id": selected.id if selected else None, "range": period.code}))
        table_rows = "".join(
            f"""<tr><td>{row.occurred_on:%d %b %Y}</td><td>{escape(row.account.name)}</td>
              <td><span class="badge {"badge-long" if row.type == CashTransactionType.DEPOSIT else "badge-short"}">{TYPES[row.type]}</span></td>
              <td class="num {tone(row.signed_amount)}">{money(row.signed_amount, signed=True)}</td>
              <td class="muted">{escape(row.note or "")}</td>
              <td><form method="post" action="/transactions/{escape(row.id)}/delete" style="margin:0">
                <input type="hidden" name="back" value="{back_to}" /><button class="btn btn-sm btn-ghost" type="submit">Delete</button></form></td></tr>"""
            for row in rows
        ) or '<tr><td colspan="6" class="muted">No deposits or withdrawals in this view.</td></tr>'
        body = f"""
          {notice(message, error)}
          {filter_bar("/transactions", accounts, selected.id if selected else None, period)}
          <section class="kpi-row">
            {kpi("Deposits", money(deposits), "positive" if deposits else "")}
            {kpi("Withdrawals", money(withdrawals), "negative" if withdrawals else "")}
            {kpi("Net cash flow", money(deposits - withdrawals, signed=True), tone(deposits - withdrawals))}
            {kpi("Transactions", str(len(rows)))}
          </section>
          {transaction_form(accounts, selected)}
          <section class="card">
            <div class="card-title"><h2>Deposits &amp; withdrawals</h2><span class="meta">They change the balance, never P&amp;L or drawdown</span></div>
            <div class="table-wrap"><table class="dense"><thead><tr><th>Date</th><th>Account</th><th>Type</th><th class="num">Amount</th><th>Note</th><th></th></tr></thead>
            <tbody>{table_rows}</tbody></table></div>
          </section>"""
        return shell("Transactions", "transactions", body, selected.name if selected else "All accounts", user.name or user.email)
    finally:
        db.close()


@router.post("/transactions")
def create_transaction(
    account_id: str = Form(""),
    type: str = Form("DEPOSIT"),  # noqa: A002 - the form field's name
    amount_text: str = Form("", alias="amount"),
    occurred_on: str = Form(""),
    note: str = Form(""),
) -> RedirectResponse:
    db = SessionLocal()
    try:
        user = current_user(db)
        account = owned_account(db, user.id, account_id)
        if account is None or account.archived_at is not None:
            return back("/transactions", error="Choose one of your trading accounts.")
        try:
            kind = choice(type, TYPES, "Type")
            value = amount(amount_text, "Amount", required=True, positive=True)
            day = book.parse_day(occurred_on)
            if day is None or day.year < 1970 or day > date(localtime.today().year + 1, 12, 31):
                raise FormProblem("Enter a valid date.")
            clean_note = text(note, "Note", 255, required=False)
        except FormProblem as problem:
            return back(f"/transactions?account_id={account.id}", error=str(problem))
        db.add(CashTransaction(user_id=user.id, account_id=account.id, type=kind, amount=value, occurred_on=day, note=clean_note))
        db.commit()
        return back(f"/transactions?account_id={account.id}", message=f"{TYPES[kind]} of {money(value)} saved to {account.name}.")
    finally:
        db.close()


@router.post("/transactions/{transaction_id}/delete")
def delete_transaction(transaction_id: str, back_to: str = Form("/transactions", alias="back")) -> RedirectResponse:
    db = SessionLocal()
    try:
        user = current_user(db)
        row = db.scalar(select(CashTransaction).where(CashTransaction.id == transaction_id, CashTransaction.user_id == user.id))
        target = back_to if back_to.startswith("/transactions") else "/transactions"
        if row is None:
            return back(target.split("?")[0], error="Transaction not found.")
        db.delete(row)
        db.commit()
        separator = "&" if "?" in target else "?"
        return RedirectResponse(f"{target}{separator}message=Transaction+deleted.", status_code=303)
    finally:
        db.close()
