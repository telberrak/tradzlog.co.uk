"""Settings → Your data: download everything, or delete the account (UK GDPR access and erasure)."""

from __future__ import annotations

import hmac
import logging
import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime
from html import escape
from typing import IO
from urllib.parse import quote

from fastapi import APIRouter, Form, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from tradzlog_api.config import settings
from tradzlog_api.security import verify_password
from tradzlog_api.services import email as mail
from tradzlog_api.services import storage
from tradzlog_api.services.account_data import delete_account, write_export
from tradzlog_db.session import SessionLocal
from tradzlog_web.auth import current_user, end_session
from tradzlog_web.ui import public_page, shell

router = APIRouter()
logger = logging.getLogger("tradzlog.web.data")

CONFIRM_WORD = "DELETE"
CHUNK = 1024 * 1024


@router.get("/settings/data", response_class=HTMLResponse)
def data_page(error: str = Query(default="")) -> str:
    db = SessionLocal()
    try:
        user = current_user(db)
        problem = f'<p class="form-error" role="alert">{escape(error)}</p>' if error else ""
        body = f"""
          {problem}
          <section class="grid two-col">
            <form class="card" method="post" action="/settings/data/export" style="display:grid;gap:14px;align-content:start">
              <div class="card-title"><h2>Download your data</h2></div>
              <p class="muted" style="margin:0">A ZIP file with everything TradzLog holds about you: profile, trading accounts,
                trades and executions, journal entries, imports and AI coaching, as JSON and as one CSV per table,
                plus your screenshots.</p>
              <button class="btn btn-primary" type="submit">Download ZIP</button>
            </form>
            <form class="card" method="post" action="/settings/data/delete" style="display:grid;gap:14px">
              <div class="card-title"><h2>Delete your account</h2></div>
              <p class="muted" style="margin:0">This permanently deletes your account, trades, journal and screenshots straight
                away. It can't be undone, so download your data first if you might want it.</p>
              <div class="field"><label for="password">Password</label>
                <input id="password" name="password" type="password" autocomplete="current-password" required /></div>
              <div class="field"><label for="confirm">Type {CONFIRM_WORD} to confirm</label>
                <input id="confirm" name="confirm" autocomplete="off" required pattern="{CONFIRM_WORD}" /></div>
              <button class="btn btn-danger" type="submit">Delete my account</button>
            </form>
          </section>"""
        return shell("Your data", "settings", body, "Settings", user.name or user.email)
    finally:
        db.close()


def build_export() -> tuple[IO[bytes], str]:
    db = SessionLocal()
    try:
        user = current_user(db)
        out = tempfile.SpooledTemporaryFile(max_size=32 * CHUNK)
        counts = write_export(db, user, storage.get_storage(), out)
        logger.info("data export for user %s: %s", user.id, counts)
        out.seek(0)
        return out, f"tradzlog-export-{datetime.now(UTC):%Y-%m-%d}.zip"
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def stream(out: IO[bytes]) -> Iterator[bytes]:
    try:
        while chunk := out.read(CHUNK):
            yield chunk
    finally:
        out.close()


@router.post("/settings/data/export")
async def export_data() -> StreamingResponse:
    out, name = await run_in_threadpool(build_export)
    return StreamingResponse(
        stream(out),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"},
    )


def data_redirect(error: str) -> RedirectResponse:
    return RedirectResponse(f"/settings/data?error={quote(error)}", status_code=status.HTTP_303_SEE_OTHER)


def remove_account(request: Request, password: str, confirm: str) -> RedirectResponse:
    db = SessionLocal()
    try:
        user = current_user(db)
        if not hmac.compare_digest(confirm.strip().upper(), CONFIRM_WORD):
            return data_redirect(f"Type {CONFIRM_WORD} to confirm.")
        if user.hashed_password and not verify_password(password, user.hashed_password):
            return data_redirect("Your password is incorrect.")
        user_id, address, name = user.id, user.email, user.name
        result = delete_account(db, user, storage.get_storage())
        logger.info("deleted user %s: %s", user_id, result)
        response = RedirectResponse("/account-deleted", status_code=status.HTTP_303_SEE_OTHER)
        end_session(db, request, response)
        db.commit()
        mail.send(mail.account_deleted(address, name))
        return response
    finally:
        db.close()


@router.post("/settings/data/delete")
async def delete_data(request: Request, password: str = Form(""), confirm: str = Form("")) -> RedirectResponse:
    return await run_in_threadpool(remove_account, request, password, confirm)


@router.get("/account-deleted", response_class=HTMLResponse)
def account_deleted() -> str:
    email = escape(settings.support_email)
    body = f"""<section class="error-page"><p class="code">Account deleted</p><h1>Your account has been deleted</h1>
      <p>Your trades, journal and screenshots have been removed. Copies in our rolling backups expire within 30 days.
        Questions? Email <a href="mailto:{email}">{email}</a>.</p><a class="btn btn-primary" href="/">Go to the home page</a></section>"""
    return public_page("Account deleted", body)
