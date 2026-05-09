from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from faker import Faker
from sqlalchemy import select

from tradzlog_api.security import hash_password
from tradzlog_api.services.analytics import rebuild_daily_stats, rebuild_equity_curve
from tradzlog_api.services.metrics import compute_planned_rr, compute_trade_metrics
from tradzlog_db.models import (
    Account,
    AccountType,
    AssetClass,
    Direction,
    Execution,
    ExecutionType,
    Instrument,
    JournalEntry,
    JournalType,
    MarketCondition,
    RuleAction,
    RuleType,
    Trade,
    TradeMetrics,
    TradeStatus,
    TradingRule,
    User,
)
from tradzlog_db.session import SessionLocal

fake = Faker()
SETUPS = ["ORB", "VWAP Reclaim", "Trend Pullback", "News Catalyst", "Earnings Play"]


def add_instruments(session) -> list[Instrument]:
    specs = [
        ("AAPL", "Apple", AssetClass.STOCK, "NASDAQ", "1"),
        ("MSFT", "Microsoft", AssetClass.STOCK, "NASDAQ", "1"),
        ("NVDA", "NVIDIA", AssetClass.STOCK, "NASDAQ", "1"),
        ("TSLA", "Tesla", AssetClass.STOCK, "NASDAQ", "1"),
        ("ES", "E-mini S&P 500", AssetClass.FUTURES, "CME", "50"),
        ("NQ", "E-mini Nasdaq 100", AssetClass.FUTURES, "CME", "20"),
        ("CL", "Crude Oil", AssetClass.FUTURES, "NYMEX", "1000"),
        ("EURUSD", "Euro / Dollar", AssetClass.FOREX, "FX", "100000"),
        ("BTCUSD", "Bitcoin", AssetClass.CRYPTO, "Crypto", "1"),
    ]
    instruments: list[Instrument] = []
    for symbol, name, asset_class, exchange, point_value in specs:
        instrument = session.scalar(
            select(Instrument).where(Instrument.symbol == symbol, Instrument.asset_class == asset_class)
        )
        if instrument is None:
            instrument = Instrument(
                symbol=symbol,
                name=name,
                asset_class=asset_class,
                exchange=exchange,
                point_value=Decimal(point_value),
                currency="USD",
            )
            session.add(instrument)
        instruments.append(instrument)
    session.flush()
    return instruments


def main() -> None:
    session = SessionLocal()
    try:
        user = session.scalar(select(User).where(User.email == "demo@tradzlog.com"))
        if user is None:
            user = User(
                email="demo@tradzlog.com",
                name="Demo Trader",
                timezone="America/New_York",
                hashed_password=hash_password("password"),
            )
            session.add(user)
            session.flush()

        instruments = add_instruments(session)
        stock_account = Account(
            user_id=user.id,
            name="Live Stock Account",
            broker="Interactive Brokers",
            currency="USD",
            starting_balance=Decimal("50000"),
            account_type=AccountType.LIVE,
        )
        futures_account = Account(
            user_id=user.id,
            name="Futures Prop Account",
            broker="NinjaTrader",
            currency="USD",
            starting_balance=Decimal("25000"),
            account_type=AccountType.PROP_FIRM,
            prop_firm_name="Apex",
            max_daily_loss=Decimal("1250"),
            max_total_loss=Decimal("2500"),
            daily_profit_target=Decimal("1500"),
        )
        session.add_all([stock_account, futures_account])
        session.flush()

        now = datetime.now(UTC)
        for offset in range(120, 0, -1):
            trade_day = now - timedelta(days=offset)
            if trade_day.weekday() >= 5:
                continue
            for _ in range(random.randint(1, 4)):
                instrument = random.choice(instruments)
                account = futures_account if instrument.asset_class == AssetClass.FUTURES else stock_account
                direction = random.choice([Direction.LONG, Direction.SHORT])
                base_price = Decimal(str(round(random.uniform(90, 550), 2)))
                if instrument.asset_class == AssetClass.FUTURES:
                    base_price = Decimal(str(round(random.uniform(3900, 5400), 2)))
                stop_distance = Decimal(str(round(random.uniform(0.5, 4.0), 2)))
                target_distance = stop_distance * Decimal(str(round(random.uniform(1.2, 3.0), 2)))
                planned_stop = base_price - stop_distance if direction == Direction.LONG else base_price + stop_distance
                planned_target = base_price + target_distance if direction == Direction.LONG else base_price - target_distance
                won = random.random() < 0.56
                exit_price = planned_target if won else planned_stop
                opened_at = trade_day.replace(hour=random.randint(9, 15), minute=random.randint(0, 59), second=0, microsecond=0)
                closed_at = opened_at + timedelta(minutes=random.randint(15, 240))
                quantity = Decimal(random.choice([1, 2, 3])) if instrument.asset_class == AssetClass.FUTURES else Decimal(random.choice([25, 50, 100]))
                trade = Trade(
                    account_id=account.id,
                    user_id=user.id,
                    instrument_id=instrument.id,
                    direction=direction,
                    status=TradeStatus.CLOSED,
                    opened_at=opened_at,
                    closed_at=closed_at,
                    setup_tag=random.choice(SETUPS),
                    timeframe=random.choice(["1m", "5m", "15m", "1h", "D"]),
                    planned_entry=base_price,
                    planned_stop=planned_stop,
                    planned_target=planned_target,
                    planned_rr=compute_planned_rr(direction, base_price, planned_stop, planned_target),
                    commissions=Decimal("1.50"),
                    notes=fake.sentence(),
                    emotional_rating=random.randint(2, 5),
                    mistake_flags=random.sample(["FOMO", "EARLY_EXIT", "LATE_ENTRY", "OVERSIZE", "NO_SETUP"], random.randint(0, 2)),
                    tags=random.sample(["London", "NY", "breakout", "pullback", "trend"], random.randint(1, 3)),
                    is_reviewed=random.random() < 0.7,
                )
                trade.executions = [
                    Execution(type=ExecutionType.ENTRY, executed_at=opened_at, price=base_price, quantity=quantity, fees=Decimal("0.75")),
                    Execution(type=ExecutionType.EXIT, executed_at=closed_at, price=exit_price, quantity=quantity, fees=Decimal("0.75")),
                ]
                session.add(trade)
                session.flush()
                computed = compute_trade_metrics(trade, trade.executions, instrument)
                session.add(
                    TradeMetrics(
                        trade_id=trade.id,
                        average_entry=computed.average_entry,
                        average_exit=computed.average_exit,
                        total_quantity=computed.total_quantity,
                        realized_pnl=computed.net_pnl,
                        pnl_percent=computed.pnl_percent,
                        r_multiple=computed.r_multiple,
                        holding_period_seconds=computed.holding_period_seconds,
                    )
                )

        for offset in range(30, 0, -1):
            review_date = (now - timedelta(days=offset)).date()
            session.add(
                JournalEntry(
                    user_id=user.id,
                    type=JournalType.DAILY,
                    date=review_date,
                    title=f"Daily review {review_date.isoformat()}",
                    content={"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": fake.paragraph()}]}]},
                    mood=random.randint(2, 5),
                    market_condition=random.choice(list(MarketCondition)),
                    key_lessons=[fake.sentence(), fake.sentence()],
                )
            )

        session.add_all(
            [
                TradingRule(user_id=user.id, name="Minimum 1.5R plan", description="Warn when planned R:R is below 1.5", type=RuleType.MIN_RR, value=Decimal("1.5"), action=RuleAction.WARN),
                TradingRule(user_id=user.id, account_id=futures_account.id, name="Prop daily loss", description="Lock after the prop daily loss limit", type=RuleType.MAX_DAILY_LOSS, value=Decimal("1250"), action=RuleAction.LOCK_TRADING),
                TradingRule(user_id=user.id, name="Max daily trades", description="Warn after five trades in a session", type=RuleType.MAX_DAILY_TRADES, value=Decimal("5"), action=RuleAction.WARN),
            ]
        )
        rebuild_daily_stats(session, stock_account.id)
        rebuild_daily_stats(session, futures_account.id)
        rebuild_equity_curve(session, stock_account)
        rebuild_equity_curve(session, futures_account)
        session.commit()
    finally:
        session.close()


if __name__ == "__main__":
    main()
