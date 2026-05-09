from __future__ import annotations

import csv
from io import StringIO

from sqlalchemy import select
from sqlalchemy.orm import Session

from tradzlog_db.models import Instrument, Trade, TradeMetrics, TradeStatus


def tax_report_csv(session: Session, user_id: str) -> str:
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "symbol",
            "open_date",
            "close_date",
            "quantity",
            "avg_entry",
            "avg_exit",
            "gross_pnl",
            "fees",
            "net_pnl",
        ]
    )
    trades = session.scalars(
        select(Trade)
        .join(TradeMetrics, TradeMetrics.trade_id == Trade.id)
        .where(Trade.user_id == user_id, Trade.status == TradeStatus.CLOSED)
        .order_by(Trade.closed_at.asc())
    ).all()
    for trade in trades:
        if trade.metrics is None:
            continue
        instrument = session.get(Instrument, trade.instrument_id)
        net_pnl = trade.metrics.realized_pnl
        gross_pnl = net_pnl + trade.commissions
        writer.writerow(
            [
                instrument.symbol if instrument else trade.instrument_id,
                trade.opened_at.isoformat(),
                trade.closed_at.isoformat() if trade.closed_at else "",
                trade.metrics.total_quantity,
                trade.metrics.average_entry,
                trade.metrics.average_exit or "",
                gross_pnl,
                trade.commissions,
                net_pnl,
            ]
        )
    return output.getvalue()


def performance_report_payload(session: Session, user_id: str) -> dict[str, object]:
    trades = session.scalars(
        select(Trade)
        .join(TradeMetrics, TradeMetrics.trade_id == Trade.id)
        .where(Trade.user_id == user_id, Trade.status == TradeStatus.CLOSED)
        .order_by(TradeMetrics.realized_pnl.desc())
    ).all()
    top_wins = trades[:5]
    top_losses = list(reversed(trades[-5:]))
    return {
        "topWins": [trade.id for trade in top_wins],
        "topLosses": [trade.id for trade in top_losses],
        "status": "READY",
    }
