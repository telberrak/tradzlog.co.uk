"""Server-rendered dashboard components: filter bar, charts, calendar, trade tables, price ladder."""

from __future__ import annotations

import calendar
from collections.abc import Iterable, Sequence
from datetime import date, datetime
from decimal import Decimal
from html import escape
from urllib.parse import urlencode

from tradzlog_db.models import Account, Direction, Execution, ExecutionType, Trade
from tradzlog_web.book import RANGES, EquityPoint, Group, Period, pnl, r_multiple
from tradzlog_web.localtime import local
from tradzlog_web.ui import empty_state, side_badge, status_badge

ZERO = Decimal("0")


# ---------------------------------------------------------------- formatting


def money(value: Decimal | int | float | None, signed: bool = False) -> str:
    amount = Decimal(value or 0)
    sign = "-" if amount < 0 else ("+" if signed and amount > 0 else "")
    return f"{sign}${abs(amount):,.2f}"


def compact_money(value: Decimal | int | float | None) -> str:
    amount = Decimal(value or 0)
    sign = "-" if amount < 0 else "+"
    size = abs(amount)
    if size >= 1_000_000:
        return f"{sign}{size / 1_000_000:.1f}m"
    if size >= 1_000:
        return f"{sign}{size / 1_000:.1f}k"
    return f"{sign}{size:.0f}"


def number(value: Decimal | int | float | None, suffix: str = "") -> str:
    amount = Decimal(value or 0)
    return f"{amount:,.2f}{suffix}"


def price(value: Decimal | None) -> str:
    if value is None:
        return "—"
    text = f"{Decimal(value):,.5f}".rstrip("0")
    whole, _, frac = text.partition(".")
    return f"{whole}.{frac.ljust(2, '0')}"


def plain_number(value: Decimal | int | float | None) -> str:
    """100.0000 -> "100", 0.5000 -> "0.5", never scientific notation like 1E+2."""
    if value is None:
        return ""
    text = format(Decimal(value), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def tone(value: Decimal | int | float | None) -> str:
    amount = Decimal(value or 0)
    if amount > 0:
        return "positive"
    if amount < 0:
        return "negative"
    return "neutral"


def short_datetime(value: datetime | None, today: date, with_time: bool = True) -> str:
    if value is None:
        return "—"
    value = local(value)
    label = f"{value:%b} {value.day}"
    if value.year != today.year:
        label += value.strftime(" %Y")
    if with_time:
        label += value.strftime(" %H:%M")
    return label


def hold_time(seconds: int | None) -> str:
    if seconds is None:
        return "—"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 48:
        return f"{hours}h {minutes:02d}m"
    return f"{hours // 24}d {hours % 24}h"


# ---------------------------------------------------------------- filter bar


def query_string(params: dict[str, str | None]) -> str:
    clean = {key: value for key, value in params.items() if value}
    return f"?{urlencode(clean)}" if clean else ""


def filter_bar(
    base_path: str,
    accounts: Sequence[Account],
    account_id: str | None,
    period: Period,
    extra: dict[str, str | None] | None = None,
) -> str:
    """Account picker + date-range segmented control. State lives in the URL."""
    extra = extra or {}
    options = '<option value="">All accounts</option>' + "".join(
        f'<option value="{escape(account.id)}" {"selected" if account.id == account_id else ""}>'
        f"{escape(account.name)}</option>"
        for account in accounts
    )
    hidden = "".join(
        f'<input type="hidden" name="{escape(key)}" value="{escape(value)}" />'
        for key, value in {"range": period.code, **extra}.items()
        if value
    )
    ranges = "".join(
        f'<a class="{"active" if code == period.code else ""}" '
        f'href="{base_path}{escape(query_string({"account_id": account_id, "range": code, **extra}))}">{label}</a>'
        for code, label in RANGES.items()
    )
    return f"""<div class="filter-bar">
      <form method="get" action="{base_path}">
        {hidden}
        <select name="account_id" aria-label="Account" onchange="this.form.submit()">{options}</select>
        <noscript><button class="btn" type="submit">Apply</button></noscript>
      </form>
      <nav class="seg" aria-label="Date range">{ranges}</nav>
    </div>"""


# ---------------------------------------------------------------- KPI cards


def kpi(label: str, value: str, value_tone: str = "", note: str = "", note_tone: str = "") -> str:
    return (
        f'<article class="kpi-card"><div class="label">{escape(label)}</div>'
        f'<div class="kpi {value_tone}">{escape(value)}</div>'
        f'<div class="note {note_tone}">{escape(note)}</div></article>'
    )


# ---------------------------------------------------------------- equity curve


def equity_chart(points: list[EquityPoint], opening_balance: Decimal) -> str:
    if not points:
        return empty_state(
            "No closed trades in this range",
            "Your equity curve and drawdown appear here once trades in this period are closed.",
            "/trades/new",
            "Log trade",
            "◇",
        )
    width, top_h, dd_h, gap, pad_l, pad_b = 720, 200, 54, 14, 64, 20
    balances = [opening_balance] + [point.balance for point in points]
    low, high = min(balances), max(balances)
    span = (high - low) or Decimal("1")
    plot_w = width - pad_l
    steps = len(balances) - 1 or 1

    def x(index: int) -> float:
        return pad_l + float(index) / steps * plot_w

    def y(value: Decimal) -> float:
        return 8 + float((high - value) / span) * (top_h - 16)

    line = " ".join(f"{x(i):.1f},{y(value):.1f}" for i, value in enumerate(balances))
    area = f"{x(0):.1f},{top_h} {line} {x(len(balances) - 1):.1f},{top_h}"
    direction = "" if balances[-1] >= opening_balance else " down"

    worst = min((point.drawdown_pct for point in points), default=ZERO)
    dd_scale = abs(worst) or Decimal("1")
    dd_top = top_h + gap
    dd_poly = [f"{x(0):.1f},{dd_top}"]
    for i, point in enumerate(points, start=1):
        depth = float(abs(point.drawdown_pct) / dd_scale) * (dd_h - 6)
        dd_poly.append(f"{x(i):.1f},{dd_top + depth:.1f}")
    dd_poly.append(f"{x(len(balances) - 1):.1f},{dd_top}")

    height = dd_top + dd_h + pad_b
    first, last = points[0].day, points[-1].day
    return f"""<svg class="chart" viewBox="0 0 {width} {height}" role="img"
        aria-label="Equity from {money(opening_balance)} to {money(balances[-1])}, max drawdown {number(worst, '%')}">
      <line class="grid-line" x1="{pad_l}" y1="8" x2="{width}" y2="8"/>
      <line class="grid-line" x1="{pad_l}" y1="{top_h}" x2="{width}" y2="{top_h}"/>
      <line class="baseline" x1="{pad_l}" y1="{y(opening_balance):.1f}" x2="{width}" y2="{y(opening_balance):.1f}"/>
      <text x="0" y="14">{escape(compact_money(high).lstrip("+"))}</text>
      <text x="0" y="{top_h}">{escape(compact_money(low).lstrip("+"))}</text>
      <polygon class="equity-fill{direction}" points="{area}"/>
      <polyline class="equity{direction}" points="{line}"/>
      <line class="grid-line" x1="{pad_l}" y1="{dd_top}" x2="{width}" y2="{dd_top}"/>
      <text x="0" y="{dd_top + 12}">DD</text>
      <text x="0" y="{dd_top + dd_h}">{escape(number(worst, "%"))}</text>
      <polygon class="dd" points="{' '.join(dd_poly)}"/>
      <text x="{pad_l}" y="{height - 4}">{escape(first.strftime("%b %d"))}</text>
      <text x="{width}" y="{height - 4}" text-anchor="end">{escape(last.strftime("%b %d"))}</text>
    </svg>"""


# ---------------------------------------------------------------- P&L calendar


def month_shift(month: date, delta: int) -> date:
    index = month.year * 12 + month.month - 1 + delta
    return date(index // 12, index % 12 + 1, 1)


def pnl_calendar(month: date, daily: dict[date, Decimal], today: date, nav_href: str) -> str:
    """Month grid coloured by daily net P&L. ``nav_href`` gets ``&month=YYYY-MM`` appended."""
    month = month.replace(day=1)
    days = {day: value for day, value in daily.items() if (day.year, day.month) == (month.year, month.month)}
    biggest = max((abs(value) for value in days.values()), default=ZERO) or Decimal("1")
    joiner = "&" if "?" in nav_href else "?"
    cells = "".join(f'<div class="dow">{name}</div>' for name in ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"))
    for week in calendar.Calendar(firstweekday=0).monthdatescalendar(month.year, month.month):
        for day in week:
            if day.month != month.month:
                cells += '<div class="day pad"></div>'
                continue
            value = days.get(day)
            classes = ["day"]
            if day == today:
                classes.append("today")
            amount = ""
            if value is not None:
                strong = abs(value) >= biggest / 2
                classes.append(("w" if value >= 0 else "l") + ("2" if strong else "1"))
                amount = f'<span class="amt">{escape(compact_money(value))}</span>'
            label = f"{day.isoformat()}: {money(value, signed=True)}" if value is not None else day.isoformat()
            cells += (
                f'<a class="{" ".join(classes)}" href="/journal?day={day.isoformat()}" title="{escape(label)}">'
                f"<span>{day.day}</span>{amount}</a>"
            )
    best = max(days.items(), key=lambda item: item[1], default=None)
    worst = min(days.items(), key=lambda item: item[1], default=None)
    green = len([value for value in days.values() if value > 0])
    foot = (
        f"<span>{green}/{len(days)} green days</span>"
        f'<span>Best <b class="positive">{escape(compact_money(best[1]))}</b> · '
        f'Worst <b class="negative">{escape(compact_money(worst[1]))}</b></span>'
        if best and worst
        else "<span>No closed trades this month</span>"
    )
    prev_link = f"{nav_href}{joiner}month={month_shift(month, -1).strftime('%Y-%m')}"
    next_link = f"{nav_href}{joiner}month={month_shift(month, 1).strftime('%Y-%m')}"
    return f"""<div class="card-title">
        <h2>Daily P&amp;L</h2>
        <div class="seg"><a href="{escape(prev_link)}" aria-label="Previous month">‹</a>
          <a>{month.strftime("%b %Y")}</a><a href="{escape(next_link)}" aria-label="Next month">›</a></div>
      </div>
      <div class="cal">{cells}</div>
      <div class="cal-foot">{foot}</div>"""


# ---------------------------------------------------------------- edge bars


def edge_bars(groups: list[Group], limit: int = 8, min_trades: int = 1) -> str:
    rows = [group for group in groups if group.stats.trades >= min_trades][:limit]
    if not rows:
        return '<p class="muted">Tag your trades with a setup to see which ones pay.</p>'
    scale = max((abs(group.stats.expectancy_r or ZERO) for group in rows), default=ZERO) or Decimal("1")
    has_negative = any((group.stats.expectancy_r or ZERO) < 0 for group in rows)
    zero_at = 30 if has_negative else 0
    out = ""
    for group in rows:
        exp = group.stats.expectancy_r or ZERO
        size = float(abs(exp) / scale) * (100 - zero_at if exp >= 0 else zero_at)
        left = zero_at if exp >= 0 else zero_at - size
        out += f"""<div class="edge-row">
          <div class="top"><span>{escape(group.key)}</span>
            <span class="num"><b class="{tone(exp)}">{number(exp, "R")}</b> · {number(group.stats.win_rate, "%")} win ·
            {group.stats.trades} trades · <span class="{tone(group.stats.net_pnl)}">{escape(compact_money(group.stats.net_pnl))}</span></span></div>
          <div class="track">{f'<i class="zero" style="left:{zero_at}%"></i>' if has_negative else ""}
            <span class="{"neg" if exp < 0 else ""}" style="left:{left:.1f}%;width:{size:.1f}%"></span></div>
        </div>"""
    return f'<div class="edge">{out}</div>'


# ---------------------------------------------------------------- trade table


def trade_table(trades: Iterable[Trade], today: date, show_account: bool = True, empty: str = "") -> str:
    rows = ""
    for trade in trades:
        value = pnl(trade)
        r_value = r_multiple(trade)
        closed = trade.metrics is not None and trade.metrics.average_exit is not None
        account_cell = f'<td class="wrap muted">{escape(trade.account.name)}</td>' if show_account else ""
        rows += f"""<tr data-href="/trades/{escape(trade.id)}">
          <td class="muted">{escape(short_datetime(trade.opened_at, today))}</td>
          <td class="sym"><a href="/trades/{escape(trade.id)}">{escape(trade.instrument.symbol)}</a></td>
          <td>{side_badge(trade.direction.value)}</td>
          <td class="wrap">{escape(trade.setup_tag or "—")}</td>
          {account_cell}
          <td class="num">{price(trade.metrics.average_entry if trade.metrics else trade.planned_entry)}</td>
          <td class="num">{price(trade.metrics.average_exit if trade.metrics else None)}</td>
          <td class="num {tone(value) if closed else "muted"}">{money(value, signed=True) if closed else "open"}</td>
          <td class="num {tone(r_value) if closed else "muted"}">{number(r_value, "R") if closed and r_value is not None else "—"}</td>
          <td>{status_badge(trade.status.value)}</td>
        </tr>"""
    if not rows:
        return empty or empty_state("No trades here yet", "Trades you log in this range show up here.", "/trades/new", "Log trade", "◇")
    account_head = "<th>Account</th>" if show_account else ""
    return f"""<div class="table-wrap"><table class="dense">
      <thead><tr><th>Opened</th><th>Symbol</th><th>Side</th><th>Setup</th>{account_head}
      <th class="num">Entry</th><th class="num">Exit</th><th class="num">Net P&amp;L</th><th class="num">R</th><th>Status</th></tr></thead>
      <tbody>{rows}</tbody></table></div>"""


# ---------------------------------------------------------------- trade detail


def price_ladder(
    direction: Direction,
    entry: Decimal | None,
    stop: Decimal | None,
    target: Decimal | None,
    exit_price: Decimal | None,
) -> str:
    levels = [
        ("Target", target, "var(--success)"),
        ("Entry", entry, "var(--text-secondary)"),
        ("Stop", stop, "var(--danger)"),
        ("Exit", exit_price, "var(--accent)"),
    ]
    known = [(label, value, color) for label, value, color in levels if value is not None]
    if len(known) < 2:
        return '<p class="muted">Add a stop and target to see the trade plan laid out on a price ladder.</p>'
    values = [value for _, value, _ in known]
    low, high = min(values), max(values)
    span = (high - low) or Decimal("1")
    width, height, top, bottom, bar_x, bar_w = 420, 240, 18, 18, 150, 70

    def y(value: Decimal) -> float:
        return top + float((high - value) / span) * (height - top - bottom)

    shapes = ""
    if entry is not None and stop is not None:
        y1, y2 = sorted((y(entry), y(stop)))
        shapes += f'<rect x="{bar_x}" y="{y1:.1f}" width="{bar_w}" height="{max(y2 - y1, 1):.1f}" fill="var(--danger-soft)"/>'
    if entry is not None and target is not None:
        y1, y2 = sorted((y(entry), y(target)))
        shapes += f'<rect x="{bar_x}" y="{y1:.1f}" width="{bar_w}" height="{max(y2 - y1, 1):.1f}" fill="var(--success-soft)"/>'
    lines = ""
    used: list[float] = []
    for label, value, color in known:
        ly = y(value)
        text_y = ly + 4
        while any(abs(text_y - other) < 14 for other in used):
            text_y += 14
        used.append(text_y)
        dash = ' stroke-dasharray="5 4"' if label == "Exit" else ""
        lines += (
            f'<line x1="{bar_x - 20}" y1="{ly:.1f}" x2="{bar_x + bar_w + 20}" y2="{ly:.1f}" stroke="{color}" stroke-width="2"{dash}/>'
            f'<text class="lbl" x="{bar_x - 28}" y="{text_y:.1f}" text-anchor="end">{label}</text>'
            f'<text x="{bar_x + bar_w + 28}" y="{text_y:.1f}" fill="{color}">{escape(price(value))}</text>'
        )
    arrow = "▲ Long" if direction == Direction.LONG else "▼ Short"
    return f"""<svg class="ladder" viewBox="0 0 {width} {height}" width="100%" role="img" aria-label="Price ladder for this trade">
      {shapes}{lines}
      <text class="lbl" x="{width - 4}" y="12" text-anchor="end">{arrow}</text>
    </svg>"""


def execution_timeline(executions: Iterable[Execution], today: date) -> str:
    items = ""
    for execution in executions:
        is_exit = execution.type in {ExecutionType.EXIT, ExecutionType.PARTIAL_EXIT}
        items += f"""<li><span class="dot {"exit" if is_exit else ""}"></span>
          <div class="t">{escape(short_datetime(execution.executed_at, today))}</div>
          <div class="d">{escape(execution.type.value.replace("_", " ").title())} {number(execution.quantity).rstrip("0").rstrip(".")} @ {price(execution.price)}</div>
          <div class="t">Fees {money(execution.fees)}</div></li>"""
    return f'<ul class="timeline">{items}</ul>' if items else '<p class="muted">No executions recorded.</p>'
