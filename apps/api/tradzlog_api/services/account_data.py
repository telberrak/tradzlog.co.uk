"""A user's right to their data (UK GDPR): export everything as a ZIP, or delete the account.

Export writes ``tradzlog-export.json`` (every row TradzLog holds about the user, grouped by table),
one CSV per table, and the screenshots themselves. Secrets are left out: password hashes, session
and sign-in token hashes.

Deletion removes the screenshots first, then the user row; every table holding the user's data
cascades from ``users`` in the database. Instruments that only this user's imports created are
removed from the shared catalogue as well.
"""

from __future__ import annotations

import csv
import enum
import io
import json
import logging
import zipfile
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import IO, Any

from sqlalchemy import delete, inspect, or_, select
from sqlalchemy.orm import Session

from tradzlog_api.services.storage import Storage
from tradzlog_db.models import (
    Account,
    AccountSnapshot,
    AIInsight,
    Attachment,
    BillingInvoice,
    BillingSubscription,
    BrokerSync,
    CashTransaction,
    DailyStats,
    Execution,
    Instrument,
    JournalEntry,
    LeaderboardProfile,
    MentorAccess,
    MentorComment,
    PublicTradeShare,
    RuleBreach,
    Trade,
    TradeMetrics,
    TradingRule,
    User,
    WebSession,
)

logger = logging.getLogger("tradzlog.account_data")

# Never exported: they are credentials, not information about the user.
SECRET_COLUMNS = {"hashed_password", "token_hash"}


def plain(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, enum.Enum):
        return value.value
    return value


def row_dict(row: Any) -> dict[str, Any]:
    return {column.key: plain(getattr(row, column.key)) for column in inspect(type(row)).columns if column.key not in SECRET_COLUMNS}


def user_rows(db: Session, user: User) -> dict[str, list[Any]]:
    """Every row that belongs to ``user``, by table name."""
    account_ids = select(Account.id).where(Account.user_id == user.id)
    trade_ids = select(Trade.id).where(Trade.user_id == user.id)
    journal_ids = select(JournalEntry.id).where(JournalEntry.user_id == user.id)
    by_user = (CashTransaction, TradingRule, RuleBreach, AIInsight, PublicTradeShare, LeaderboardProfile, BillingSubscription, BillingInvoice)
    rows: dict[str, list[Any]] = {
        "users": [user],
        "web_sessions": list(db.scalars(select(WebSession).where(WebSession.user_id == user.id))),
        "accounts": list(db.scalars(select(Account).where(Account.user_id == user.id).order_by(Account.created_at))),
        "trades": list(db.scalars(select(Trade).where(Trade.user_id == user.id).order_by(Trade.opened_at))),
        "executions": list(db.scalars(select(Execution).where(Execution.trade_id.in_(trade_ids)).order_by(Execution.executed_at))),
        "trade_metrics": list(db.scalars(select(TradeMetrics).where(TradeMetrics.trade_id.in_(trade_ids)))),
        "journal_entries": list(db.scalars(select(JournalEntry).where(JournalEntry.user_id == user.id).order_by(JournalEntry.created_at))),
        "attachments": list(db.scalars(select(Attachment).where(
            or_(Attachment.trade_id.in_(trade_ids), Attachment.journal_entry_id.in_(journal_ids))).order_by(Attachment.created_at))),
        "broker_imports": list(db.scalars(select(BrokerSync).where(BrokerSync.account_id.in_(account_ids)).order_by(BrokerSync.created_at))),
        "daily_stats": list(db.scalars(select(DailyStats).where(DailyStats.account_id.in_(account_ids)))),
        "account_snapshots": list(db.scalars(select(AccountSnapshot).where(AccountSnapshot.account_id.in_(account_ids)))),
        "mentor_access": list(db.scalars(select(MentorAccess).where(MentorAccess.student_user_id == user.id))),
        "mentor_comments": list(db.scalars(select(MentorComment).where(MentorComment.student_user_id == user.id))),
    }
    for model in by_user:
        rows[model.__tablename__] = list(db.scalars(select(model).where(model.user_id == user.id)))
    # Instruments are a shared catalogue; include the ones this user's trades use, for context.
    instrument_ids = {trade.instrument_id for trade in rows["trades"]}
    rows["instruments"] = list(db.scalars(select(Instrument).where(Instrument.id.in_(instrument_ids)))) if instrument_ids else []
    return rows


def write_export(db: Session, user: User, storage: Storage, out: IO[bytes]) -> dict[str, int]:
    """Write the user's export ZIP to ``out``; returns row counts per table plus ``screenshots``."""
    rows = user_rows(db, user)
    tables = {name: [row_dict(row) for row in items] for name, items in rows.items()}
    counts = {name: len(items) for name, items in tables.items()}
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        missing = []
        saved = 0
        for attachment in rows["attachments"]:
            data = storage.read(attachment.url)
            if data is None:
                missing.append(attachment.id)
                continue
            extension = attachment.file_name.rsplit(".", 1)[-1][:5] if "." in attachment.file_name else "img"
            archive.writestr(f"screenshots/{attachment.id}.{extension}", data)
            saved += 1
        counts["screenshots"] = saved
        document = {
            "exported_at": datetime.now(UTC).isoformat(),
            "format": "tradzlog-export/1",
            "notes": "Amounts are decimal strings and times are UTC (ISO 8601). Screenshots are in screenshots/<attachment id>.",
            "missing_screenshots": missing,
            "tables": tables,
        }
        archive.writestr("tradzlog-export.json", json.dumps(document, indent=2, default=str))
        for name, items in tables.items():
            if not items:
                continue
            text = io.StringIO()
            writer = csv.DictWriter(text, fieldnames=list(items[0]))
            writer.writeheader()
            for item in items:
                writer.writerow({key: json.dumps(value) if isinstance(value, dict | list) else value for key, value in item.items()})
            archive.writestr(f"csv/{name}.csv", text.getvalue())
        archive.writestr("README.txt", README)
    return counts


README = """TradzLog data export

tradzlog-export.json  everything TradzLog holds about you, by table
csv/                  the same data, one spreadsheet per table
screenshots/          your uploaded screenshots, named by attachment id (see the attachments table)

Password and session hashes are not included: they are credentials, not your data.
"""


def delete_account(db: Session, user: User, storage: Storage) -> dict[str, int]:
    """Delete the user's screenshots and every row about them, and commit."""
    user_id = user.id
    refs = list(db.scalars(select(Attachment.url).where(or_(
        Attachment.trade_id.in_(select(Trade.id).where(Trade.user_id == user_id)),
        Attachment.journal_entry_id.in_(select(JournalEntry.id).where(JournalEntry.user_id == user_id)),
    ))))
    for ref in refs:
        storage.delete(ref)
    swept = 0
    try:
        swept = storage.delete_owner(user_id)  # files never recorded in the database, e.g. an interrupted upload
    except Exception:
        logger.warning("could not sweep stored files for deleted user %s; delete attachments/%s/ by hand", user_id, user_id, exc_info=True)

    created: set[str] = set()
    for summary in db.scalars(select(BrokerSync.summary).where(BrokerSync.account_id.in_(select(Account.id).where(Account.user_id == user_id)))):
        created.update((summary or {}).get("instrument_ids_created") or [])

    db.execute(delete(User).where(User.id == user_id))  # the database cascades to every table above
    db.flush()
    removed = 0
    for instrument in db.scalars(select(Instrument).where(Instrument.id.in_(created))) if created else []:
        if db.scalar(select(Trade.id).where(Trade.instrument_id == instrument.id).limit(1)) is None:
            db.delete(instrument)
            removed += 1
    db.commit()
    return {"screenshots": len(refs), "stray_files": swept, "instruments": removed}
