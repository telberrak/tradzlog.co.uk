from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class TradeStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


class ExecutionType(str, Enum):
    ENTRY = "ENTRY"
    EXIT = "EXIT"
    ADD = "ADD"
    PARTIAL_EXIT = "PARTIAL_EXIT"


class AccountType(str, Enum):
    LIVE = "LIVE"
    PAPER = "PAPER"
    PROP_FIRM = "PROP_FIRM"


class AssetClass(str, Enum):
    STOCK = "STOCK"
    FUTURES = "FUTURES"
    FOREX = "FOREX"
    OPTIONS = "OPTIONS"
    CRYPTO = "CRYPTO"
    COMMODITY = "COMMODITY"


class OrmModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    name: str | None = Field(default=None, max_length=120)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class MagicLinkRequest(BaseModel):
    email: EmailStr


class TokenExchangeRequest(BaseModel):
    token: str = Field(min_length=32, max_length=512)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=32, max_length=512)
    password: str = Field(min_length=8, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"


class UserRead(OrmModel):
    id: str
    email: EmailStr
    name: str | None
    avatar_url: str | None
    timezone: str
    email_verified_at: datetime | None
    plan: str
    onboarded_at: datetime | None


class AccountCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    broker: str = Field(min_length=1, max_length=120)
    currency: str = Field(default="USD", min_length=3, max_length=8)
    starting_balance: Decimal = Field(gt=0)
    account_type: AccountType
    prop_firm_name: str | None = None
    max_daily_loss: Decimal | None = None
    max_total_loss: Decimal | None = None
    daily_profit_target: Decimal | None = None


class AccountUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    broker: str | None = Field(default=None, min_length=1, max_length=120)
    currency: str | None = Field(default=None, min_length=3, max_length=8)
    starting_balance: Decimal | None = Field(default=None, gt=0)
    account_type: AccountType | None = None
    prop_firm_name: str | None = None
    max_daily_loss: Decimal | None = None
    max_total_loss: Decimal | None = None
    daily_profit_target: Decimal | None = None


class AccountRead(AccountCreate, OrmModel):
    id: str
    user_id: str
    created_at: datetime
    updated_at: datetime


class InstrumentCreate(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=160)
    asset_class: AssetClass
    exchange: str | None = None
    tick_size: Decimal | None = None
    contract_size: Decimal | None = None
    point_value: Decimal | None = None
    currency: str = "USD"

    @field_validator("symbol")
    @classmethod
    def uppercase_symbol(cls, value: str) -> str:
        return value.upper().strip()


class InstrumentRead(InstrumentCreate, OrmModel):
    id: str


class ExecutionCreate(BaseModel):
    type: ExecutionType
    executed_at: datetime
    price: Decimal = Field(gt=0)
    quantity: Decimal = Field(gt=0)
    fees: Decimal = Field(default=Decimal("0"), ge=0)
    broker_id: str | None = None
    notes: str | None = None


class ExecutionRead(ExecutionCreate, OrmModel):
    id: str
    trade_id: str


class TradeCreate(BaseModel):
    account_id: str
    instrument_id: str
    direction: Direction
    status: TradeStatus = TradeStatus.OPEN
    opened_at: datetime
    closed_at: datetime | None = None
    setup_tag: str | None = None
    timeframe: str | None = None
    planned_entry: Decimal | None = None
    planned_stop: Decimal | None = None
    planned_target: Decimal | None = None
    commissions: Decimal = Field(default=Decimal("0"), ge=0)
    notes: str | None = None
    emotional_rating: int | None = Field(default=None, ge=1, le=5)
    mistake_flags: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    executions: list[ExecutionCreate] = Field(default_factory=list)


class TradeUpdate(BaseModel):
    instrument_id: str | None = None
    direction: Direction | None = None
    status: TradeStatus | None = None
    opened_at: datetime | None = None
    closed_at: datetime | None = None
    setup_tag: str | None = None
    timeframe: str | None = None
    planned_entry: Decimal | None = None
    planned_stop: Decimal | None = None
    planned_target: Decimal | None = None
    commissions: Decimal | None = Field(default=None, ge=0)
    notes: str | None = None
    emotional_rating: int | None = Field(default=None, ge=1, le=5)
    mistake_flags: list[str] | None = None
    tags: list[str] | None = None
    is_reviewed: bool | None = None


class CloseTradeRequest(BaseModel):
    exit_price: Decimal = Field(gt=0)
    exit_datetime: datetime
    quantity: Decimal = Field(gt=0)
    fees: Decimal = Field(default=Decimal("0"), ge=0)


class TradeMetricsRead(OrmModel):
    trade_id: str
    average_entry: Decimal
    average_exit: Decimal | None
    total_quantity: Decimal
    realized_pnl: Decimal
    pnl_percent: Decimal | None
    r_multiple: Decimal | None
    holding_period_seconds: int | None


class TradeRead(OrmModel):
    id: str
    account_id: str
    user_id: str
    instrument_id: str
    direction: str
    status: str
    opened_at: datetime
    closed_at: datetime | None
    setup_tag: str | None
    timeframe: str | None
    planned_entry: Decimal | None
    planned_stop: Decimal | None
    planned_target: Decimal | None
    planned_rr: Decimal | None
    commissions: Decimal
    notes: str | None
    emotional_rating: int | None
    mistake_flags: list[str]
    tags: list[str]
    is_reviewed: bool
    executions: list[ExecutionRead] = Field(default_factory=list)
    metrics: TradeMetricsRead | None = None


class JournalCreate(BaseModel):
    type: str
    trade_id: str | None = None
    date: date
    title: str = Field(min_length=1, max_length=180)
    content: dict[str, object]
    mood: int | None = Field(default=None, ge=1, le=5)
    market_condition: str | None = None
    key_lessons: list[str] = Field(default_factory=list, max_length=5)


class JournalRead(JournalCreate, OrmModel):
    id: str
    user_id: str
    created_at: datetime
    updated_at: datetime


class RuleCreate(BaseModel):
    account_id: str | None = None
    name: str
    description: str
    type: str
    value: Decimal
    action: str
    is_active: bool = True


class RuleRead(RuleCreate, OrmModel):
    id: str
    user_id: str


class AnalyticsSummary(BaseModel):
    trades_count: int
    net_pnl: Decimal
    gross_pnl: Decimal
    win_rate: Decimal
    profit_factor: Decimal
    average_r: Decimal
    average_hold_seconds: Decimal
