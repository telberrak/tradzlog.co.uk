"""Broker import: upload -> preview (nothing written) -> confirm -> summary, with undo."""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from html import escape
from urllib.parse import quote

from fastapi import APIRouter, File, Form, Query, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete, select
from starlette.concurrency import run_in_threadpool

from tradzlog_api.services.imports import DATE_ORDERS, FORMATS, PARSER_VERSION, ImportFormatError, parse_import
from tradzlog_api.services.trade_import import (
    UndoNotAllowed,
    apply_import,
    fingerprints,
    from_row,
    latest_undoable,
    plan_import,
    to_row,
    undo_import,
)
from tradzlog_db.models import Account, BrokerSync, BrokerSyncStatus, BrokerSyncType
from tradzlog_db.session import SessionLocal
from tradzlog_web.auth import current_user
from tradzlog_web.components import kpi, money, number, plain_number
from tradzlog_web.localtime import fmt, timezone_names, valid_timezone, zone
from tradzlog_web.ui import shell

logger = logging.getLogger("tradzlog.web.import")
router = APIRouter()

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_ROWS = 20_000


def back(path: str, message: str = "", error: str = "") -> RedirectResponse:
    query = f"?error={quote(error)}" if error else (f"?message={quote(message)}" if message else "")
    return RedirectResponse(path + query, status_code=status.HTTP_303_SEE_OTHER)


def notice(message: str, error: str) -> str:
    if error:
        return f'<p class="form-error" role="alert">{escape(error)}</p>'
    return f'<p class="form-ok">{escape(message)}</p>' if message else ""


def owned_batch(db, user_id: str, batch_id: str) -> tuple[BrokerSync, Account] | None:
    found = db.execute(
        select(BrokerSync, Account).join(Account, Account.id == BrokerSync.account_id)
        .where(BrokerSync.id == batch_id, Account.user_id == user_id)
    ).first()
    return (found[0], found[1]) if found else None


def batch_state(batch: BrokerSync) -> tuple[str, str]:
    if batch.undone_at:
        return "Undone", "badge-cancelled"
    if batch.status == BrokerSyncStatus.SUCCESS and batch.imported_at:
        return "Imported", "badge-completed"
    if batch.status == BrokerSyncStatus.PENDING:
        return "Waiting for confirmation", "badge-pending"
    if batch.status == BrokerSyncStatus.FAILED:
        return "Failed", "badge-failed"
    return batch.status.value.title(), ""


# --------------------------------------------------------------------- upload page


@router.get("/settings/import", response_class=HTMLResponse)
def import_page(message: str = Query(default=""), error: str = Query(default="")) -> str:
    db = SessionLocal()
    try:
        user = current_user(db)
        accounts = db.scalars(select(Account).where(Account.user_id == user.id, Account.archived_at.is_(None)).order_by(Account.created_at)).all()
        history = db.execute(
            select(BrokerSync, Account).join(Account, Account.id == BrokerSync.account_id)
            .where(Account.user_id == user.id).order_by(BrokerSync.created_at.desc()).limit(50)
        ).all()
        if not accounts:
            body = f'{notice(message, error)}<section class="empty-state page-block"><h3>Create a trading account first</h3><p>Imports go into one of your trading accounts.</p><a class="btn btn-primary" href="/settings/accounts">Create trading account</a></section>'
            return shell("Import trades", "settings", body, "Settings", user.name or user.email)
        account_options = "".join(f'<option value="{escape(a.id)}">{escape(a.name)} · {escape(a.broker)}</option>' for a in accounts)
        format_options = "".join(f'<option value="{code}">{escape(label)}</option>' for code, label in FORMATS.items())
        date_options = "".join(f'<option value="{code}">{escape(label)}</option>' for code, label in DATE_ORDERS.items())
        tz_options = "".join(
            f'<option value="{escape(name)}" {"selected" if name == user.timezone else ""}>{escape(name.replace("_", " "))}</option>'
            for name in timezone_names()
        )
        rows = ""
        for batch, account in history:
            label, css = batch_state(batch)
            summary = batch.summary or {}
            counts = (
                f"{summary.get('fills_imported', 0)} fills · {summary.get('trades_opened', 0)} trades · {summary.get('duplicates', 0)} duplicates"
                if batch.imported_at else ""
            )
            rows += f"""<tr data-href="/settings/import/{escape(batch.id)}"><td class="muted">{escape(fmt(batch.created_at))}</td>
              <td><a href="/settings/import/{escape(batch.id)}">{escape(batch.file_name or "Import")}</a></td>
              <td>{escape(account.name)}</td><td>{escape(FORMATS.get(batch.file_format or "", batch.broker))}</td>
              <td><span class="badge {css}">{escape(label)}</span></td><td class="muted">{escape(counts)}</td></tr>"""
        rows = rows or '<tr><td colspan="6" class="muted">No imports yet.</td></tr>'
        body = f"""
          {notice(message, error)}
          <section class="grid two-col">
            <form class="card" method="post" action="/settings/import/preview" enctype="multipart/form-data" style="display:grid;gap:12px">
              <div class="card-title"><h2>Import from your broker</h2></div>
              <div class="form-row">
                <div class="field"><label for="i-account">Into account</label><select id="i-account" name="account_id" required>{account_options}</select></div>
                <div class="field"><label for="i-format">File format</label><select id="i-format" name="file_format">{format_options}</select></div>
              </div>
              <div class="form-row">
                <div class="field"><label for="i-tz">Times in the file are in</label><select id="i-tz" name="timezone">{tz_options}</select></div>
                <div class="field"><label for="i-dates">Date order</label><select id="i-dates" name="date_order">{date_options}</select></div>
              </div>
              <p class="hint" style="margin-top:-4px">Brokers often export in their own timezone (IBKR: your report setting). NinjaTrader writes dates in your computer's regional format.</p>
              <div class="field"><label for="i-file">Export file (CSV, or MetaTrader HTML report)</label><input id="i-file" type="file" name="file" accept=".csv,.txt,.htm,.html,text/csv,text/html" /></div>
              <div class="field"><label for="i-paste">…or paste rows (with a header row)</label><textarea id="i-paste" name="pasted" placeholder="Symbol,Date/Time,Side,Quantity,Price,Commission" style="min-height:90px"></textarea></div>
              <div><button class="btn btn-primary" type="submit">Preview import</button> <span class="hint">Nothing is saved until you confirm.</span></div>
            </form>
            <section class="card">
              <div class="card-title"><h2>Supported exports</h2></div>
              <ul class="muted" style="margin:0;padding-left:18px;line-height:1.7">
                <li><b>Interactive Brokers</b>: Flex Query with the Trades section, CSV.</li>
                <li><b>MetaTrader 5</b>: History → Report (HTML), or a Deals CSV.</li>
                <li><b>NinjaTrader</b>: Account Performance → Executions, export CSV.</li>
                <li><b>Tradovate</b>: Reports → Performance, export CSV.</li>
                <li><b>tastytrade</b>: History → Transactions, choose the dates, CSV.</li>
                <li><b>Anything else</b>: a CSV with symbol, date/time, side (or signed quantity), quantity and price columns.</li>
              </ul>
              <p class="hint">Re-importing the same file, or an overlapping one, skips fills that are already in the account.</p>
            </section>
          </section>
          <section class="card" style="margin-top:14px">
            <div class="card-title"><h2>Import history</h2></div>
            <div class="table-wrap"><table class="dense"><thead><tr><th>Uploaded</th><th>File</th><th>Account</th><th>Format</th><th>Status</th><th>Result</th></tr></thead><tbody>{rows}</tbody></table></div>
          </section>"""
        return shell("Import trades", "settings", body, "Settings", user.name or user.email)
    finally:
        db.close()


@router.post("/settings/import/preview")
async def upload_for_preview(
    account_id: str = Form(...),
    file_format: str = Form("auto"),
    timezone: str = Form("UTC"),
    date_order: str = Form("auto"),
    pasted: str = Form(""),
    file: UploadFile | None = File(default=None),
) -> RedirectResponse:
    raw = b""
    file_name = "Pasted rows"
    if file is not None and file.filename:
        raw = await file.read(MAX_FILE_BYTES + 1)
        file_name = file.filename[:255]
        await file.close()
    elif pasted.strip():
        raw = pasted.encode("utf-8")
    if not raw:
        return back("/settings/import", error="Choose a file or paste some rows.")
    if len(raw) > MAX_FILE_BYTES:
        return back("/settings/import", error="That file is over 5 MB. Export a shorter date range.")
    if file_format not in FORMATS or date_order not in DATE_ORDERS or not valid_timezone(timezone):
        return back("/settings/import", error="Choose a file format and timezone from the lists.")
    # Parsing and saving can take a while for big files: keep them off the event loop.
    return await run_in_threadpool(create_preview, account_id, file_format, timezone, date_order, file_name, raw)


def create_preview(account_id: str, file_format: str, timezone: str, date_order: str, file_name: str, raw: bytes) -> RedirectResponse:
    db = SessionLocal()
    try:
        user = current_user(db)
        account = db.scalar(select(Account).where(Account.id == account_id, Account.user_id == user.id))
        if account is None:
            return back("/settings/import", error="Account not found.")
        try:
            result = parse_import(raw, file_format, zone(timezone), date_order)
        except ImportFormatError as problem:
            return back("/settings/import", error=str(problem))
        if len(result.executions) > MAX_ROWS:
            return back("/settings/import", error=f"That file has more than {MAX_ROWS:,} fills. Export a shorter date range.")
        batch = BrokerSync(
            account_id=account.id, broker=account.broker, sync_type=BrokerSyncType.CSV, status=BrokerSyncStatus.PENDING,
            file_name=file_name, file_format=result.file_format, source_timezone=timezone,
            payload={
                "rows": [to_row(fill) for fill in result.executions],
                "problems": [{"row": p.row_number, "reason": p.reason} for p in result.problems[:500]],
                "problem_count": len(result.problems),
                "skipped": result.skipped,
                "date_note": result.date_note,
                "parser_version": PARSER_VERSION,
            },
        )
        db.add(batch)
        db.commit()
        return RedirectResponse(f"/settings/import/{batch.id}", status_code=status.HTTP_303_SEE_OTHER)
    finally:
        db.close()


# --------------------------------------------------------------------- preview / summary


def pnl_check(tradzlog: Decimal | None, broker: Decimal | None) -> str:
    if broker is None or tradzlog is None:
        return ""
    gap = tradzlog - broker
    verdict = (
        '<span class="positive">matches</span>' if abs(gap) < Decimal("0.01")
        else f'<span class="amber">differs by {money(gap, signed=True)}</span> (positions still open, or fees the file doesn\'t list, cause small gaps)'
    )
    return f'<p class="hint">Broker-reported P&amp;L for these fills: <b>{money(broker, signed=True)}</b>; TradzLog: <b>{money(tradzlog, signed=True)}</b>, {verdict}.</p>'


def stale(batch: BrokerSync) -> bool:
    return (batch.payload or {}).get("parser_version") != PARSER_VERSION


STALE_MESSAGE = ("This upload was read by an older version of the importer, which has since been improved. "
                 "Discard it and upload the file again to import it correctly.")


def preview_body(db, batch: BrokerSync, account: Account) -> str:
    payload = batch.payload or {}
    if stale(batch):
        return f"""<p class="form-error" role="alert">{escape(STALE_MESSAGE)}</p>
          <form method="post" action="/settings/import/{escape(batch.id)}/discard"><button class="btn btn-primary" type="submit">Discard this upload</button></form>"""
    fills = [from_row(row) for row in payload.get("rows", [])]
    plan = plan_import(db, account, fills)
    new_prints = {fingerprint for _, fingerprint in plan.new_fills}
    prints = fingerprints(account.id, fills)
    instruments = "".join(
        f"""<tr><td><b>{escape(symbol)}</b></td><td>{escape(spec.asset_class.value.title())}</td>
          <td><input name="pv_{escape(symbol)}" type="number" step="any" min="0" value="{escape(plain_number(spec.point_value))}" style="max-width:140px" /></td>
          <td>{'<span class="badge badge-review">Check this</span>' if spec.needs_review else '<span class="muted small">From the file or a known contract</span>'}</td></tr>"""
        for symbol, spec in sorted(plan.new_instruments.items())
    )
    instruments_html = (
        f"""<section class="card" style="margin-top:14px"><div class="card-title"><h2>New instruments</h2>
          <span class="meta">Point value = profit for a 1.00 price move on 1 unit (ES 50, standard forex lot 100000)</span></div>
          <div class="table-wrap"><table class="dense"><thead><tr><th>Symbol</th><th>Type</th><th>Point value</th><th></th></tr></thead><tbody>{instruments}</tbody></table></div></section>"""
        if instruments else ""
    )
    problems = payload.get("problems", [])
    problems_html = (
        f"""<details class="card" style="margin-top:14px" open><summary><b>{payload.get('problem_count', len(problems))} rows couldn't be read</b> (they will be left out)</summary>
          <ul class="small" style="margin:10px 0 0;padding-left:18px">{''.join(f"<li>Row {p['row']}: {escape(p['reason'])}</li>" for p in problems[:50])}</ul></details>"""
        if problems else ""
    )
    fill_rows = "".join(
        f"""<tr><td class="muted">{escape(fmt(fill.executed_at))}</td><td><b>{escape(fill.symbol)}</b></td>
          <td><span class="badge {'badge-long' if fill.side == 'BUY' else 'badge-short' if fill.side == 'SELL' else ''}">{'EXPIRE/ASSIGN' if fill.side == 'CLOSE' else fill.side}</span></td>
          <td class="num">{escape(number(fill.quantity).rstrip('0').rstrip('.'))}</td><td class="num">{escape(plain_number(fill.price))}</td>
          <td class="num">{money(fill.fees)}</td><td class="muted">{escape(fill.broker_id or '')}</td>
          <td>{'<span class="badge badge-open">New</span>' if print_ in new_prints else '<span class="badge">Already imported</span>'}</td></tr>"""
        for fill, print_ in list(zip(fills, prints, strict=True))[:200]
    )
    more = f'<p class="hint">Showing the first 200 of {len(fills):,} fills.</p>' if len(fills) > 200 else ""
    nothing_new = not plan.new_fills
    broker_hint = (
        f'<p class="hint">The file reports a P&amp;L of <b>{money(plan.broker_pnl, signed=True)}</b> for the new fills. '
        "After importing, TradzLog's own figure is shown next to it.</p>"
        if plan.broker_pnl is not None else ""
    )
    return f"""
      <section class="kpi-row">
        {kpi("Fills in file", f"{plan.total_rows:,}", "", f"{payload.get('skipped', 0)} non-trade rows skipped")}
        {kpi("New fills", f"{len(plan.new_fills):,}", "positive" if plan.new_fills else "", f"{plan.duplicates:,} already imported")}
        {kpi("Trades", f"{plan.trades_opened:,} new", "", f"{plan.trades_closed:,} closed · {plan.trades_continued:,} continued")}
        {kpi("Instruments", f"{len(plan.new_instruments):,} new", "", "added to the shared list")}
        {kpi("Unreadable rows", f"{payload.get('problem_count', 0):,}", "negative" if problems else "", "see below" if problems else "none")}
      </section>
      {f'<p class="form-error" role="status">{escape(payload["date_note"])}</p>' if payload.get("date_note") else ""}
      {broker_hint}
      <form method="post" action="/settings/import/{escape(batch.id)}/confirm">
        {instruments_html}
        <div class="actions" style="margin:14px 0">
          <button class="btn btn-primary" type="submit" {'disabled' if nothing_new else ''}>{'Nothing new to import' if nothing_new else f'Import {len(plan.new_fills):,} fills into {escape(account.name)}'}</button>
          <span class="hint">File times read as {escape(batch.source_timezone or 'UTC')}, shown in your timezone.</span>
        </div>
      </form>
      <form method="post" action="/settings/import/{escape(batch.id)}/discard" style="margin:0 0 14px"><button class="btn btn-sm" type="submit">Discard this upload</button></form>
      {problems_html}
      <section class="card" style="margin-top:14px"><div class="card-title"><h2>Fills</h2><span class="meta">{escape(FORMATS.get(batch.file_format or '', ''))}</span></div>
        <div class="table-wrap"><table class="dense"><thead><tr><th>Time</th><th>Symbol</th><th>Side</th><th class="num">Qty</th><th class="num">Price</th><th class="num">Fees</th><th>Broker ID</th><th></th></tr></thead><tbody>{fill_rows}</tbody></table></div>{more}
      </section>"""


def summary_body(db, batch: BrokerSync, account: Account) -> str:
    summary = batch.summary or {}
    tradzlog = Decimal(summary["tradzlog_pnl"]) if summary.get("tradzlog_pnl") is not None else None
    broker = Decimal(summary["broker_pnl"]) if summary.get("broker_pnl") is not None else None
    created = summary.get("instruments_created") or []
    if batch.undone_at:
        action = f'<p class="muted">Undone {escape(fmt(batch.undone_at))}: {summary.get("undone_fills", 0)} fills and {summary.get("undone_trades_deleted", 0)} trades removed.</p>'
    elif (latest := latest_undoable(db, account.id)) is not None and latest.id == batch.id:
        action = f"""<form method="post" action="/settings/import/{escape(batch.id)}/undo" onsubmit="return confirm('Remove every fill and trade this import added?');">
          <button class="btn btn-danger" type="submit">Undo this import</button></form>"""
    else:
        action = '<p class="hint">Only the latest import of an account can be undone.</p>'
    return f"""
      <section class="kpi-row">
        {kpi("Fills imported", f"{summary.get('fills_imported', 0):,}", "positive", f"{summary.get('duplicates', 0):,} already there, skipped")}
        {kpi("New trades", f"{summary.get('trades_opened', 0):,}", "", f"{summary.get('trades_closed', 0):,} closed · {summary.get('trades_continued', 0):,} continued")}
        {kpi("P&L of these trades", money(tradzlog, signed=True) if tradzlog is not None else "—", "", "net of fees")}
        {kpi("Broker P&L", money(broker, signed=True) if broker is not None else "Not in file", "", "as reported in the file")}
        {kpi("New instruments", str(len(created)), "", ", ".join(created[:4]) + ("…" if len(created) > 4 else ""))}
      </section>
      {pnl_check(tradzlog, broker)}
      <div class="actions" style="margin:14px 0"><a class="btn btn-primary" href="/trades?account_id={escape(account.id)}&amp;range=ALL">View trades</a>
        <a class="btn" href="/settings/import">Import another file</a>{action}</div>"""


@router.get("/settings/import/{batch_id}", response_class=HTMLResponse)
def import_detail(batch_id: str, message: str = Query(default=""), error: str = Query(default="")) -> str:
    db = SessionLocal()
    try:
        user = current_user(db)
        found = owned_batch(db, user.id, batch_id)
        if found is None:
            return shell("Import", "settings", '<section class="empty-state"><h3>Import not found</h3><a class="btn" href="/settings/import">Back to imports</a></section>', "Settings", user.name or user.email)
        batch, account = found
        label, css = batch_state(batch)
        content = preview_body(db, batch, account) if batch.status == BrokerSyncStatus.PENDING else summary_body(db, batch, account)
        body = f"""{notice(message, error)}
          <div class="trade-head"><span class="sym" style="font-size:20px">{escape(batch.file_name or 'Import')}</span>
            <span class="badge {css}">{escape(label)}</span><span class="meta">into {escape(account.name)} · uploaded {escape(fmt(batch.created_at))}</span></div>
          {content}"""
        return shell("Import trades", "settings", body, "Settings", user.name or user.email)
    finally:
        db.close()


# --------------------------------------------------------------------- actions


def point_values_from(form) -> dict[str, Decimal]:
    values = {}
    for key, value in form.items():
        if key.startswith("pv_") and isinstance(value, str) and value.strip():
            try:
                values[key[3:]] = Decimal(value.strip())
            except InvalidOperation:
                continue
    return values


@router.post("/settings/import/{batch_id}/confirm")
async def confirm_import(batch_id: str, request: Request) -> RedirectResponse:
    point_values = point_values_from(await request.form())
    return await run_in_threadpool(run_confirm, batch_id, point_values)


def run_confirm(batch_id: str, point_values: dict[str, Decimal]) -> RedirectResponse:
    db = SessionLocal()
    try:
        user = current_user(db)
        found = owned_batch(db, user.id, batch_id)
        if found is None:
            return back("/settings/import", error="Import not found.")
        batch, account = found
        if batch.status != BrokerSyncStatus.PENDING:
            return back(f"/settings/import/{batch_id}", error="This import has already been processed.")
        if stale(batch):
            return back(f"/settings/import/{batch_id}", error=STALE_MESSAGE)
        fills = [from_row(row) for row in (batch.payload or {}).get("rows", [])]
        try:
            summary = apply_import(db, batch, account, fills, point_values)
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("import %s failed", batch_id)
            return back(f"/settings/import/{batch_id}", error="The import failed and nothing was saved. Please try again.")
        return back(f"/settings/import/{batch_id}", message=f"Imported {summary['fills_imported']:,} fills.")
    finally:
        db.close()


@router.post("/settings/import/{batch_id}/discard")
def discard_import(batch_id: str) -> RedirectResponse:
    db = SessionLocal()
    try:
        user = current_user(db)
        found = owned_batch(db, user.id, batch_id)
        if found is None or found[0].status != BrokerSyncStatus.PENDING:
            return back("/settings/import", error="Only an upload waiting for confirmation can be discarded.")
        db.execute(delete(BrokerSync).where(BrokerSync.id == batch_id))
        db.commit()
        return back("/settings/import", message="Upload discarded.")
    finally:
        db.close()


@router.post("/settings/import/{batch_id}/undo")
def undo(batch_id: str) -> RedirectResponse:
    db = SessionLocal()
    try:
        user = current_user(db)
        found = owned_batch(db, user.id, batch_id)
        if found is None:
            return back("/settings/import", error="Import not found.")
        batch, account = found
        try:
            result = undo_import(db, batch, account)
        except UndoNotAllowed as reason:
            return back(f"/settings/import/{batch_id}", error=str(reason))
        db.commit()
        return back(f"/settings/import/{batch_id}", message=f"Undone: {result['fills']:,} fills, {result['trades_deleted']:,} trades and {result['instruments_removed']:,} unused instruments removed.")
    finally:
        db.close()
