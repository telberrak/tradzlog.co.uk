from datetime import UTC, datetime, timedelta
from decimal import Decimal

from tradzlog_api.services.metrics import compute_trade_metrics
from tradzlog_db.models import Direction, Execution, ExecutionType, Instrument, Trade, TradeStatus


def test_compute_long_trade_metrics() -> None:
    instrument = Instrument(
        symbol="ES",
        name="E-mini S&P",
        asset_class="FUTURES",
        point_value=Decimal("50"),
    )
    opened_at = datetime(2026, 1, 1, 14, 30, tzinfo=UTC)
    trade = Trade(
        account_id="account",
        user_id="user",
        instrument_id="instrument",
        direction=Direction.LONG,
        status=TradeStatus.CLOSED,
        opened_at=opened_at,
        closed_at=opened_at + timedelta(hours=1),
        planned_stop=Decimal("4990"),
        commissions=Decimal("0"),
    )
    executions = [
        Execution(
            type=ExecutionType.ENTRY,
            executed_at=opened_at,
            price=Decimal("5000"),
            quantity=Decimal("2"),
            fees=Decimal("2"),
        ),
        Execution(
            type=ExecutionType.EXIT,
            executed_at=opened_at + timedelta(hours=1),
            price=Decimal("5010"),
            quantity=Decimal("2"),
            fees=Decimal("2"),
        ),
    ]
    metrics = compute_trade_metrics(trade, executions, instrument)
    assert metrics.gross_pnl == Decimal("1000")
    assert metrics.net_pnl == Decimal("996")
    assert metrics.r_multiple == Decimal("0.996")
