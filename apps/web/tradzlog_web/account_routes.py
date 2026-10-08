"""Sign-in, sign-up, sign-out and the security settings page."""

from __future__ import annotations

from datetime import UTC, datetime
from html import escape
from urllib.parse import quote

from fastapi import APIRouter, Form, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete, select

from tradzlog_api.config import settings
from tradzlog_api.security import hash_password, verify_password
from tradzlog_api.services.users import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    EmailTaken,
    RegistrationClosed,
    authenticate,
    register_user,
)
from tradzlog_db.models import WebSession
from tradzlog_db.session import SessionLocal
from tradzlog_web.auth import (
    CURRENT_USER_ID,
    current_user,
    end_session,
    safe_next,
    start_session,
)
from tradzlog_web.ui import auth_page, shell

router = APIRouter()


def error_box(message: str) -> str:
    return f'<p class="form-error" role="alert">{escape(message)}</p>' if message else ""


def password_problem(password: str, repeat: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Use at least {MIN_PASSWORD_LENGTH} characters for your password."
    if len(password) > MAX_PASSWORD_LENGTH:
        return f"Use at most {MAX_PASSWORD_LENGTH} characters for your password."
    if password != repeat:
        return "The two passwords don't match."
    return ""


# --------------------------------------------------------------------- sign in


def login_form(next_path: str, email: str = "", error: str = "") -> str:
    signup_hint = (
        '<p class="switch">New here? <a href="/signup">Create an account</a></p>'
    )
    return auth_page(
        "Sign in",
        f"""{error_box(error)}
        <form method="post" action="/login">
          <input type="hidden" name="next" value="{escape(next_path)}" />
          <div class="field"><label for="email">Email</label>
            <input id="email" name="email" type="email" autocomplete="email" value="{escape(email)}" required autofocus /></div>
          <div class="field"><label for="password">Password</label>
            <input id="password" name="password" type="password" autocomplete="current-password" required /></div>
          <button class="btn btn-primary" type="submit">Sign in</button>
        </form>
        {signup_hint}""",
    )


@router.get("/login", response_class=HTMLResponse)
def login_page(next: str | None = Query(default=None)) -> HTMLResponse:  # noqa: A002 - query name
    if CURRENT_USER_ID.get() is not None:
        return RedirectResponse(safe_next(next), status_code=status.HTTP_303_SEE_OTHER)
    return HTMLResponse(login_form(safe_next(next)))


@router.post("/login")
def login_submit(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    next: str = Form("/dashboard"),  # noqa: A002 - form field name
) -> HTMLResponse:
    db = SessionLocal()
    try:
        user = authenticate(db, email, password)
        if user is None:
            # One message for unknown email and wrong password, so accounts can't be probed.
            return HTMLResponse(login_form(safe_next(next), email, "Email or password is incorrect."), status_code=400)
        response = RedirectResponse(safe_next(next), status_code=status.HTTP_303_SEE_OTHER)
        start_session(db, user, request, response)
        db.commit()
        return response
    finally:
        db.close()


# --------------------------------------------------------------------- sign up


def signup_form(name: str = "", email: str = "", error: str = "") -> str:
    invite = (
        ""
        if settings.registration_open
        else """<div class="field"><label for="invite_code">Invite code</label>
            <input id="invite_code" name="invite_code" autocomplete="off" required /></div>"""
    )
    return auth_page(
        "Create your account",
        f"""{error_box(error)}
        <form method="post" action="/signup">
          <div class="field"><label for="name">Name</label>
            <input id="name" name="name" autocomplete="name" maxlength="120" value="{escape(name)}" /></div>
          <div class="field"><label for="email">Email</label>
            <input id="email" name="email" type="email" autocomplete="email" maxlength="255" value="{escape(email)}" required /></div>
          <div class="field"><label for="password">Password</label>
            <input id="password" name="password" type="password" autocomplete="new-password" minlength="{MIN_PASSWORD_LENGTH}" maxlength="{MAX_PASSWORD_LENGTH}" required /></div>
          <div class="field"><label for="password2">Repeat password</label>
            <input id="password2" name="password2" type="password" autocomplete="new-password" required /></div>
          {invite}
          <button class="btn btn-primary" type="submit">Create account</button>
        </form>
        <p class="switch">Already have an account? <a href="/login">Sign in</a></p>""",
    )


@router.get("/signup", response_class=HTMLResponse)
def signup_page() -> HTMLResponse:
    if CURRENT_USER_ID.get() is not None:
        return RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    return HTMLResponse(signup_form())


@router.post("/signup")
def signup_submit(
    request: Request,
    name: str = Form(""),
    email: str = Form(""),
    password: str = Form(""),
    password2: str = Form(""),
    invite_code: str = Form(""),
) -> HTMLResponse:
    problem = password_problem(password, password2)
    if "@" not in email or len(email) > 255:
        problem = "Enter a valid email address."
    if problem:
        return HTMLResponse(signup_form(name, email, problem), status_code=400)
    db = SessionLocal()
    try:
        try:
            user = register_user(db, email, password, name[:120], invite_code or None)
        except RegistrationClosed:
            return HTMLResponse(signup_form(name, email, "That invite code isn't valid."), status_code=403)
        except EmailTaken:
            return HTMLResponse(
                signup_form(name, email, "An account with this email already exists. Sign in instead."), status_code=409
            )
        response = RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)
        start_session(db, user, request, response)
        db.commit()
        return response
    finally:
        db.close()


# --------------------------------------------------------------------- sign out


@router.post("/logout")
def logout(request: Request) -> RedirectResponse:
    db = SessionLocal()
    try:
        response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
        end_session(db, request, response)
        db.commit()
        return response
    finally:
        db.close()


# --------------------------------------------------------------------- security settings


def describe_agent(agent: str | None) -> str:
    agent = agent or ""
    browser = next((name for key, name in (("Edg", "Edge"), ("Chrome", "Chrome"), ("Firefox", "Firefox"), ("Safari", "Safari")) if key in agent), "Browser")
    system = next((name for key, name in (("Windows", "Windows"), ("Mac OS", "macOS"), ("Android", "Android"), ("iPhone", "iPhone"), ("Linux", "Linux")) if key in agent), "")
    return f"{browser} on {system}" if system else browser


@router.get("/settings/security", response_class=HTMLResponse)
def security_page(request: Request, message: str = Query(default=""), error: str = Query(default="")) -> str:
    db = SessionLocal()
    try:
        user = current_user(db)
        sessions = db.scalars(
            select(WebSession)
            .where(WebSession.user_id == user.id, WebSession.expires_at > datetime.now(UTC))
            .order_by(WebSession.last_seen_at.desc())
        ).all()
        this_session = request.state.session_token_hash
        rows = "".join(
            f"""<tr><td>{escape(describe_agent(row.user_agent))}{' <span class="badge badge-open">This device</span>' if row.token_hash == this_session else ""}</td>
              <td class="muted">{escape(row.ip_address or "")}</td>
              <td class="muted">{row.last_seen_at:%Y-%m-%d %H:%M} UTC</td></tr>"""
            for row in sessions
        )
        notice = (f'<p class="form-ok">{escape(message)}</p>' if message else "") + error_box(error)
        body = f"""
          {notice}
          <section class="grid two-col">
            <form class="card" method="post" action="/settings/security/password" style="display:grid;gap:14px">
              <div class="card-title"><h2>Change password</h2></div>
              <div class="field"><label for="current">Current password</label><input id="current" name="current" type="password" autocomplete="current-password" required /></div>
              <div class="field"><label for="new">New password</label><input id="new" name="new" type="password" autocomplete="new-password" minlength="{MIN_PASSWORD_LENGTH}" required /></div>
              <div class="field"><label for="new2">Repeat new password</label><input id="new2" name="new2" type="password" autocomplete="new-password" required /></div>
              <p class="hint">Changing your password signs you out on every other device.</p>
              <button class="btn btn-primary" type="submit">Change password</button>
            </form>
            <section class="card">
              <div class="card-title"><h2>Signed-in devices</h2>
                <form method="post" action="/settings/security/sign-out-others"><button class="btn btn-sm" type="submit">Sign out other devices</button></form></div>
              <div class="table-wrap"><table class="dense"><thead><tr><th>Device</th><th>IP</th><th>Last active</th></tr></thead><tbody>{rows}</tbody></table></div>
            </section>
          </section>"""
        return shell("Security", "settings", body, "Settings", user.name or user.email)
    finally:
        db.close()


def security_redirect(message: str = "", error: str = "") -> RedirectResponse:
    query = f"?message={quote(message)}" if message else f"?error={quote(error)}"
    return RedirectResponse(f"/settings/security{query}", status_code=status.HTTP_303_SEE_OTHER)


def sign_out_other_sessions(db, user_id: str, keep_token_hash: str | None) -> None:
    query = delete(WebSession).where(WebSession.user_id == user_id)
    if keep_token_hash:
        query = query.where(WebSession.token_hash != keep_token_hash)
    db.execute(query)


@router.post("/settings/security/password")
def change_password(request: Request, current: str = Form(""), new: str = Form(""), new2: str = Form("")) -> RedirectResponse:
    db = SessionLocal()
    try:
        user = current_user(db)
        if not user.hashed_password or not verify_password(current, user.hashed_password):
            return security_redirect(error="Your current password is incorrect.")
        problem = password_problem(new, new2)
        if problem:
            return security_redirect(error=problem)
        user.hashed_password = hash_password(new)
        sign_out_other_sessions(db, user.id, request.state.session_token_hash)
        db.commit()
        return security_redirect(message="Password changed. Other devices have been signed out.")
    finally:
        db.close()


@router.post("/settings/security/sign-out-others")
def sign_out_others(request: Request) -> RedirectResponse:
    db = SessionLocal()
    try:
        user = current_user(db)
        sign_out_other_sessions(db, user.id, request.state.session_token_hash)
        db.commit()
        return security_redirect(message="Other devices have been signed out.")
    finally:
        db.close()
