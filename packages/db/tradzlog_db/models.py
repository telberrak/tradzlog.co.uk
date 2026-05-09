from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, Enum, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tradzlog_db.base import Base


def uuid_str() -> str:
    return str(uuid.uuid4())


class Plan(str, enum.Enum):
    FREE = "FREE"
    PRO = "PRO"
    ELITE = "ELITE"


class AccountType(str, enum.Enum):
    LIVE = "LIVE"
    PAPER = "PAPER"
    PROP_FIRM = "PROP_FIRM"


class AssetClass(str, enum.Enum):
    STOCK = "STOCK"
    FUTURES = "FUTURES"
    FOREX = "FOREX"
    OPTIONS = "OPTIONS"
    CRYPTO = "CRYPTO"
    COMMODITY = "COMMODITY"


class Direction(str, enum.Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class TradeStatus(str, enum.Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


class ExecutionType(str, enum.Enum):
    ENTRY = "ENTRY"
    EXIT = "EXIT"
    ADD = "ADD"
    PARTIAL_EXIT = "PARTIAL_EXIT"


class JournalType(str, enum.Enum):
    TRADE_REVIEW = "TRADE_REVIEW"
    DAILY = "DAILY"
    WEEKLY = "WEEKLY"
    FREEFORM = "FREEFORM"


class MarketCondition(str, enum.Enum):
    TRENDING = "TRENDING"
    RANGING = "RANGING"
    CHOPPY = "CHOPPY"
    HIGH_VOL = "HIGH_VOL"
    LOW_VOL = "LOW_VOL"


class RuleType(str, enum.Enum):
    MAX_DAILY_LOSS = "MAX_DAILY_LOSS"
    MAX_DAILY_TRADES = "MAX_DAILY_TRADES"
    MAX_POSITION_SIZE = "MAX_POSITION_SIZE"
    MAX_CONSECUTIVE_LOSSES = "MAX_CONSECUTIVE_LOSSES"
    MANDATORY_JOURNAL = "MANDATORY_JOURNAL"
    MIN_RR = "MIN_RR"
    DAILY_PROFIT_TARGET = "DAILY_PROFIT_TARGET"


class RuleAction(str, enum.Enum):
    WARN = "WARN"
    LOCK_TRADING = "LOCK_TRADING"
    NOTIFY = "NOTIFY"


class AIInsightType(str, enum.Enum):
    PATTERN = "PATTERN"
    COACHING = "COACHING"
    WEEKLY_SUMMARY = "WEEKLY_SUMMARY"
    SETUP_ANALYSIS = "SETUP_ANALYSIS"


class BrokerSyncType(str, enum.Enum):
    CSV = "CSV"
    API = "API"


class BrokerSyncStatus(str, enum.Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class AuthTokenPurpose(str, enum.Enum):
    EMAIL_VERIFICATION = "EMAIL_VERIFICATION"
    PASSWORD_RESET = "PASSWORD_RESET"
    MAGIC_LINK = "MAGIC_LINK"


class BillingStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    TRIALING = "TRIALING"
    PAST_DUE = "PAST_DUE"
    CANCELED = "CANCELED"


class InvoiceStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    OPEN = "OPEN"
    PAID = "PAID"
    VOID = "VOID"
    UNCOLLECTIBLE = "UNCOLLECTIBLE"


class LeaderboardMetric(str, enum.Enum):
    PROFIT_FACTOR = "PROFIT_FACTOR"
    EXPECTANCY = "EXPECTANCY"
    WIN_RATE = "WIN_RATE"


class MentorAccessStatus(str, enum.Enum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    name: Mapped[str | None] = mapped_column(String(120))
    avatar_url: Mapped[str | None] = mapped_column(String(500))
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)
    hashed_password: Mapped[str | None] = mapped_column(String(255))
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    plan: Mapped[Plan] = mapped_column(Enum(Plan), default=Plan.FREE, nullable=False)
    onboarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    accounts: Mapped[list[Account]] = relationship(back_populates="user", cascade="all, delete-orphan")
    trades: Mapped[list[Trade]] = relationship(back_populates="user", cascade="all, delete-orphan")
    auth_tokens: Mapped[list[AuthToken]] = relationship(back_populates="user", cascade="all, delete-orphan")
    subscriptions: Mapped[list[BillingSubscription]] = relationship(back_populates="user", cascade="all, delete-orphan")
    invoices: Mapped[list[BillingInvoice]] = relationship(back_populates="user", cascade="all, delete-orphan")
    trade_shares: Mapped[list[PublicTradeShare]] = relationship(back_populates="user", cascade="all, delete-orphan")
    leaderboard_profiles: Mapped[list[LeaderboardProfile]] = relationship(back_populates="user", cascade="all, delete-orphan")


class AuthToken(TimestampMixin, Base):
    __tablename__ = "auth_tokens"
    __table_args__ = (
        Index("ix_auth_tokens_user_purpose", "user_id", "purpose"),
        Index("ix_auth_tokens_token_hash", "token_hash"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[AuthTokenPurpose] = mapped_column(Enum(AuthTokenPurpose), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="auth_tokens")


class BillingSubscription(TimestampMixin, Base):
    __tablename__ = "billing_subscriptions"
    __table_args__ = (Index("ix_billing_subscriptions_user_status", "user_id", "status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    plan: Mapped[Plan] = mapped_column(Enum(Plan), nullable=False)
    status: Mapped[BillingStatus] = mapped_column(Enum(BillingStatus), default=BillingStatus.ACTIVE, nullable=False)
    provider: Mapped[str] = mapped_column(String(40), default="LOCAL", nullable=False)
    provider_customer_id: Mapped[str | None] = mapped_column(String(120), index=True)
    provider_subscription_id: Mapped[str | None] = mapped_column(String(120), index=True)
    current_period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    user: Mapped[User] = relationship(back_populates="subscriptions")


class BillingInvoice(TimestampMixin, Base):
    __tablename__ = "billing_invoices"
    __table_args__ = (Index("ix_billing_invoices_user_created", "user_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    subscription_id: Mapped[str | None] = mapped_column(ForeignKey("billing_subscriptions.id", ondelete="SET NULL"), index=True)
    provider_invoice_id: Mapped[str | None] = mapped_column(String(120), index=True)
    status: Mapped[InvoiceStatus] = mapped_column(Enum(InvoiceStatus), default=InvoiceStatus.PAID, nullable=False)
    amount_due: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    amount_paid: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="USD", nullable=False)
    hosted_invoice_url: Mapped[str | None] = mapped_column(String(1000))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="invoices")


class Account(TimestampMixin, Base):
    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    broker: Mapped[str] = mapped_column(String(120), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="USD", nullable=False)
    starting_balance: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    account_type: Mapped[AccountType] = mapped_column(Enum(AccountType), nullable=False)
    prop_firm_name: Mapped[str | None] = mapped_column(String(120))
    max_daily_loss: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    max_total_loss: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    daily_profit_target: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="accounts")
    trades: Mapped[list[Trade]] = relationship(back_populates="account", cascade="all, delete-orphan")


class Instrument(TimestampMixin, Base):
    __tablename__ = "instruments"
    __table_args__ = (UniqueConstraint("symbol", "asset_class", name="uq_instruments_symbol_asset_class"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    symbol: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    asset_class: Mapped[AssetClass] = mapped_column(Enum(AssetClass), index=True, nullable=False)
    exchange: Mapped[str | None] = mapped_column(String(80))
    tick_size: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    contract_size: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    point_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    currency: Mapped[str] = mapped_column(String(8), default="USD", nullable=False)


class Trade(TimestampMixin, Base):
    __tablename__ = "trades"
    __table_args__ = (
        Index("ix_trades_user_status_opened", "user_id", "status", "opened_at"),
        Index("ix_trades_account_opened", "account_id", "opened_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    instrument_id: Mapped[str] = mapped_column(ForeignKey("instruments.id"), index=True)
    direction: Mapped[Direction] = mapped_column(Enum(Direction), nullable=False)
    status: Mapped[TradeStatus] = mapped_column(Enum(TradeStatus), default=TradeStatus.OPEN, nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    setup_tag: Mapped[str | None] = mapped_column(String(80), index=True)
    timeframe: Mapped[str | None] = mapped_column(String(16))
    planned_entry: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    planned_stop: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    planned_target: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    planned_rr: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    commissions: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    emotional_rating: Mapped[int | None] = mapped_column(Integer)
    mistake_flags: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    tags: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    is_reviewed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    journal_entry_id: Mapped[str | None] = mapped_column(ForeignKey("journal_entries.id", use_alter=True))

    account: Mapped[Account] = relationship(back_populates="trades")
    user: Mapped[User] = relationship(back_populates="trades")
    instrument: Mapped[Instrument] = relationship()
    executions: Mapped[list[Execution]] = relationship(back_populates="trade", cascade="all, delete-orphan", order_by="Execution.executed_at")
    metrics: Mapped[TradeMetrics | None] = relationship(back_populates="trade", cascade="all, delete-orphan", uselist=False)


class Execution(TimestampMixin, Base):
    __tablename__ = "executions"
    __table_args__ = (Index("ix_executions_trade_time", "trade_id", "executed_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    trade_id: Mapped[str] = mapped_column(ForeignKey("trades.id", ondelete="CASCADE"), index=True)
    type: Mapped[ExecutionType] = mapped_column(Enum(ExecutionType), nullable=False)
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    fees: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"), nullable=False)
    broker_id: Mapped[str | None] = mapped_column(String(120), index=True)
    notes: Mapped[str | None] = mapped_column(Text)

    trade: Mapped[Trade] = relationship(back_populates="executions")


class TradeMetrics(TimestampMixin, Base):
    __tablename__ = "trade_metrics"

    trade_id: Mapped[str] = mapped_column(ForeignKey("trades.id", ondelete="CASCADE"), primary_key=True)
    average_entry: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    average_exit: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    total_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    pnl_percent: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    r_multiple: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    holding_period_seconds: Mapped[int | None] = mapped_column(Integer)
    max_adverse_excursion: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    max_favorable_excursion: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))

    trade: Mapped[Trade] = relationship(back_populates="metrics")


class JournalEntry(TimestampMixin, Base):
    __tablename__ = "journal_entries"
    __table_args__ = (Index("ix_journal_user_date", "user_id", "date"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    type: Mapped[JournalType] = mapped_column(Enum(JournalType), nullable=False)
    trade_id: Mapped[str | None] = mapped_column(ForeignKey("trades.id", ondelete="SET NULL"), index=True)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    content: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, nullable=False)
    mood: Mapped[int | None] = mapped_column(Integer)
    market_condition: Mapped[MarketCondition | None] = mapped_column(Enum(MarketCondition))
    key_lessons: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)


class Attachment(TimestampMixin, Base):
    __tablename__ = "attachments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    journal_entry_id: Mapped[str | None] = mapped_column(ForeignKey("journal_entries.id", ondelete="CASCADE"), index=True)
    trade_id: Mapped[str | None] = mapped_column(ForeignKey("trades.id", ondelete="CASCADE"), index=True)
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(120), nullable=False)
    annotation_data: Mapped[dict[str, object] | None] = mapped_column(JSONB)


class TradingRule(TimestampMixin, Base):
    __tablename__ = "trading_rules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[str | None] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(140), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[RuleType] = mapped_column(Enum(RuleType), nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    action: Mapped[RuleAction] = mapped_column(Enum(RuleAction), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class RuleBreach(TimestampMixin, Base):
    __tablename__ = "rule_breaches"
    __table_args__ = (Index("ix_rule_breaches_user_time", "user_id", "breached_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    rule_id: Mapped[str] = mapped_column(ForeignKey("trading_rules.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    trade_id: Mapped[str | None] = mapped_column(ForeignKey("trades.id", ondelete="SET NULL"), index=True)
    breached_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    value: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class DailyStats(TimestampMixin, Base):
    __tablename__ = "daily_stats"
    __table_args__ = (UniqueConstraint("account_id", "date", name="uq_daily_stats_account_date"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    date: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    trades_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    wins: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    losses: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    breakeven: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    gross_pnl: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"), nullable=False)
    net_pnl: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"), nullable=False)
    commissions: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"), nullable=False)
    win_rate: Mapped[Decimal] = mapped_column(Numeric(18, 8), default=Decimal("0"), nullable=False)
    avg_win: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"), nullable=False)
    avg_loss: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"), nullable=False)
    profit_factor: Mapped[Decimal] = mapped_column(Numeric(18, 8), default=Decimal("0"), nullable=False)
    biggest_win: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"), nullable=False)
    biggest_loss: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=Decimal("0"), nullable=False)
    r_multiple_sum: Mapped[Decimal] = mapped_column(Numeric(18, 8), default=Decimal("0"), nullable=False)


class AccountSnapshot(TimestampMixin, Base):
    __tablename__ = "account_snapshots"
    __table_args__ = (UniqueConstraint("account_id", "date", name="uq_account_snapshots_account_date"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    date: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    balance: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    drawdown_from_peak: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    drawdown_pct: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)


class AIInsight(TimestampMixin, Base):
    __tablename__ = "ai_insights"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[str | None] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    type: Mapped[AIInsightType] = mapped_column(Enum(AIInsightType), nullable=False)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    supporting_trade_ids: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BrokerSync(TimestampMixin, Base):
    __tablename__ = "broker_syncs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    broker: Mapped[str] = mapped_column(String(120), nullable=False)
    sync_type: Mapped[BrokerSyncType] = mapped_column(Enum(BrokerSyncType), nullable=False)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[BrokerSyncStatus] = mapped_column(Enum(BrokerSyncStatus), default=BrokerSyncStatus.PENDING, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)


class PublicTradeShare(TimestampMixin, Base):
    __tablename__ = "public_trade_shares"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_public_trade_shares_slug"),
        Index("ix_public_trade_shares_user", "user_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    trade_id: Mapped[str] = mapped_column(ForeignKey("trades.id", ondelete="CASCADE"), index=True)
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    anonymized: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    show_r_multiple_only: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="trade_shares")


class LeaderboardProfile(TimestampMixin, Base):
    __tablename__ = "leaderboard_profiles"
    __table_args__ = (UniqueConstraint("user_id", name="uq_leaderboard_profiles_user"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    display_name: Mapped[str] = mapped_column(String(80), nullable=False)
    metric: Mapped[LeaderboardMetric] = mapped_column(Enum(LeaderboardMetric), default=LeaderboardMetric.EXPECTANCY, nullable=False)
    is_public: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    asset_class_filter: Mapped[AssetClass | None] = mapped_column(Enum(AssetClass))

    user: Mapped[User] = relationship(back_populates="leaderboard_profiles")


class MentorAccess(TimestampMixin, Base):
    __tablename__ = "mentor_access"
    __table_args__ = (Index("ix_mentor_access_student", "student_user_id", "status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    student_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    mentor_email: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    mentor_name: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[MentorAccessStatus] = mapped_column(Enum(MentorAccessStatus), default=MentorAccessStatus.ACTIVE, nullable=False)
    can_view_journals: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    can_comment: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MentorComment(TimestampMixin, Base):
    __tablename__ = "mentor_comments"
    __table_args__ = (Index("ix_mentor_comments_trade", "trade_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    mentor_access_id: Mapped[str] = mapped_column(ForeignKey("mentor_access.id", ondelete="CASCADE"), index=True)
    student_user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    trade_id: Mapped[str | None] = mapped_column(ForeignKey("trades.id", ondelete="CASCADE"), index=True)
    journal_entry_id: Mapped[str | None] = mapped_column(ForeignKey("journal_entries.id", ondelete="CASCADE"), index=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
