from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, DivisionByZero, InvalidOperation

from tradzlog_db.models import Direction, Execution, ExecutionType, Instrument, Trade

ZERO = Decimal("0")


@dataclass(frozen=True)
class ComputedTradeMetrics:
    average_entry: Decimal
    average_exit: Decimal | None
    total_quantity: Decimal
    gross_pnl: Decimal
    net_pnl: Decimal
    pnl_percent: Decimal | None
    r_multiple: Decimal | None
    holding_period_seconds: int | None


def weighted_average(executions: list[Execution]) -> Decimal | None:
    total_quantity = sum((execution.quantity for execution in executions), ZERO)
    if total_quantity == ZERO:
        return None
    return sum((execution.price * execution.quantity for execution in executions), ZERO) / total_quantity


def compute_planned_rr(
    direction: Direction,
    entry: Decimal | None,
    stop: Decimal | None,
    target: Decimal | None,
) -> Decimal | None:
    if entry is None or stop is None or target is None or entry == stop:
        return None
    reward = target - entry if direction == Direction.LONG else entry - target
    return reward / abs(entry - stop)


def compute_trade_metrics(
    trade: Trade,
    executions: list[Execution],
    instrument: Instrument,
) -> ComputedTradeMetrics:
    entries = [row for row in executions if row.type in {ExecutionType.ENTRY, ExecutionType.ADD}]
    exits = [row for row in executions if row.type in {ExecutionType.EXIT, ExecutionType.PARTIAL_EXIT}]
    average_entry = weighted_average(entries) or ZERO
    average_exit = weighted_average(exits)
    entry_quantity = sum((row.quantity for row in entries), ZERO)
    exit_quantity = sum((row.quantity for row in exits), ZERO)
    point_value = instrument.point_value or Decimal("1")
    fees = trade.commissions + sum((row.fees for row in executions), ZERO)

    if average_exit is None or exit_quantity == ZERO:
        gross_pnl = ZERO
    elif trade.direction == Direction.LONG:
        gross_pnl = (average_exit - average_entry) * exit_quantity * point_value
    else:
        gross_pnl = (average_entry - average_exit) * exit_quantity * point_value

    net_pnl = gross_pnl - fees
    basis = average_entry * entry_quantity * point_value
    pnl_percent = (net_pnl / basis * Decimal("100")) if basis else None

    risk_per_unit = abs(average_entry - trade.planned_stop) if trade.planned_stop is not None else ZERO
    try:
        r_multiple = net_pnl / (risk_per_unit * entry_quantity * point_value) if risk_per_unit and entry_quantity else None
    except (DivisionByZero, InvalidOperation):
        r_multiple = None

    holding_period_seconds = None
    if trade.closed_at is not None:
        holding_period_seconds = int((trade.closed_at - trade.opened_at).total_seconds())

    return ComputedTradeMetrics(
        average_entry=average_entry,
        average_exit=average_exit,
        total_quantity=entry_quantity,
        gross_pnl=gross_pnl,
        net_pnl=net_pnl,
        pnl_percent=pnl_percent,
        r_multiple=r_multiple,
        holding_period_seconds=holding_period_seconds,
    )
