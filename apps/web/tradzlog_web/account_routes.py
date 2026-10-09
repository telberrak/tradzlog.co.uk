"""Sign-in, sign-up, sign-out and the security settings page."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe
from html import escape
from urllib.parse import quote

from fastapi import APIRouter, Form, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete, select

from tradzlog_api.config import settings
from tradzlog_api.security import hash_password, verify_password
from tradzlog_api.services import email as mail
from tradzlog_api.services.users import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    EmailTaken,
    RegistrationClosed,
    authenticate,
    normalise_email,
    register_user,
)
from tradzlog_db.models import AuthToken, AuthTokenPurpose, User, WebSession
from tradzlog_db.session import SessionLocal
from tradzlog_web.auth import (
    CURRENT_USER_ID,
    current_user,
    end_session,
    hash_token,
    safe_next,
    start_session,
)
from tradzlog_web.localtime import timezone_names, valid_timezone
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


def login_form(next_path: str, email: str = "", error: str = "", message: str = "") -> str:
    signup_hint = (
        '<p class="switch"><a href="/forgot-password">Forgot your password?</a><br />New here? <a href="/signup">Create an account</a></p>'
    )
    notice = f'<p class="form-ok">{escape(message)}</p>' if message else ""
    return auth_page(
        "Sign in",
        f"""{notice}{error_box(error)}
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
def login_page(next: str | None = Query(default=None), reset: str = Query(default="")) -> HTMLResponse:  # noqa: A002 - query name
    if CURRENT_USER_ID.get() is not None:
        return RedirectResponse(safe_next(next), status_code=status.HTTP_303_SEE_OTHER)
    message = "Password changed. Sign in with your new password." if reset else ""
    return HTMLResponse(login_form(safe_next(next), message=message))


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
          <input type="hidden" name="timezone" id="tz" value="UTC" />
          <button class="btn btn-primary" type="submit">Create account</button>
        </form>
        <script>try {{ document.getElementById("tz").value = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"; }} catch (e) {{}}</script>
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
    timezone: str = Form("UTC"),
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
        if valid_timezone(timezone):
            user.timezone = timezone
        response = RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)
        start_session(db, user, request, response)
        db.commit()
        mail.send(mail.welcome(user.email, user.name))
        return response
    finally:
        db.close()


# --------------------------------------------------------------------- forgotten password

RESET_LIFETIME = timedelta(minutes=60)


def forgot_form(email_value: str = "", sent: bool = False) -> str:
    if sent:
        body = """<p class="form-ok">If an account uses that address, we've emailed it a link to choose a new password.
          The link works once, for 60 minutes. Check your spam folder if it doesn't arrive.</p>
          <p class="switch"><a href="/login">Back to sign in</a></p>"""
    else:
        body = f"""<form method="post" action="/forgot-password">
          <p class="muted" style="margin:0">Enter your account's email and we'll send you a link to choose a new password.</p>
          <div class="field"><label for="email">Email</label>
            <input id="email" name="email" type="email" autocomplete="email" maxlength="255" value="{escape(email_value)}" required autofocus /></div>
          <button class="btn btn-primary" type="submit">Email me a link</button>
        </form>
        <p class="switch"><a href="/login">Back to sign in</a></p>"""
    return auth_page("Reset your password", body)


@router.get("/forgot-password", response_class=HTMLResponse)
def forgot_page() -> str:
    return forgot_form()


@router.post("/forgot-password", response_class=HTMLResponse)
def forgot_submit(email_address: str = Form("", alias="email")) -> str:
    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.email == normalise_email(email_address)))
        if user is not None and user.hashed_password:
            now = datetime.now(UTC)
            # Only the newest link works.
            db.execute(delete(AuthToken).where(AuthToken.user_id == user.id, AuthToken.purpose == AuthTokenPurpose.PASSWORD_RESET))
            raw = token_urlsafe(32)
            db.add(AuthToken(user_id=user.id, purpose=AuthTokenPurpose.PASSWORD_RESET, token_hash=hash_token(raw), expires_at=now + RESET_LIFETIME))
            db.commit()
            link = f"{settings.public_url}/reset-password?token={raw}"
            mail.send(mail.password_reset(user.email, user.name, link, int(RESET_LIFETIME.total_seconds() // 60)))
        # The same answer whether or not the account exists, so addresses can't be probed.
        return forgot_form(sent=True)
    finally:
        db.close()


def live_reset_token(db, raw: str) -> AuthToken | None:
    if not raw or len(raw) > 128:
        return None
    token = db.scalar(select(AuthToken).where(AuthToken.token_hash == hash_token(raw), AuthToken.purpose == AuthTokenPurpose.PASSWORD_RESET))
    if token is None or token.used_at is not None or token.expires_at <= datetime.now(UTC):
        return None
    return token


def reset_form(raw: str, error: str = "") -> str:
    return auth_page("Choose a new password", f"""{error_box(error)}
        <form method="post" action="/reset-password">
          <input type="hidden" name="token" value="{escape(raw)}" />
          <div class="field"><label for="new">New password</label>
            <input id="new" name="new" type="password" autocomplete="new-password" minlength="{MIN_PASSWORD_LENGTH}" maxlength="{MAX_PASSWORD_LENGTH}" required autofocus /></div>
          <div class="field"><label for="new2">Repeat new password</label>
            <input id="new2" name="new2" type="password" autocomplete="new-password" required /></div>
          <p class="hint">Setting a new password signs you out on every device.</p>
          <button class="btn btn-primary" type="submit">Set new password</button>
        </form>""")


def expired_link() -> str:
    return auth_page("Link expired", """<p class="form-error">This reset link has expired or was already used.</p>
        <p class="switch"><a href="/forgot-password">Send a new link</a></p>""")


@router.get("/reset-password", response_class=HTMLResponse)
def reset_page(token: str = Query(default="")) -> HTMLResponse:
    db = SessionLocal()
    try:
        if live_reset_token(db, token) is None:
            return HTMLResponse(expired_link(), status_code=400)
        return HTMLResponse(reset_form(token))
    finally:
        db.close()


@router.post("/reset-password", response_model=None)
def reset_submit(token: str = Form(""), new: str = Form(""), new2: str = Form("")) -> HTMLResponse | RedirectResponse:
    db = SessionLocal()
    try:
        found = live_reset_token(db, token)
        if found is None:
            return HTMLResponse(expired_link(), status_code=400)
        problem = password_problem(new, new2)
        if problem:
            return HTMLResponse(reset_form(token, problem), status_code=400)
        user = db.get(User, found.user_id)
        user.hashed_password = hash_password(new)
        found.used_at = datetime.now(UTC)
        sign_out_other_sessions(db, user.id, None)  # every device, including any an attacker had
        db.commit()
        mail.send(mail.password_changed(user.email, user.name))
        return RedirectResponse("/login?reset=1", status_code=status.HTTP_303_SEE_OTHER)
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
        mail.send(mail.password_changed(user.email, user.name))
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


# --------------------------------------------------------------------- profile


@router.get("/settings/profile", response_class=HTMLResponse)
def profile_page(message: str = Query(default=""), error: str = Query(default="")) -> str:
    db = SessionLocal()
    try:
        user = current_user(db)
        options = "".join(
            f'<option value="{escape(name)}" {"selected" if name == user.timezone else ""}>{escape(name.replace("_", " "))}</option>'
            for name in timezone_names()
        )
        notice = (f'<p class="form-ok">{escape(message)}</p>' if message else "") + error_box(error)
        body = f"""
          {notice}
          <form class="card" method="post" action="/settings/profile" style="display:grid;gap:14px;max-width:560px">
            <div class="card-title"><h2>Profile</h2></div>
            <div class="field"><label for="name">Name</label><input id="name" name="name" maxlength="120" value="{escape(user.name or "")}" /></div>
            <div class="field"><label>Email</label><input value="{escape(user.email)}" disabled /></div>
            <div class="field"><label for="timezone">Timezone</label><select id="timezone" name="timezone">{options}</select>
              <p class="hint" id="tz-hint">Dates, the P&amp;L calendar, date ranges and times you enter use this timezone.</p></div>
            <div class="actions"><button class="btn btn-primary" type="submit">Save profile</button>
              <button class="btn" type="button" id="tz-detect">Use this device's timezone</button></div>
          </form>
          <script>
            (function () {{
              var button = document.getElementById("tz-detect"), select = document.getElementById("timezone");
              var detected; try {{ detected = Intl.DateTimeFormat().resolvedOptions().timeZone; }} catch (e) {{}}
              if (!detected || !select.querySelector('option[value="' + detected + '"]')) {{ button.hidden = true; return; }}
              button.textContent = "Use this device's timezone (" + detected.replace(/_/g, " ") + ")";
              button.addEventListener("click", function () {{ select.value = detected; }});
            }})();
          </script>"""
        return shell("Profile", "settings", body, "Settings", user.name or user.email)
    finally:
        db.close()


@router.post("/settings/profile")
def save_profile(name: str = Form(""), timezone: str = Form("UTC")) -> RedirectResponse:
    db = SessionLocal()
    try:
        user = current_user(db)
        if not valid_timezone(timezone):
            return RedirectResponse(f"/settings/profile?error={quote('Choose a timezone from the list.')}", status_code=status.HTTP_303_SEE_OTHER)
        user.name = name.strip()[:120] or None
        user.timezone = timezone
        db.commit()
        return RedirectResponse(f"/settings/profile?message={quote('Profile saved.')}", status_code=status.HTTP_303_SEE_OTHER)
    finally:
        db.close()
