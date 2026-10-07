from __future__ import annotations

from datetime import UTC, datetime

from tradzlog_api.services.ai import build_coaching_payload, generate_coaching_insight
from tradzlog_api.services.analytics import (
    grouped_performance,
    rebuild_daily_stats,
    rebuild_equity_curve,
    summary,
)
from tradzlog_api.services.imports import parse_broker_csv
from tradzlog_api.services.reports import performance_report_payload, tax_report_csv
from tradzlog_db.models import Account, BrokerSync, BrokerSyncStatus, BrokerSyncType, User
from tradzlog_db.session import SessionLocal


def rebuild_account_analytics(account_id: str) -> dict[str, str]:
    session = SessionLocal()
    try:
        account = session.get(Account, account_id)
        if account is None:
            return {"status": "NOT_FOUND", "accountId": account_id}
        rebuild_daily_stats(session, account.id)
        rebuild_equity_curve(session, account)
        session.commit()
        return {"status": "READY", "accountId": account.id}
    finally:
        session.close()


def generate_performance_report(user_id: str) -> dict[str, object]:
    session = SessionLocal()
    try:
        return performance_report_payload(session, user_id)
    finally:
        session.close()


def generate_tax_csv_report(user_id: str) -> dict[str, object]:
    session = SessionLocal()
    try:
        csv_payload = tax_report_csv(session, user_id)
        return {"status": "READY", "userId": user_id, "bytes": len(csv_payload.encode("utf-8"))}
    finally:
        session.close()


def generate_ai_pattern_insight(user_id: str, account_id: str | None = None) -> dict[str, str]:
    session = SessionLocal()
    try:
        user = session.get(User, user_id)
        if user is None:
            return {"status": "NOT_FOUND", "userId": user_id}
        payload = build_coaching_payload(dict(summary(session, user.id, account_id)), grouped_performance(session, user.id, "setup"))
        insight = generate_coaching_insight(user.id, user.name or user.email, account_id, payload)
        session.add(insight)
        session.commit()
        session.refresh(insight)
        return {"status": "READY", "insightId": insight.id}
    finally:
        session.close()


def preview_import_csv(account_id: str, broker: str, raw_csv: str) -> dict[str, object]:
    session = SessionLocal()
    try:
        account = session.get(Account, account_id)
        if account is None:
            return {"status": "NOT_FOUND", "accountId": account_id}
        rows = parse_broker_csv(raw_csv.encode("utf-8"))
        sync = BrokerSync(
            account_id=account.id,
            broker=broker,
            sync_type=BrokerSyncType.CSV,
            last_sync_at=datetime.now(UTC),
            status=BrokerSyncStatus.SUCCESS,
        )
        session.add(sync)
        session.commit()
        return {"status": "PREVIEW_READY", "syncId": sync.id, "rows": len(rows)}
    finally:
        session.close()
