from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from tradzlog_web import book, localtime
from tradzlog_web.components import short_datetime
from tradzlog_web.context import CURRENT_TZ

LONDON = ZoneInfo("Europe/London")


@pytest.fixture
def in_london():
    token = CURRENT_TZ.set(LONDON)
    yield
    CURRENT_TZ.reset(token)


def trade_closed_at(when: datetime) -> SimpleNamespace:
    return SimpleNamespace(closed_at=when, opened_at=when, metrics=SimpleNamespace(realized_pnl=Decimal("100")))


def test_trades_count_on_the_users_local_day(in_london) -> None:
    late = datetime(2026, 6, 30, 23, 30, tzinfo=UTC)  # 00:30 on 1 July in London (BST)
    assert book.closed_day(trade_closed_at(late)) == date(2026, 7, 1)
    assert book.daily_pnl([trade_closed_at(late)]) == {date(2026, 7, 1): Decimal("100")}


def test_utc_users_keep_utc_days() -> None:
    late = datetime(2026, 6, 30, 23, 30, tzinfo=UTC)
    assert book.closed_day(trade_closed_at(late)) == date(2026, 6, 30)


def test_form_times_are_read_in_the_users_timezone(in_london) -> None:
    typed = datetime(2026, 7, 1, 10, 0)  # what a datetime-local field sends: no timezone
    assert localtime.from_local_input(typed) == datetime(2026, 7, 1, 9, 0, tzinfo=UTC)
    winter = datetime(2026, 1, 15, 10, 0)  # GMT, no daylight saving
    assert localtime.from_local_input(winter) == datetime(2026, 1, 15, 10, 0, tzinfo=UTC)


def test_times_are_shown_in_local_time(in_london) -> None:
    assert short_datetime(datetime(2026, 6, 30, 23, 30, tzinfo=UTC), date(2026, 10, 8)) == "Jul 1 00:30"
    assert localtime.fmt(datetime(2026, 6, 30, 23, 30, tzinfo=UTC), "%Y-%m-%d %H:%M %Z") == "2026-07-01 00:30 BST"


def test_unknown_timezones_fall_back_to_utc() -> None:
    assert localtime.zone("Not/AZone") is UTC
    assert localtime.zone("") is UTC
    assert localtime.valid_timezone("Europe/London") and localtime.valid_timezone("UTC")
    assert not localtime.valid_timezone("Not/AZone")
