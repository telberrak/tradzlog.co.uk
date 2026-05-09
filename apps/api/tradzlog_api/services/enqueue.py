from __future__ import annotations

from tradzlog_api.services import tasks
from tradzlog_api.services.jobs import (
    QUEUE_AI,
    QUEUE_ANALYTICS,
    QUEUE_IMPORTS,
    QUEUE_REPORTS,
    EnqueuedJob,
    enqueue_job,
)


def enqueue_analytics_rebuild(account_id: str) -> EnqueuedJob:
    return enqueue_job(QUEUE_ANALYTICS, tasks.rebuild_account_analytics, account_id)


def enqueue_performance_report(user_id: str) -> EnqueuedJob:
    return enqueue_job(QUEUE_REPORTS, tasks.generate_performance_report, user_id)


def enqueue_tax_report(user_id: str) -> EnqueuedJob:
    return enqueue_job(QUEUE_REPORTS, tasks.generate_tax_csv_report, user_id)


def enqueue_ai_pattern_insight(user_id: str, account_id: str | None = None) -> EnqueuedJob:
    return enqueue_job(QUEUE_AI, tasks.generate_ai_pattern_insight, user_id, account_id)


def enqueue_import_preview(account_id: str, broker: str, raw_csv: str) -> EnqueuedJob:
    return enqueue_job(QUEUE_IMPORTS, tasks.preview_import_csv, account_id, broker, raw_csv)
