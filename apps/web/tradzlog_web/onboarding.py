"""First-run guide on the dashboard: timezone → trading account → trades → first journal note.

Each step is done when the data exists, so the guide always matches reality. It disappears for good
(``User.onboarded_at``) once every step is done or the user dismisses it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape

from fastapi import APIRouter, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from tradzlog_db.models import Account, JournalEntry, Trade, User
from tradzlog_db.session import SessionLocal
from tradzlog_web.auth import current_user

router = APIRouter()


@dataclass(frozen=True)
class Step:
    title: str
    text: str
    links: tuple[tuple[str, str], ...]
    done: bool
    optional: bool = False


def steps(db: Session, user: User) -> list[Step]:
    has_account = db.scalar(select(Account.id).where(Account.user_id == user.id).limit(1)) is not None
    has_trade = db.scalar(select(Trade.id).where(Trade.user_id == user.id).limit(1)) is not None
    has_journal = db.scalar(select(JournalEntry.id).where(JournalEntry.user_id == user.id).limit(1)) is not None
    zone = (user.timezone or "UTC").replace("_", " ")
    trade_links = (("Import from your broker", "/settings/import"), ("Log a trade", "/trades/new")) if has_account else ()
    return [
        Step("Check your timezone", f"Trades and daily P&L use your local time. It is set to {zone}.",
             (("Change it", "/settings/profile"),), user.timezone not in {None, "", "UTC"}),
        Step("Add a trading account", "One per broker or prop-firm account, with its currency and starting balance.",
             (("Add an account", "/settings/accounts"),), has_account),
        Step("Bring in your trades", "Import a broker export (Interactive Brokers, tastytrade, NinjaTrader and more) or log one by hand."
             if has_account else "Add a trading account first, then import a broker export or log a trade.", trade_links, has_trade),
        Step("Write your first journal note", "A few lines on a trade or your day: what you saw, what you felt, what you'd repeat.",
             (("New journal entry", "/journal/new"),), has_journal, optional=True),
    ]


def checklist(db: Session, user: User) -> str:
    """The guide's HTML for the dashboard, or "" once onboarding is finished (recorded on the user)."""
    if user.onboarded_at is not None:
        return ""
    found = steps(db, user)
    if all(step.done for step in found):
        user.onboarded_at = datetime.now(UTC)
        db.commit()
        return ""
    required = [step for step in found if not step.optional]
    done = sum(step.done for step in required)
    items = []
    for number, step in enumerate(found, start=1):
        links = "" if step.done else "".join(
            f'<a class="btn btn-sm{" btn-primary" if index == 0 else ""}" href="{href}">{escape(label)}</a>'
            for index, (label, href) in enumerate(step.links)
        )
        mark = "✓" if step.done else str(number)
        extra = ' <span class="muted">(optional)</span>' if step.optional else ""
        items.append(f"""<li class="{"done" if step.done else ""}"><span class="step-mark" aria-hidden="true">{mark}</span>
          <div><h3>{escape(step.title)}{extra}</h3><p>{escape(step.text)}</p>{f'<div class="step-links">{links}</div>' if links else ""}</div></li>""")
    return f"""<section class="card onboarding page-block" aria-label="Getting started">
      <div class="card-title"><h2>Get started with TradzLog</h2>
        <form method="post" action="/onboarding/dismiss"><button class="btn btn-sm btn-ghost" type="submit">Hide this guide</button></form></div>
      <p class="muted" style="margin:0 0 12px">{done} of {len(required)} steps done</p>
      <div class="onboarding-progress" role="progressbar" aria-valuemin="0" aria-valuemax="{len(required)}" aria-valuenow="{done}"><span style="width:{done * 100 // len(required)}%"></span></div>
      <ol class="onboarding-steps">{"".join(items)}</ol>
    </section>"""


@router.post("/onboarding/dismiss")
def dismiss() -> RedirectResponse:
    db = SessionLocal()
    try:
        user = current_user(db)
        user.onboarded_at = user.onboarded_at or datetime.now(UTC)
        db.commit()
        return RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    finally:
        db.close()
