"""Turn imported fills into round-trip trades: preview (plan), confirm (apply) and undo.

Fills are grouped per account and instrument and walked in time order (FIFO position tracking):
a fill in the position's direction adds to it, an opposite fill reduces it, closing the trade when
the position reaches zero; a fill larger than the position closes it and opens the opposite trade
with the remainder. An import continues the account's oldest open trade in that instrument.

Each fill carries a fingerprint of its source row (scoped to the account), so re-importing the same
file, or an export that overlaps an earlier one, adds nothing.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from tradzlog_api.services.analytics import rebuild_daily_stats, rebuild_equity_curve
from tradzlog_api.services.imports import (
    EQUITY_OPTION_MULTIPLIER,
    FOREX_LOT,
    FUTURES_POINT_VALUES,
    ImportedExecution,
    futures_root,
    is_currency_pair,
    is_occ_option,
)
from tradzlog_api.services.metrics import compute_trade_metrics
from tradzlog_db.models import (
    Account,
    AccountSnapshot,
    AssetClass,
    BrokerSync,
    BrokerSyncStatus,
    DailyStats,
    Direction,
    Execution,
    ExecutionType,
    Instrument,
    Trade,
    TradeMetrics,
    TradeStatus,
)

ZERO = Decimal("0")
ENTRY_TYPES = {ExecutionType.ENTRY, ExecutionType.ADD}


# --------------------------------------------------------------------- serialising pending rows


def to_row(fill: ImportedExecution) -> dict[str, object]:
    return {
        "symbol": fill.symbol, "executed_at": fill.executed_at.isoformat(), "side": fill.side, "price": str(fill.price),
        "quantity": str(fill.quantity), "fees": str(fill.fees), "broker_id": fill.broker_id, "asset_class": fill.asset_class,
        "point_value": None if fill.point_value is None else str(fill.point_value), "currency": fill.currency,
        "broker_pnl": None if fill.broker_pnl is None else str(fill.broker_pnl), "row_number": fill.row_number,
    }


def from_row(row: dict[str, object]) -> ImportedExecution:
    def dec(name: str) -> Decimal | None:
        return None if row.get(name) is None else Decimal(str(row[name]))

    return ImportedExecution(
        symbol=str(row["symbol"]), executed_at=datetime.fromisoformat(str(row["executed_at"])), side=str(row["side"]),
        price=dec("price"), quantity=dec("quantity"), fees=dec("fees") or ZERO, broker_id=row.get("broker_id"),
        asset_class=row.get("asset_class"), point_value=dec("point_value"), currency=row.get("currency"),
        broker_pnl=dec("broker_pnl"), row_number=int(row.get("row_number") or 0),
    )


# --------------------------------------------------------------------- fingerprints


def fingerprints(account_id: str, fills: list[ImportedExecution]) -> list[str]:
    """Stable per source row; identical rows in one file are told apart by their occurrence."""
    seen: dict[str, int] = defaultdict(int)
    out = []
    for fill in fills:
        base = "|".join([account_id, fill.symbol, fill.executed_at.astimezone(UTC).isoformat(), fill.side,
                         format(fill.price.normalize(), "f"), format(fill.quantity.normalize(), "f"), fill.broker_id or ""])
        seen[base] += 1
        out.append(hashlib.sha256(f"{base}|{seen[base]}".encode()).hexdigest())
    return out


def existing_fingerprints(db: Session, account_id: str, candidates: list[str]) -> set[str]:
    found: set[str] = set()
    for start in range(0, len(candidates), 500):
        chunk = candidates[start : start + 500]
        found.update(
            db.scalars(
                select(Execution.fingerprint).join(Trade, Trade.id == Execution.trade_id)
                .where(Trade.account_id == account_id, Execution.fingerprint.in_(chunk))
            ).all()
        )
    return found


# --------------------------------------------------------------------- instruments


@dataclass
class NewInstrument:
    symbol: str
    asset_class: AssetClass
    point_value: Decimal
    currency: str
    needs_review: bool


def default_instrument(fill: ImportedExecution) -> NewInstrument:
    root = futures_root(fill.symbol)
    occ = is_occ_option(fill.symbol)
    if occ and fill.asset_class in {None, "STOCK", "OPTIONS"}:
        asset_class = AssetClass.OPTIONS
    elif fill.asset_class:
        asset_class = AssetClass(fill.asset_class)
    elif root:
        asset_class = AssetClass.FUTURES
    elif is_currency_pair(fill.symbol):
        asset_class = AssetClass.FOREX
    else:
        asset_class = AssetClass.STOCK
    point_value = fill.point_value or (FUTURES_POINT_VALUES.get(root) if root else None)
    if point_value is None and occ:
        point_value = EQUITY_OPTION_MULTIPLIER  # standard US equity/index option: 100 shares per contract
    if point_value is None and asset_class == AssetClass.FOREX:
        point_value = FOREX_LOT
    # Stocks and crypto move 1:1; anything else without a known multiplier must be checked.
    needs_review = point_value is None and asset_class not in {AssetClass.STOCK, AssetClass.CRYPTO}
    return NewInstrument(fill.symbol, asset_class, point_value or Decimal("1"), (fill.currency or "USD").upper()[:8], needs_review)


def find_instrument(db: Session, fill: ImportedExecution) -> Instrument | None:
    rows = db.scalars(select(Instrument).where(Instrument.symbol == fill.symbol)).all()
    if fill.asset_class:
        rows = [row for row in rows if row.asset_class.value == fill.asset_class] or rows
    return rows[0] if rows else None


# --------------------------------------------------------------------- position walk


@dataclass
class Leg:
    """One execution to write: a whole fill, or one side of a fill that flips the position."""

    fill: ImportedExecution
    fingerprint: str
    quantity: Decimal
    fees: Decimal
    type: ExecutionType


@dataclass
class PlannedTrade:
    direction: Direction
    legs: list[Leg] = field(default_factory=list)
    existing: Trade | None = None
    closed_at: datetime | None = None


def open_position(trade: Trade) -> Decimal:
    entered = sum((row.quantity for row in trade.executions if row.type in ENTRY_TYPES), ZERO)
    exited = sum((row.quantity for row in trade.executions if row.type not in ENTRY_TYPES), ZERO)
    return entered - exited


def walk(fills: list[tuple[ImportedExecution, str]], open_trade: Trade | None) -> list[PlannedTrade]:
    """Round trips for one instrument, starting from an existing open trade if there is one."""
    planned: list[PlannedTrade] = []
    current = PlannedTrade(open_trade.direction, existing=open_trade) if open_trade else None
    position = open_position(open_trade) if open_trade else ZERO
    if current:
        planned.append(current)
    for fill, fingerprint in sorted(fills, key=lambda item: (item[0].executed_at, item[0].row_number)):
        if fill.side == "CLOSE":
            # An expiration or assignment: closes the open position, whichever way it faces.
            if current is None or position == 0:
                continue  # nothing open here (opened before TradzLog's history): nothing to close
            buying = current.direction == Direction.SHORT
        else:
            buying = fill.side == "BUY"
        remaining, fees = fill.quantity, fill.fees
        while remaining > 0:
            if current is None or position == 0:
                current = PlannedTrade(Direction.LONG if buying else Direction.SHORT)
                planned.append(current)
                current.legs.append(Leg(fill, fingerprint, remaining, fees, ExecutionType.ENTRY))
                position, remaining = remaining, ZERO
            elif (current.direction == Direction.LONG) == buying:
                current.legs.append(Leg(fill, fingerprint, remaining, fees, ExecutionType.ADD))
                position, remaining = position + remaining, ZERO
            else:
                closing = min(remaining, position)
                leg_fees = fees if closing == remaining else (fees * closing / remaining).quantize(Decimal("0.0001"))
                position -= closing
                current.legs.append(Leg(fill, fingerprint, closing, leg_fees, ExecutionType.EXIT if position == 0 else ExecutionType.PARTIAL_EXIT))
                if position == 0:
                    current.closed_at = fill.executed_at
                remaining, fees = remaining - closing, fees - leg_fees
    return [trade for trade in planned if trade.legs]


# --------------------------------------------------------------------- plan (preview)


@dataclass
class ImportPlan:
    total_rows: int
    duplicates: int
    new_fills: list[tuple[ImportedExecution, str]]
    new_instruments: dict[str, NewInstrument]
    trades: list[PlannedTrade]
    broker_pnl: Decimal | None

    @property
    def trades_opened(self) -> int:
        return len([trade for trade in self.trades if trade.existing is None])

    @property
    def trades_closed(self) -> int:
        return len([trade for trade in self.trades if trade.closed_at is not None])

    @property
    def trades_continued(self) -> int:
        return len([trade for trade in self.trades if trade.existing is not None])


def plan_import(db: Session, account: Account, fills: list[ImportedExecution]) -> ImportPlan:
    prints = fingerprints(account.id, fills)
    already = existing_fingerprints(db, account.id, prints)
    new_fills = [(fill, fingerprint) for fill, fingerprint in zip(fills, prints, strict=True) if fingerprint not in already]

    new_instruments: dict[str, NewInstrument] = {}
    by_instrument: dict[str, list[tuple[ImportedExecution, str]]] = defaultdict(list)
    instrument_for: dict[str, Instrument | None] = {}
    for fill, fingerprint in new_fills:
        if fill.symbol not in instrument_for:
            instrument_for[fill.symbol] = find_instrument(db, fill)
            if instrument_for[fill.symbol] is None:
                new_instruments[fill.symbol] = default_instrument(fill)
        by_instrument[fill.symbol].append((fill, fingerprint))

    trades: list[PlannedTrade] = []
    for symbol, group in by_instrument.items():
        instrument = instrument_for[symbol]
        open_trade = None
        if instrument is not None:
            open_trade = db.scalar(
                select(Trade).options(selectinload(Trade.executions))
                .where(Trade.account_id == account.id, Trade.instrument_id == instrument.id, Trade.status == TradeStatus.OPEN)
                .order_by(Trade.opened_at).limit(1)
            )
        trades.extend(walk(group, open_trade))

    reported = [fill.broker_pnl for fill, _ in new_fills if fill.broker_pnl is not None]
    return ImportPlan(len(fills), len(fills) - len(new_fills), new_fills, new_instruments, trades, sum(reported, ZERO) if reported else None)


# --------------------------------------------------------------------- apply (confirm)


def refresh_trade(db: Session, trade: Trade) -> Decimal:
    """Status, close time and metrics from the trade's executions; returns its net P&L."""
    open_qty = open_position(trade)
    exits = [row for row in trade.executions if row.type not in ENTRY_TYPES]
    if open_qty <= 0 and exits:
        trade.status = TradeStatus.CLOSED
        trade.closed_at = max(row.executed_at for row in exits)
    else:
        trade.status = TradeStatus.OPEN
        trade.closed_at = None
    computed = compute_trade_metrics(trade, list(trade.executions), trade.instrument)
    metrics = trade.metrics or TradeMetrics(trade_id=trade.id, average_entry=computed.average_entry,
                                            total_quantity=computed.total_quantity, realized_pnl=computed.net_pnl)
    if trade.metrics is None:
        db.add(metrics)
        trade.metrics = metrics
    metrics.average_entry = computed.average_entry
    metrics.average_exit = computed.average_exit
    metrics.total_quantity = computed.total_quantity
    metrics.realized_pnl = computed.net_pnl
    metrics.pnl_percent = computed.pnl_percent
    metrics.r_multiple = computed.r_multiple
    metrics.holding_period_seconds = computed.holding_period_seconds
    return computed.net_pnl


def rebuild_account_stats(db: Session, account: Account) -> None:
    db.flush()
    db.execute(delete(AccountSnapshot).where(AccountSnapshot.account_id == account.id))
    db.execute(delete(DailyStats).where(DailyStats.account_id == account.id))
    db.flush()
    rebuild_daily_stats(db, account.id)
    rebuild_equity_curve(db, account)


def apply_import(
    db: Session, batch: BrokerSync, account: Account, fills: list[ImportedExecution], point_values: dict[str, Decimal] | None = None
) -> dict[str, object]:
    """Write the plan for ``fills`` (re-checked against the database now). Caller commits."""
    plan = plan_import(db, account, fills)
    instruments: dict[str, Instrument] = {}
    for symbol, spec in plan.new_instruments.items():
        chosen = (point_values or {}).get(symbol)
        instrument = Instrument(symbol=symbol, name=symbol, asset_class=spec.asset_class,
                                point_value=chosen if chosen and chosen > 0 else spec.point_value, currency=spec.currency)
        db.add(instrument)
        instruments[symbol] = instrument
    db.flush()

    touched: list[Trade] = []
    for planned in plan.trades:
        symbol = planned.legs[0].fill.symbol
        if planned.existing is not None:
            trade = planned.existing
        else:
            instrument = instruments.get(symbol) or find_instrument(db, planned.legs[0].fill)
            first = planned.legs[0].fill
            trade = Trade(account_id=account.id, user_id=account.user_id, instrument_id=instrument.id, instrument=instrument,
                          direction=planned.direction, status=TradeStatus.OPEN, opened_at=first.executed_at,
                          planned_entry=first.price, commissions=ZERO, mistake_flags=[], tags=[], is_reviewed=False,
                          import_batch_id=batch.id)
            db.add(trade)
        for leg in planned.legs:
            trade.executions.append(Execution(type=leg.type, executed_at=leg.fill.executed_at, price=leg.fill.price,
                                              quantity=leg.quantity, fees=leg.fees, broker_id=leg.fill.broker_id,
                                              import_batch_id=batch.id, fingerprint=leg.fingerprint))
        touched.append(trade)
    db.flush()
    pnl = sum((refresh_trade(db, trade) for trade in touched), ZERO)
    rebuild_account_stats(db, account)

    summary = {
        "rows": plan.total_rows, "duplicates": plan.duplicates, "fills_imported": len(plan.new_fills),
        "trades_opened": plan.trades_opened, "trades_continued": plan.trades_continued, "trades_closed": plan.trades_closed,
        "instruments_created": sorted(instruments), "instrument_ids_created": sorted(i.id for i in instruments.values()),
        "tradzlog_pnl": str(pnl),
        "broker_pnl": None if plan.broker_pnl is None else str(plan.broker_pnl),
    }
    batch.status = BrokerSyncStatus.SUCCESS
    batch.imported_at = datetime.now(UTC)
    batch.last_sync_at = batch.imported_at
    batch.summary = {**(batch.summary or {}), **summary}
    batch.payload = None
    return summary


# --------------------------------------------------------------------- undo


def latest_undoable(db: Session, account_id: str) -> BrokerSync | None:
    return db.scalar(
        select(BrokerSync).where(BrokerSync.account_id == account_id, BrokerSync.status == BrokerSyncStatus.SUCCESS,
                                 BrokerSync.imported_at.is_not(None), BrokerSync.undone_at.is_(None))
        .order_by(BrokerSync.imported_at.desc()).limit(1)
    )


class UndoNotAllowed(Exception):
    pass


def undo_import(db: Session, batch: BrokerSync, account: Account) -> dict[str, int]:
    """Remove everything ``batch`` added. Only the account's latest import can be undone, so a later
    import never ends up continuing a trade that no longer exists. Caller commits."""
    if batch.undone_at is not None or batch.imported_at is None:
        raise UndoNotAllowed("This import has nothing to undo.")
    latest = latest_undoable(db, account.id)
    if latest is None or latest.id != batch.id:
        raise UndoNotAllowed("Undo the newer import of this account first.")
    trade_ids = set(db.scalars(select(Execution.trade_id).where(Execution.import_batch_id == batch.id)).all())
    removed_fills = db.execute(delete(Execution).where(Execution.import_batch_id == batch.id)).rowcount or 0
    db.flush()
    deleted = updated = 0
    for trade in db.scalars(
        select(Trade).options(selectinload(Trade.executions), selectinload(Trade.metrics), selectinload(Trade.instrument))
        .where(Trade.id.in_(trade_ids))
    ).all():
        db.refresh(trade, ["executions"])
        if not trade.executions:
            db.delete(trade)
            deleted += 1
        else:
            refresh_trade(db, trade)
            updated += 1
    rebuild_account_stats(db, account)
    removed_instruments = remove_unused_instruments(db, batch)
    batch.undone_at = datetime.now(UTC)
    batch.summary = {**(batch.summary or {}), "undone_fills": removed_fills, "undone_trades_deleted": deleted,
                     "undone_trades_reopened": updated, "undone_instruments_removed": removed_instruments}
    return {"fills": removed_fills, "trades_deleted": deleted, "trades_updated": updated, "instruments_removed": len(removed_instruments)}


def remove_unused_instruments(db: Session, batch: BrokerSync) -> list[str]:
    """Delete instruments this import created that no trade uses any more (so a corrected
    re-import can create them again with the right settings)."""
    summary = batch.summary or {}
    query = select(Instrument)
    if summary.get("instrument_ids_created") is not None:
        query = query.where(Instrument.id.in_(summary["instrument_ids_created"]))
    elif summary.get("instruments_created") and batch.imported_at:
        # Older imports recorded only symbols: match instruments created while that import was confirmed.
        query = query.where(Instrument.symbol.in_(summary["instruments_created"]),
                            Instrument.created_at >= batch.created_at, Instrument.created_at <= batch.imported_at)
    else:
        return []
    removed = []
    db.flush()
    for instrument in db.scalars(query).all():
        if db.scalar(select(Trade.id).where(Trade.instrument_id == instrument.id).limit(1)) is None:
            removed.append(instrument.symbol)
            db.delete(instrument)
    return sorted(removed)
