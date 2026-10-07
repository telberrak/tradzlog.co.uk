from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tradzlog_api.services.cache import analytics_cache, decimalize
from tradzlog_db.models import (
    Account,
    AccountSnapshot,
    DailyStats,
    Instrument,
    Trade,
    TradeMetrics,
    TradeStatus,
)

ZERO = Decimal("0")
ANALYTICS_TTL_SECONDS = 300


def _profit_factor(values: list[Decimal]) -> Decimal:
    winners = sum((value for value in values if value > 0), ZERO)
    losers = abs(sum((value for value in values if value < 0), ZERO))
    return winners / losers if losers else ZERO


def _win_rate(values: list[Decimal]) -> Decimal:
    if not values:
        return ZERO
    wins = len([value for value in values if value > 0])
    return Decimal(wins) / Decimal(len(values)) * Decimal("100")


def closed_trades_query(user_id: str, account_id: str | None = None):
    query = (
        select(Trade)
        .join(TradeMetrics, TradeMetrics.trade_id == Trade.id)
        .where(Trade.user_id == user_id, Trade.status == TradeStatus.CLOSED)
        .order_by(Trade.closed_at.asc())
    )
    if account_id:
        query = query.where(Trade.account_id == account_id)
    return query


def summary(session: Session, user_id: str, account_id: str | None = None) -> dict[str, Decimal | int]:
    cache_key = f"analytics:summary:{user_id}:{account_id or 'all'}"
    cached = analytics_cache.get(cache_key)
    if cached is not None:
        return decimalize(cached)
    rows = session.execute(
        select(
            func.count(Trade.id),
            func.coalesce(func.sum(TradeMetrics.realized_pnl), 0),
            func.coalesce(func.sum(Trade.commissions), 0),
            func.coalesce(func.avg(TradeMetrics.r_multiple), 0),
            func.coalesce(func.avg(TradeMetrics.holding_period_seconds), 0),
        )
        .join(TradeMetrics, TradeMetrics.trade_id == Trade.id)
        .where(Trade.user_id == user_id, Trade.status == TradeStatus.CLOSED)
        .where(Trade.account_id == account_id if account_id else True)
    ).one()
    trades = session.scalars(closed_trades_query(user_id, account_id)).all()
    pnls = [trade.metrics.realized_pnl for trade in trades if trade.metrics is not None]
    result = {
        "trades_count": int(rows[0] or 0),
        "net_pnl": Decimal(rows[1] or 0),
        "gross_pnl": Decimal(rows[1] or 0) + Decimal(rows[2] or 0),
        "win_rate": _win_rate(pnls),
        "profit_factor": _profit_factor(pnls),
        "average_r": Decimal(rows[3] or 0),
        "average_hold_seconds": Decimal(rows[4] or 0),
    }
    analytics_cache.set(cache_key, result, ANALYTICS_TTL_SECONDS)
    return result


def rebuild_daily_stats(session: Session, account_id: str) -> None:
    trades = session.scalars(
        select(Trade)
        .join(TradeMetrics, TradeMetrics.trade_id == Trade.id)
        .where(Trade.account_id == account_id, Trade.status == TradeStatus.CLOSED)
        .order_by(Trade.closed_at.asc())
    ).all()
    grouped: dict[date, list[Trade]] = defaultdict(list)
    for trade in trades:
        if trade.closed_at is not None:
            grouped[trade.closed_at.date()].append(trade)

    for stat_date, day_trades in grouped.items():
        pnls = [trade.metrics.realized_pnl for trade in day_trades if trade.metrics is not None]
        r_values = [trade.metrics.r_multiple or ZERO for trade in day_trades if trade.metrics is not None]
        wins = [value for value in pnls if value > 0]
        losses = [value for value in pnls if value < 0]
        stat = session.scalar(
            select(DailyStats).where(DailyStats.account_id == account_id, DailyStats.date == stat_date)
        )
        if stat is None:
            stat = DailyStats(account_id=account_id, date=stat_date)
            session.add(stat)
        stat.trades_count = len(day_trades)
        stat.wins = len(wins)
        stat.losses = len(losses)
        stat.breakeven = len([value for value in pnls if value == 0])
        stat.gross_pnl = sum(pnls, ZERO) + sum((trade.commissions for trade in day_trades), ZERO)
        stat.net_pnl = sum(pnls, ZERO)
        stat.commissions = sum((trade.commissions for trade in day_trades), ZERO)
        stat.win_rate = _win_rate(pnls)
        stat.avg_win = sum(wins, ZERO) / Decimal(len(wins)) if wins else ZERO
        stat.avg_loss = sum(losses, ZERO) / Decimal(len(losses)) if losses else ZERO
        stat.profit_factor = _profit_factor(pnls)
        stat.biggest_win = max(wins) if wins else ZERO
        stat.biggest_loss = min(losses) if losses else ZERO
        stat.r_multiple_sum = sum(r_values, ZERO)
    analytics_cache.delete_prefix("analytics:")


def rebuild_equity_curve(session: Session, account: Account) -> None:
    rows = session.scalars(
        select(DailyStats).where(DailyStats.account_id == account.id).order_by(DailyStats.date.asc())
    ).all()
    balance = account.starting_balance
    peak = balance
    for row in rows:
        balance += row.net_pnl
        peak = max(peak, balance)
        drawdown = balance - peak
        drawdown_pct = drawdown / peak * Decimal("100") if peak else ZERO
        snapshot = session.scalar(
            select(AccountSnapshot).where(AccountSnapshot.account_id == account.id, AccountSnapshot.date == row.date)
        )
        if snapshot is None:
            snapshot = AccountSnapshot(account_id=account.id, date=row.date, balance=balance, drawdown_from_peak=drawdown, drawdown_pct=drawdown_pct)
            session.add(snapshot)
        else:
            snapshot.balance = balance
            snapshot.drawdown_from_peak = drawdown
            snapshot.drawdown_pct = drawdown_pct


def grouped_performance(session: Session, user_id: str, dimension: str) -> list[dict[str, object]]:
    cache_key = f"analytics:grouped:{user_id}:{dimension}"
    cached = analytics_cache.get(cache_key)
    if cached is not None:
        return decimalize(cached)
    trades = session.scalars(closed_trades_query(user_id)).all()
    buckets: dict[str, list[Trade]] = defaultdict(list)
    for trade in trades:
        key = "Unassigned"
        if dimension == "setup":
            key = trade.setup_tag or "Unassigned"
        elif dimension == "instrument":
            instrument = session.get(Instrument, trade.instrument_id)
            key = instrument.symbol if instrument else trade.instrument_id
        elif dimension == "weekday":
            key = trade.opened_at.strftime("%A")
        elif dimension == "hour":
            key = f"{trade.opened_at.hour:02d}:00"
        buckets[key].append(trade)

    output: list[dict[str, object]] = []
    for key, bucket in buckets.items():
        pnls = [trade.metrics.realized_pnl for trade in bucket if trade.metrics is not None]
        r_values = [trade.metrics.r_multiple or ZERO for trade in bucket if trade.metrics is not None]
        output.append(
            {
                "key": key,
                "trades": len(bucket),
                "netPnl": sum(pnls, ZERO),
                "winRate": _win_rate(pnls),
                "profitFactor": _profit_factor(pnls),
                "averageR": sum(r_values, ZERO) / Decimal(len(r_values)) if r_values else ZERO,
            }
        )
    result = sorted(output, key=lambda row: row["netPnl"], reverse=True)
    analytics_cache.set(cache_key, result, ANALYTICS_TTL_SECONDS)
    return result


def streaks(session: Session, user_id: str) -> dict[str, int | str]:
    trades = session.scalars(closed_trades_query(user_id)).all()
    best_win = best_loss = current = 0
    current_kind = "none"
    for trade in trades:
        if trade.metrics is None or trade.metrics.realized_pnl == 0:
            current = 0
            current_kind = "breakeven"
            continue
        kind = "win" if trade.metrics.realized_pnl > 0 else "loss"
        current = current + 1 if kind == current_kind else 1
        current_kind = kind
        if kind == "win":
            best_win = max(best_win, current)
        else:
            best_loss = max(best_loss, current)
    return {"longestWinStreak": best_win, "longestLossStreak": best_loss, "currentStreak": current, "currentKind": current_kind}
