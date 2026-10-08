"""Pure trade-book calculations behind the web UI's filtered views.

Everything here works on already-loaded ``Trade`` rows (with ``metrics`` populated), so every
panel on a page is computed from the same filtered set of trades and can never disagree.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from tradzlog_db.models import Trade
from tradzlog_web.localtime import local_date

ZERO = Decimal("0")

RANGES: dict[str, str] = {"1W": "1W", "1M": "1M", "3M": "3M", "YTD": "YTD", "ALL": "All"}
DEFAULT_RANGE = "3M"


@dataclass(frozen=True)
class Period:
    code: str
    start: date | None
    end: date
    previous_start: date | None

    def contains(self, day: date) -> bool:
        return (self.start is None or day >= self.start) and day <= self.end

    def contains_previous(self, day: date) -> bool:
        return self.previous_start is not None and self.start is not None and self.previous_start <= day < self.start

    @property
    def custom(self) -> bool:
        return CUSTOM_SEPARATOR in self.code


# A custom range travels in the same ``range`` parameter as the presets, e.g. "2026-01-01..2026-03-31"
# (either side may be empty), so every link that carries the range keeps it.
CUSTOM_SEPARATOR = ".."


def custom_code(start: date | None, end: date | None) -> str | None:
    """The ``range`` value for a from/to pair (swapped if reversed), or None if both are empty."""
    if start is None and end is None:
        return None
    if start is not None and end is not None and start > end:
        start, end = end, start
    return f"{start.isoformat() if start else ''}{CUSTOM_SEPARATOR}{end.isoformat() if end else ''}"


def parse_day(value: str) -> date | None:
    try:
        return date.fromisoformat(value.strip()) if value.strip() else None
    except ValueError:
        return None


def resolve_period(code: str | None, today: date) -> Period:
    if code and CUSTOM_SEPARATOR in code:
        first, _, last = code.partition(CUSTOM_SEPARATOR)
        start, end = parse_day(first), parse_day(last)
        if start is not None or end is not None:
            canonical = custom_code(start, end)
            start, end = (parse_day(part) for part in canonical.split(CUSTOM_SEPARATOR))
            end = end or max(today, start)
            previous_start = start - (end - start + timedelta(days=1)) if start else None
            return Period(canonical, start, end, previous_start)
    code = (code or DEFAULT_RANGE).upper()
    if code not in RANGES:
        code = DEFAULT_RANGE
    if code == "ALL":
        return Period(code, None, today, None)
    if code == "YTD":
        start = date(today.year, 1, 1)
    else:
        start = today - timedelta(days={"1W": 7, "1M": 30, "3M": 90}[code] - 1)
    length = today - start + timedelta(days=1)
    return Period(code, start, today, start - length)


def pnl(trade: Trade) -> Decimal:
    return trade.metrics.realized_pnl if trade.metrics is not None else ZERO


def r_multiple(trade: Trade) -> Decimal | None:
    return trade.metrics.r_multiple if trade.metrics is not None else None


def closed_day(trade: Trade) -> date | None:
    return local_date(trade.closed_at) if trade.closed_at is not None else None


def opened_day(trade: Trade) -> date:
    return local_date(trade.opened_at)


@dataclass(frozen=True)
class Stats:
    trades: int
    wins: int
    losses: int
    net_pnl: Decimal
    gross_win: Decimal
    gross_loss: Decimal
    win_rate: Decimal
    profit_factor: Decimal | None
    expectancy_r: Decimal | None
    average_hold_hours: Decimal | None
    best_trade: Decimal
    worst_trade: Decimal


def stats(trades: Iterable[Trade]) -> Stats:
    rows = list(trades)
    values = [pnl(trade) for trade in rows]
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value < 0]
    gross_win = sum(wins, ZERO)
    gross_loss = abs(sum(losses, ZERO))
    r_values = [value for value in (r_multiple(trade) for trade in rows) if value is not None]
    holds = [
        trade.metrics.holding_period_seconds
        for trade in rows
        if trade.metrics is not None and trade.metrics.holding_period_seconds is not None
    ]
    return Stats(
        trades=len(rows),
        wins=len(wins),
        losses=len(losses),
        net_pnl=sum(values, ZERO),
        gross_win=gross_win,
        gross_loss=gross_loss,
        win_rate=Decimal(len(wins)) / Decimal(len(rows)) * 100 if rows else ZERO,
        profit_factor=gross_win / gross_loss if gross_loss else None,
        expectancy_r=sum(r_values, ZERO) / Decimal(len(r_values)) if r_values else None,
        average_hold_hours=Decimal(sum(holds)) / Decimal(len(holds)) / 3600 if holds else None,
        best_trade=max(values, default=ZERO),
        worst_trade=min(values, default=ZERO),
    )


def daily_pnl(trades: Iterable[Trade]) -> dict[date, Decimal]:
    days: dict[date, Decimal] = defaultdict(lambda: ZERO)
    for trade in trades:
        day = closed_day(trade)
        if day is not None:
            days[day] += pnl(trade)
    return dict(sorted(days.items()))


@dataclass(frozen=True)
class EquityPoint:
    day: date
    balance: Decimal
    drawdown_pct: Decimal


def equity_curve(opening_balance: Decimal, trades: Iterable[Trade], cash_flows: dict[date, Decimal] | None = None) -> list[EquityPoint]:
    """Daily closing balance and drawdown-from-peak, starting from ``opening_balance``.

    Deposits and withdrawals (``cash_flows``, signed, by day) move the balance and the peak together,
    so they never show up as gains or drawdowns.
    """
    balance = opening_balance
    peak = opening_balance
    points: list[EquityPoint] = []
    pnl_by_day = daily_pnl(trades)
    flows = cash_flows or {}
    for day in sorted(set(pnl_by_day) | set(flows)):
        flow = flows.get(day, ZERO)
        balance += pnl_by_day.get(day, ZERO) + flow
        peak = max(peak + flow, balance)
        drawdown = (balance - peak) / peak * 100 if peak > 0 else ZERO
        points.append(EquityPoint(day, balance, drawdown))
    return points


def max_drawdown_pct(points: list[EquityPoint]) -> Decimal:
    return min((point.drawdown_pct for point in points), default=ZERO)


@dataclass(frozen=True)
class Group:
    key: str
    stats: Stats


def group_by(trades: Iterable[Trade], key: Callable[[Trade], str]) -> list[Group]:
    buckets: dict[str, list[Trade]] = defaultdict(list)
    for trade in trades:
        buckets[key(trade)].append(trade)
    return [Group(name, stats(rows)) for name, rows in buckets.items()]


def by_expectancy(groups: list[Group]) -> list[Group]:
    return sorted(groups, key=lambda group: (group.stats.expectancy_r or ZERO, group.stats.net_pnl), reverse=True)


def streaks(trades: Iterable[Trade]) -> dict[str, int | str]:
    ordered = sorted((trade for trade in trades if trade.closed_at is not None), key=lambda trade: trade.closed_at)
    best_win = best_loss = current = 0
    current_kind = "none"
    for trade in ordered:
        value = pnl(trade)
        if value == 0:
            current, current_kind = 0, "breakeven"
            continue
        kind = "win" if value > 0 else "loss"
        current = current + 1 if kind == current_kind else 1
        current_kind = kind
        if kind == "win":
            best_win = max(best_win, current)
        else:
            best_loss = max(best_loss, current)
    return {"longestWinStreak": best_win, "longestLossStreak": best_loss, "currentStreak": current, "currentKind": current_kind}


def percent_change(current: Decimal, previous: Decimal) -> Decimal | None:
    if previous == 0:
        return None
    return (current - previous) / abs(previous) * 100
