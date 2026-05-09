from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

import tradzlog_web.main as web_main
from tradzlog_db.models import (
    AssetClass,
    BillingInvoice,
    BillingSubscription,
    Direction,
    Instrument,
    LeaderboardMetric,
    LeaderboardProfile,
    MentorAccess,
    MentorAccessStatus,
    MentorComment,
    Plan,
    PublicTradeShare,
    Trade,
    User,
)


class FakeWebSession:
    def __init__(self, scalar_results: list[object | None]) -> None:
        self.scalar_results = scalar_results
        self.added: list[object] = []
        self.commits = 0
        self.closed = False

    def scalar(self, statement: Any) -> object | None:
        del statement
        return self.scalar_results.pop(0) if self.scalar_results else None

    def add(self, instance: object) -> None:
        self.added.append(instance)

    def flush(self) -> None:
        for index, instance in enumerate(self.added, start=1):
            if isinstance(instance, BillingSubscription) and instance.id is None:
                instance.id = f"generated-{index}"

    def commit(self) -> None:
        self.commits += 1

    def close(self) -> None:
        self.closed = True


def added(session: FakeWebSession, model: type[object]) -> list[object]:
    return [instance for instance in session.added if isinstance(instance, model)]


def use_session(monkeypatch: pytest.MonkeyPatch, session: FakeWebSession) -> FakeWebSession:
    monkeypatch.setattr(web_main, "SessionLocal", lambda: session)
    return session


def user() -> User:
    return User(id="user-1", email="trader@example.com", hashed_password="hashed")


def test_sensitive_web_post_detection_covers_dynamic_and_community_paths() -> None:
    assert web_main.is_sensitive_web_post("/settings/billing/checkout")
    assert web_main.is_sensitive_web_post("/community/mentor/comments")
    assert web_main.is_sensitive_web_post("/positions/trade-1/close")
    assert web_main.is_sensitive_web_post("/trades/trade-1/share")
    assert not web_main.is_sensitive_web_post("/dashboard")


def test_validate_upload_rejects_unsafe_names_and_mime_types() -> None:
    with pytest.raises(HTTPException) as unsafe_name:
        web_main.validate_upload(
            SimpleNamespace(filename="..\\chart.png", content_type="image/png")
        )
    assert unsafe_name.value.status_code == 400

    with pytest.raises(HTTPException) as unsupported_type:
        web_main.validate_upload(
            SimpleNamespace(filename="chart.gif", content_type="image/gif")
        )
    assert unsupported_type.value.status_code == 400

    web_main.validate_upload(SimpleNamespace(filename="chart.webp", content_type="image/webp"))


def test_billing_checkout_updates_plan_and_records_paid_invoice(monkeypatch) -> None:
    active_user = user()
    session = use_session(monkeypatch, FakeWebSession([active_user]))

    response = web_main.billing_checkout(Plan.PRO)

    subscriptions = added(session, BillingSubscription)
    invoices = added(session, BillingInvoice)
    assert response.status_code == 303
    assert response.headers["location"] == "/settings/billing"
    assert active_user.plan == Plan.PRO
    assert len(subscriptions) == 1
    assert subscriptions[0].user_id == "user-1"
    assert subscriptions[0].plan == Plan.PRO
    assert len(invoices) == 1
    assert invoices[0].amount_paid == web_main.PLAN_DETAILS[Plan.PRO]["price"]
    assert invoices[0].subscription_id == subscriptions[0].id
    assert session.commits == 1
    assert session.closed


def test_leaderboard_profile_create_and_update_are_scoped_to_current_user(monkeypatch) -> None:
    active_user = user()
    session = use_session(monkeypatch, FakeWebSession([active_user, None]))

    response = web_main.save_leaderboard_profile(
        display_name="Consistent Trader",
        metric=LeaderboardMetric.WIN_RATE,
        is_public="1",
    )

    profiles = added(session, LeaderboardProfile)
    assert response.status_code == 303
    assert len(profiles) == 1
    assert profiles[0].user_id == "user-1"
    assert profiles[0].display_name == "Consistent Trader"
    assert profiles[0].metric == LeaderboardMetric.WIN_RATE
    assert profiles[0].is_public is True

    existing = profiles[0]
    update_session = use_session(monkeypatch, FakeWebSession([active_user, existing]))
    web_main.save_leaderboard_profile(
        display_name="Private Trader",
        metric=LeaderboardMetric.EXPECTANCY,
        is_public=None,
    )

    assert added(update_session, LeaderboardProfile) == []
    assert existing.display_name == "Private Trader"
    assert existing.metric == LeaderboardMetric.EXPECTANCY
    assert existing.is_public is False


def test_share_trade_hides_missing_or_cross_user_trade(monkeypatch) -> None:
    use_session(monkeypatch, FakeWebSession([user(), None]))

    with pytest.raises(HTTPException) as exc:
        web_main.share_trade("other-user-trade")

    assert exc.value.status_code == 404


def test_share_trade_creates_anonymized_public_share_for_owned_trade(monkeypatch) -> None:
    instrument = Instrument(symbol="ES", name="E-mini S&P", asset_class=AssetClass.FUTURES)
    trade = Trade(id="trade-1", user_id="user-1", direction=Direction.LONG)
    trade.instrument = instrument
    session = use_session(monkeypatch, FakeWebSession([user(), trade, None]))

    response = web_main.share_trade("trade-1")

    shares = added(session, PublicTradeShare)
    assert response.status_code == 303
    assert len(shares) == 1
    assert shares[0].user_id == "user-1"
    assert shares[0].trade_id == "trade-1"
    assert shares[0].anonymized is True
    assert shares[0].show_r_multiple_only is True
    assert response.headers["location"] == f"/share/{shares[0].slug}"


def test_grant_mentor_access_normalizes_email_and_sets_permissions(monkeypatch) -> None:
    session = use_session(monkeypatch, FakeWebSession([user()]))

    response = web_main.grant_mentor_access(
        mentor_email="Mentor@Example.com",
        mentor_name="Mentor",
        can_view_journals="1",
        can_comment=None,
    )

    accesses = added(session, MentorAccess)
    assert response.status_code == 303
    assert len(accesses) == 1
    assert accesses[0].student_user_id == "user-1"
    assert accesses[0].mentor_email == "mentor@example.com"
    assert accesses[0].status == MentorAccessStatus.ACTIVE
    assert accesses[0].can_view_journals is True
    assert accesses[0].can_comment is False


def test_create_mentor_comment_requires_owned_access_and_trade(monkeypatch) -> None:
    active_user = user()
    access = MentorAccess(
        id="access-1",
        student_user_id="user-1",
        mentor_email="mentor@example.com",
        status=MentorAccessStatus.ACTIVE,
        can_comment=True,
    )
    trade = Trade(id="trade-1", user_id="user-1")
    session = use_session(monkeypatch, FakeWebSession([active_user, access, trade]))

    response = web_main.create_mentor_comment(
        mentor_access_id="access-1",
        trade_id="trade-1",
        body="Review your exit discipline.",
    )

    comments = added(session, MentorComment)
    assert response.status_code == 303
    assert len(comments) == 1
    assert comments[0].mentor_access_id == "access-1"
    assert comments[0].student_user_id == "user-1"
    assert comments[0].trade_id == "trade-1"
    assert comments[0].body == "Review your exit discipline."

    use_session(monkeypatch, FakeWebSession([active_user, None, trade]))
    with pytest.raises(HTTPException) as exc:
        web_main.create_mentor_comment(
            mentor_access_id="other-access",
            trade_id="trade-1",
            body="Blocked",
        )
    assert exc.value.status_code == 404
