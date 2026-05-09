from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tradzlog_db.models import DailyStats, RuleBreach, RuleType, Trade, TradeMetrics, TradingRule


def evaluate_rules(session: Session, trade: Trade) -> list[RuleBreach]:
    rules = session.scalars(
        select(TradingRule).where(
            TradingRule.user_id == trade.user_id,
            TradingRule.is_active.is_(True),
            (TradingRule.account_id.is_(None)) | (TradingRule.account_id == trade.account_id),
        )
    ).all()
    breaches: list[RuleBreach] = []
    for rule in rules:
        actual: Decimal | None = None
        if rule.type == RuleType.MIN_RR and trade.planned_rr is not None and trade.planned_rr < rule.value:
            actual = trade.planned_rr
        elif rule.type == RuleType.MAX_DAILY_TRADES:
            count = session.scalar(
                select(func.count(Trade.id)).where(
                    Trade.account_id == trade.account_id,
                    func.date(Trade.opened_at) == trade.opened_at.date(),
                )
            ) or 0
            if Decimal(count) >= rule.value:
                actual = Decimal(count)
        elif rule.type == RuleType.MAX_DAILY_LOSS:
            daily = session.scalar(
                select(DailyStats).where(
                    DailyStats.account_id == trade.account_id,
                    DailyStats.date == trade.opened_at.date(),
                )
            )
            if daily is not None and daily.net_pnl < -abs(rule.value):
                actual = daily.net_pnl
        elif rule.type == RuleType.MAX_POSITION_SIZE and trade.metrics is not None:
            exposure = trade.metrics.average_entry * trade.metrics.total_quantity
            if exposure > rule.value:
                actual = exposure
        elif rule.type == RuleType.MANDATORY_JOURNAL and trade.is_reviewed and trade.journal_entry_id is None:
            actual = Decimal("1")
        elif rule.type == RuleType.MAX_CONSECUTIVE_LOSSES:
            recent = session.scalars(
                select(TradeMetrics)
                .join(Trade, Trade.id == TradeMetrics.trade_id)
                .where(Trade.account_id == trade.account_id)
                .order_by(Trade.closed_at.desc())
                .limit(int(rule.value))
            ).all()
            if len(recent) == int(rule.value) and all(row.realized_pnl < 0 for row in recent):
                actual = Decimal(len(recent))
        if actual is not None:
            breach = RuleBreach(
                rule_id=rule.id,
                user_id=trade.user_id,
                account_id=trade.account_id,
                trade_id=trade.id,
                breached_at=datetime.now(UTC),
                value=actual,
                acknowledged=False,
            )
            session.add(breach)
            breaches.append(breach)
    return breaches
