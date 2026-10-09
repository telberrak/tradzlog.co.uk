from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import tradzlog_web.main as web_main
from tradzlog_db.models import Direction, TradeStatus
from tradzlog_web import book
from tradzlog_web.components import (
    edge_bars,
    filter_bar,
    money,
    month_shift,
    pnl_calendar,
    price_ladder,
    short_datetime,
)

TODAY = date(2026, 10, 7)


def make_trade(day: date, pnl: str, r: str | None = None, setup: str | None = "ORB", hold: int = 3600):
    closed_at = datetime(day.year, day.month, day.day, 15, tzinfo=UTC)
    return SimpleNamespace(
        status=TradeStatus.CLOSED,
        opened_at=closed_at - timedelta(seconds=hold),
        closed_at=closed_at,
        setup_tag=setup,
        metrics=SimpleNamespace(
            realized_pnl=Decimal(pnl),
            r_multiple=Decimal(r) if r is not None else None,
            holding_period_seconds=hold,
        ),
    )


def test_resolve_period_ranges_and_previous_window() -> None:
    month = book.resolve_period("1m", TODAY)
    assert (month.code, month.start, month.end) == ("1M", date(2026, 9, 8), TODAY)
    assert month.previous_start == date(2026, 8, 9)
    assert month.contains(date(2026, 9, 8)) and not month.contains(date(2026, 9, 7))
    assert month.contains_previous(date(2026, 9, 7)) and not month.contains_previous(date(2026, 9, 8))

    assert book.resolve_period("YTD", TODAY).start == date(2026, 1, 1)
    everything = book.resolve_period("ALL", TODAY)
    assert everything.start is None and everything.previous_start is None
    assert book.resolve_period("bogus", TODAY).code == book.DEFAULT_RANGE


def test_stats_win_rate_profit_factor_and_expectancy() -> None:
    trades = [make_trade(TODAY, "300", "2"), make_trade(TODAY, "-100", "-1"), make_trade(TODAY, "100", "1")]
    stats = book.stats(trades)
    assert (stats.trades, stats.wins, stats.losses) == (3, 2, 1)
    assert stats.net_pnl == Decimal("300")
    assert stats.profit_factor == Decimal("4")
    assert stats.expectancy_r == Decimal("2") / 3
    assert stats.average_hold_hours == Decimal("1")

    no_losses = book.stats([make_trade(TODAY, "50", "1")])
    assert no_losses.profit_factor is None
    assert book.stats([]).win_rate == 0


def test_equity_curve_tracks_balance_and_drawdown_from_peak() -> None:
    trades = [
        make_trade(date(2026, 10, 1), "1000"),
        make_trade(date(2026, 10, 2), "-2200"),
        make_trade(date(2026, 10, 2), "100"),
        make_trade(date(2026, 10, 5), "500"),
    ]
    curve = book.equity_curve(Decimal("10000"), trades)
    assert [point.balance for point in curve] == [Decimal("11000"), Decimal("8900"), Decimal("9400")]
    assert book.max_drawdown_pct(curve) == (Decimal("8900") - Decimal("11000")) / Decimal("11000") * 100
    # The curve's change always equals the net P&L of the same trades: KPI and chart can't disagree.
    assert curve[-1].balance - Decimal("10000") == book.stats(trades).net_pnl


def test_group_by_and_expectancy_ranking() -> None:
    trades = [make_trade(TODAY, "100", "1", "A"), make_trade(TODAY, "-50", "-0.5", "B"), make_trade(TODAY, "400", "2", None)]
    groups = book.by_expectancy(book.group_by(trades, lambda trade: trade.setup_tag or "Untagged"))
    assert [group.key for group in groups] == ["Untagged", "A", "B"]


def test_streaks_follow_close_order() -> None:
    trades = [
        make_trade(date(2026, 10, 3), "-1"),
        make_trade(date(2026, 10, 1), "1"),
        make_trade(date(2026, 10, 2), "1"),
        make_trade(date(2026, 10, 4), "-1"),
    ]
    assert book.streaks(trades) == {"longestWinStreak": 2, "longestLossStreak": 2, "currentStreak": 2, "currentKind": "loss"}


def test_filter_bar_keeps_state_in_urls_and_escapes_names() -> None:
    accounts = [SimpleNamespace(id="acc-1", name="<Prop> & Co")]
    html = filter_bar("/trades", accounts, "acc-1", book.resolve_period("1M", TODAY), {"status_filter": "OPEN"})
    assert "&lt;Prop&gt; &amp; Co" in html and "<Prop>" not in html
    assert 'href="/trades?account_id=acc-1&amp;range=YTD&amp;status_filter=OPEN"' in html
    assert 'class="active" href="/trades?account_id=acc-1&amp;range=1M' in html
    assert '<input type="hidden" name="status_filter" value="OPEN" />' in html


def test_pnl_calendar_colours_days_and_links_to_journal() -> None:
    daily = {date(2026, 10, 2): Decimal("1200"), date(2026, 10, 5): Decimal("-100"), date(2026, 9, 30): Decimal("5")}
    html = pnl_calendar(date(2026, 10, 1), daily, TODAY, "/dashboard?range=3M")
    assert 'class="day w2" href="/journal?day=2026-10-02"' in html
    assert 'class="day l1" href="/journal?day=2026-10-05"' in html
    assert "day=2026-09-30" not in html  # other months are padding, not data
    assert "month=2026-09" in html and "month=2026-11" in html
    assert "1/2 green days" in html
    assert month_shift(date(2026, 12, 1), 1) == date(2027, 1, 1)
    assert month_shift(date(2026, 1, 1), -1) == date(2025, 12, 1)


def test_edge_bars_handle_negative_expectancy() -> None:
    groups = book.group_by([make_trade(TODAY, "100", "1", "Win"), make_trade(TODAY, "-100", "-1", "Lose")], lambda t: t.setup_tag)
    html = edge_bars(book.by_expectancy(groups))
    assert html.index("Win") < html.index("Lose")
    assert 'class="neg"' in html and 'class="zero"' in html


def test_price_ladder_needs_two_levels() -> None:
    assert "<svg" not in price_ladder(Direction.LONG, Decimal("100"), None, None, None)
    html = price_ladder(Direction.SHORT, Decimal("100"), Decimal("102"), Decimal("95"), Decimal("97.5"))
    assert "▼ Short" in html and "97.50" in html and "102.00" in html


def test_formatting_helpers() -> None:
    assert money(Decimal("-1234.5")) == "-$1,234.50"
    assert money(Decimal("10"), signed=True) == "+$10.00"
    assert short_datetime(datetime(2026, 10, 6, 15, 14), TODAY) == "Oct 6 15:14"
    assert short_datetime(datetime(2025, 1, 2, 9, 5), TODAY, with_time=False) == "Jan 2 2025"


def test_log_trade_form_parsing() -> None:
    assert web_main.form_decimal("") is None
    assert web_main.form_decimal(" 1.25 ") == Decimal("1.25")
    assert web_main.form_datetime("2026-10-07T10:00") == datetime(2026, 10, 7, 10, tzinfo=UTC)
    with pytest.raises(HTTPException):
        web_main.form_decimal("abc")
    with pytest.raises(HTTPException):
        web_main.form_datetime("yesterday")


def test_log_trade_rejects_closed_trade_without_exit_and_bad_size() -> None:
    def submit(**overrides: object) -> None:
        fields = {
            "account_id": "a", "instrument_id": "i", "direction": Direction.LONG, "opened_at": "2026-10-07T10:00",
            "entry_price": "10", "quantity": "1", "trade_status": None, "setup_tag": "", "timeframe": "",
            "planned_stop": "", "planned_target": "", "entry_fees": "0", "closed_at": "", "exit_price": "",
            "exit_fees": "0", "notes": "",
        }
        web_main.create_trade_from_form(**{**fields, **overrides})

    # All of these are rejected before any database access.
    with pytest.raises(HTTPException, match="positive size"):
        submit(quantity="0")
    with pytest.raises(HTTPException, match="exit price"):
        submit(trade_status=TradeStatus.CLOSED)
    with pytest.raises(HTTPException, match="before opened"):
        submit(exit_price="11", closed_at="2026-10-06T10:00")


def test_custom_date_ranges() -> None:
    period = book.resolve_period("2026-01-01..2026-03-31", TODAY)
    assert (period.start, period.end, period.custom) == (date(2026, 1, 1), date(2026, 3, 31), True)
    assert period.previous_start == date(2025, 10, 3)  # the 90 days before
    assert period.contains(date(2026, 3, 31)) and not period.contains(date(2026, 4, 1))
    swapped = book.resolve_period("2026-03-31..2026-01-01", TODAY)
    assert swapped.code == "2026-01-01..2026-03-31"
    since = book.resolve_period("2026-09-01..", TODAY)
    assert (since.start, since.end) == (date(2026, 9, 1), TODAY)
    until = book.resolve_period("..2026-02-01", TODAY)
    assert (until.start, until.end, until.previous_start) == (None, date(2026, 2, 1), None)
    assert book.resolve_period("garbage..x", TODAY).code == book.DEFAULT_RANGE
    assert book.custom_code(None, None) is None
    assert book.custom_code(date(2026, 5, 2), date(2026, 5, 1)) == "2026-05-01..2026-05-02"


def test_filter_bar_shows_custom_dates() -> None:
    html = filter_bar("/trades", [], None, book.resolve_period("2026-01-01..2026-03-31", TODAY))
    assert 'name="from" value="2026-01-01"' in html and 'name="to" value="2026-03-31"' in html
    assert 'class="date-range active"' in html and 'class="active"' not in html.split("date-range")[0]
    preset = filter_bar("/trades", [], None, book.resolve_period("1M", TODAY))
    assert 'name="from" value=""' in preset


def test_deposits_and_withdrawals_move_balance_but_not_drawdown() -> None:
    trades = [make_trade(date(2026, 1, 2), "100"), make_trade(date(2026, 1, 5), "-50")]
    flows = {date(2026, 1, 3): Decimal("1000"), date(2026, 1, 4): Decimal("-500")}
    curve = book.equity_curve(Decimal("1000"), trades, flows)
    assert [(point.day.day, point.balance) for point in curve] == [(2, Decimal("1100")), (3, Decimal("2100")), (4, Decimal("1600")), (5, Decimal("1550"))]
    assert [point.drawdown_pct for point in curve[:3]] == [0, 0, 0]  # the withdrawal is not a drawdown
    assert curve[3].drawdown_pct == Decimal("-50") / Decimal("1600") * 100


def test_uk_tax_years_run_from_6_april() -> None:
    years = web_main.uk_tax_years(date(2026, 10, 9))
    assert years[0] == ("2026/27", "2026-04-06..2027-04-05") and years[1] == ("2025/26", "2025-04-06..2026-04-05")
    assert web_main.uk_tax_years(date(2026, 4, 5))[0] == ("2025/26", "2025-04-06..2026-04-05")
