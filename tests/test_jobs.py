from tradzlog_api.services import enqueue
from tradzlog_api.services.jobs import EnqueuedJob, QUEUE_AI, QUEUE_ANALYTICS, QUEUE_IMPORTS, QUEUE_REPORTS


def test_enqueue_helpers_use_expected_queues(monkeypatch) -> None:
    calls: list[tuple[str, str, tuple[object, ...]]] = []

    def fake_enqueue(queue_name, func, *args, **kwargs):
        del kwargs
        calls.append((queue_name, func.__name__, args))
        return EnqueuedJob(queue=queue_name, job_id="job-1", function_name=func.__name__)

    monkeypatch.setattr(enqueue, "enqueue_job", fake_enqueue)

    assert enqueue.enqueue_analytics_rebuild("account").queue == QUEUE_ANALYTICS
    assert enqueue.enqueue_performance_report("user").queue == QUEUE_REPORTS
    assert enqueue.enqueue_tax_report("user").queue == QUEUE_REPORTS
    assert enqueue.enqueue_ai_pattern_insight("user").queue == QUEUE_AI
    assert enqueue.enqueue_import_preview("account", "Generic", "symbol,price").queue == QUEUE_IMPORTS

    assert calls == [
        (QUEUE_ANALYTICS, "rebuild_account_analytics", ("account",)),
        (QUEUE_REPORTS, "generate_performance_report", ("user",)),
        (QUEUE_REPORTS, "generate_tax_csv_report", ("user",)),
        (QUEUE_AI, "generate_ai_pattern_insight", ("user", None)),
        (QUEUE_IMPORTS, "preview_import_csv", ("account", "Generic", "symbol,price")),
    ]
