from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from io import StringIO


@dataclass(frozen=True)
class ImportedExecution:
    symbol: str
    executed_at: datetime
    side: str
    price: Decimal
    quantity: Decimal
    fees: Decimal
    broker_id: str | None


COLUMN_ALIASES = {
    "symbol": {"symbol", "ticker", "instrument", "contract"},
    "executed_at": {"executed_at", "datetime", "date/time", "time", "timestamp", "date"},
    "side": {"side", "action", "buy/sell", "type"},
    "price": {"price", "fill price", "avg price"},
    "quantity": {"quantity", "qty", "shares", "contracts", "size"},
    "fees": {"fees", "commission", "commissions"},
    "broker_id": {"order id", "order_id", "broker id", "execution id", "exec id"},
}


def _normalise_header(value: str) -> str:
    cleaned = value.strip().lower()
    for canonical, aliases in COLUMN_ALIASES.items():
        if cleaned in aliases:
            return canonical
    return cleaned.replace(" ", "_")


def parse_decimal(value: str | None, default: str = "0") -> Decimal:
    if value is None or value.strip() == "":
        return Decimal(default)
    return Decimal(value.replace(",", "").strip())


def parse_datetime(value: str) -> datetime:
    value = value.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(value, fmt)
            return parsed.replace(tzinfo=UTC)
        except ValueError:
            continue
    return datetime.fromisoformat(value).astimezone(UTC)


def parse_broker_csv(raw: bytes) -> list[ImportedExecution]:
    text = raw.decode("utf-8-sig")
    reader = csv.DictReader(StringIO(text))
    if reader.fieldnames is None:
        return []
    reader.fieldnames = [_normalise_header(field) for field in reader.fieldnames]
    rows: list[ImportedExecution] = []
    for row in reader:
        symbol = (row.get("symbol") or "").upper().strip()
        timestamp = row.get("executed_at")
        price = parse_decimal(row.get("price"))
        quantity = parse_decimal(row.get("quantity"))
        if not symbol or not timestamp or price <= 0 or quantity <= 0:
            continue
        rows.append(
            ImportedExecution(
                symbol=symbol,
                executed_at=parse_datetime(timestamp),
                side=(row.get("side") or "").upper().strip(),
                price=price,
                quantity=quantity,
                fees=parse_decimal(row.get("fees")),
                broker_id=row.get("broker_id") or None,
            )
        )
    return rows
