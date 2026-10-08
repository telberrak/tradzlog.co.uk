"""Broker export parsing: every supported format becomes a list of ``ImportedExecution`` fills.

Conventions shared by all formats, so instruments mean the same thing whichever broker they came from:

- times are converted to UTC; times without an offset are read in the timezone the user picked;
- ``side`` is BUY or SELL and ``quantity`` is positive;
- ``fees`` is the total cost of the fill (commission, exchange fees, swap), positive when paid;
- forex quantities are in lots with a 100,000 point value (IBKR base-currency units are converted);
- ``broker_pnl`` is the broker's own realised P&L for the fill, net of costs, when the file has it.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, tzinfo
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from io import StringIO

FORMATS: dict[str, str] = {
    "auto": "Detect automatically",
    "generic": "Generic CSV (one fill per row)",
    "ibkr_flex": "Interactive Brokers (Flex Query: Trades)",
    "mt5": "MetaTrader 5 (Deals, CSV or HTML report)",
    "ninjatrader": "NinjaTrader (Executions)",
    "tradovate": "Tradovate (Performance)",
}

FOREX_LOT = Decimal("100000")
CURRENCIES = {
    "USD", "EUR", "GBP", "JPY", "CHF", "AUD", "NZD", "CAD", "SEK", "NOK", "DKK", "PLN", "HUF", "CZK", "TRY",
    "ZAR", "MXN", "SGD", "HKD", "CNH", "ILS", "RUB", "THB",
}
# Dollar value of a 1.00 price move for one contract of common futures (by root symbol).
FUTURES_POINT_VALUES: dict[str, Decimal] = {
    root: Decimal(value)
    for root, value in {
        "ES": "50", "MES": "5", "NQ": "20", "MNQ": "2", "YM": "5", "MYM": "0.5", "RTY": "50", "M2K": "5",
        "CL": "1000", "MCL": "100", "QM": "500", "NG": "10000", "QG": "2500", "GC": "100", "MGC": "10",
        "SI": "5000", "SIL": "1000", "HG": "25000", "PL": "50", "ZB": "1000", "UB": "1000", "ZN": "1000",
        "ZF": "1000", "ZT": "2000", "6E": "125000", "M6E": "12500", "6B": "62500", "6J": "12500000",
        "6A": "100000", "6C": "100000", "6S": "125000", "ZC": "50", "ZS": "50", "ZW": "50", "FDAX": "25",
        "FDXM": "5", "FESX": "10",
    }.items()
}
MONTH_CODES = "FGHJKMNQUVXZ"


@dataclass(frozen=True)
class ImportedExecution:
    symbol: str
    executed_at: datetime
    side: str
    price: Decimal
    quantity: Decimal
    fees: Decimal = Decimal("0")
    broker_id: str | None = None
    asset_class: str | None = None
    point_value: Decimal | None = None
    currency: str | None = None
    broker_pnl: Decimal | None = None
    row_number: int = 0


@dataclass(frozen=True)
class RowProblem:
    row_number: int
    reason: str


@dataclass
class ParseResult:
    file_format: str
    executions: list[ImportedExecution] = field(default_factory=list)
    problems: list[RowProblem] = field(default_factory=list)
    skipped: int = 0  # rows that are not trades (deposits, headers, cancelled fills)


class ImportFormatError(ValueError):
    """The file can't be read as the chosen format."""


# --------------------------------------------------------------------- shared helpers


def parse_decimal(value: str | None, default: str | None = "0") -> Decimal | None:
    """Numbers as brokers write them: thousands separators, $ signs, (1.00) for negatives."""
    if value is None or value.strip() in {"", "-", "--"}:
        return Decimal(default) if default is not None else None
    cleaned = value.strip().replace(",", "").replace("$", "").replace(" ", "").replace(" ", "")
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    cleaned = cleaned.strip("()")
    try:
        number = Decimal(cleaned)
    except InvalidOperation:
        raise ValueError(f"not a number: {value.strip()!r}") from None
    return -number if negative else number


DATETIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y%m%d %H%M%S", "%Y%m%d %H:%M:%S",
    "%Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%Y %I:%M:%S %p",
    "%m/%d/%Y %I:%M %p", "%d/%m/%Y %H:%M:%S", "%Y-%m-%d", "%Y%m%d", "%m/%d/%Y",
)


def parse_datetime(value: str, tz: tzinfo = UTC) -> datetime:
    """A timestamp in UTC; values without an offset are read in ``tz``."""
    text = re.sub(r"\s+", " ", value.strip().replace(";", " ").replace(",", " ")).strip()
    for fmt in DATETIME_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return parsed.replace(tzinfo=tz).astimezone(UTC)
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        raise ValueError(f"unrecognised date/time: {value.strip()!r}") from None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=tz)).astimezone(UTC)


SIDES = {
    "BUY": "BUY", "B": "BUY", "BOT": "BUY", "BOUGHT": "BUY", "BUYTOCOVER": "BUY", "BUY TO COVER": "BUY",
    "BUYTOOPEN": "BUY", "BUY TO OPEN": "BUY", "BUYTOCLOSE": "BUY", "BUY TO CLOSE": "BUY", "COVER": "BUY", "LONG": "BUY",
    "SELL": "SELL", "S": "SELL", "SLD": "SELL", "SOLD": "SELL", "SELLSHORT": "SELL", "SELL SHORT": "SELL",
    "SHORT": "SELL", "SELLTOOPEN": "SELL", "SELL TO OPEN": "SELL", "SELLTOCLOSE": "SELL", "SELL TO CLOSE": "SELL",
}


def normalise_side(value: str | None) -> str | None:
    return SIDES.get(re.sub(r"\s+", " ", (value or "").strip().upper()))


def decode(raw: bytes) -> str:
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")  # MetaTrader HTML reports
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ImportFormatError("The file's text encoding isn't supported.")


def csv_rows(text: str) -> list[list[str]]:
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = "\t" if sample.count("\t") > sample.count(",") else ","
    return [row for row in csv.reader(StringIO(text), delimiter=delimiter) if any(cell.strip() for cell in row)]


class _TableReader(HTMLParser):
    """Collects the cell text of every table row in an HTML document."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._span = 1

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []
            span = dict(attrs).get("colspan")
            self._span = int(span) if span and span.isdigit() else 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._row is not None and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._row.extend([""] * (self._span - 1))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if any(cell for cell in self._row):
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def html_rows(text: str) -> list[list[str]]:
    reader = _TableReader()
    reader.feed(text)
    return reader.rows


def key(header: str) -> str:
    return re.sub(r"[^a-z0-9/]", "", header.strip().lower())


def records(header: list[str], rows: list[list[str]], start: int) -> list[tuple[int, dict[str, str]]]:
    keys = [key(cell) for cell in header]
    out = []
    for offset, row in enumerate(rows):
        values = [cell.strip() for cell in row] + [""] * (len(keys) - len(row))
        out.append((start + offset, dict(zip(keys, values, strict=False))))
    return out


def futures_root(symbol: str) -> str | None:
    """'ESZ6', 'ESZ26', 'MNQH7', 'ES 12-26' -> 'ES' / 'MNQ' when the root is a known contract."""
    symbol = symbol.upper().strip()
    candidates = [symbol.split(" ")[0]]
    match = re.fullmatch(rf"([A-Z0-9]{{1,4}}?)[{MONTH_CODES}]\d{{1,2}}", symbol)
    if match:
        candidates.insert(0, match.group(1))
    return next((root for root in candidates if root in FUTURES_POINT_VALUES), None)


def is_currency_pair(symbol: str) -> bool:
    letters = re.sub(r"[^A-Z]", "", symbol.upper())[:6]
    return len(letters) == 6 and letters[:3] in CURRENCIES and letters[3:] in CURRENCIES


# --------------------------------------------------------------------- formats


def detect_format(header_keys: set[str]) -> str:
    if {"tradeprice", "buy/sell"} <= header_keys or "ibcommission" in header_keys:
        return "ibkr_flex"
    if {"buyprice", "sellprice", "boughttimestamp", "soldtimestamp"} <= header_keys:
        return "tradovate"
    if {"instrument", "action", "e/x"} <= header_keys or {"instrument", "action", "quantity", "price", "time", "orderid"} <= header_keys:
        return "ninjatrader"
    if {"deal", "symbol", "type", "direction", "volume", "price"} <= header_keys:
        return "mt5"
    return "generic"


GENERIC_ALIASES = {
    "symbol": {"symbol", "ticker", "instrument", "contract", "market", "security"},
    "time": {"executedat", "datetime", "date/time", "time", "timestamp", "date", "filltime", "executiontime", "tradetime"},
    "side": {"side", "action", "buy/sell", "type", "direction", "b/s"},
    "price": {"price", "fillprice", "avgprice", "averageprice", "executionprice", "tradeprice"},
    "quantity": {"quantity", "qty", "shares", "contracts", "size", "volume", "filledqty", "amount"},
    "fees": {"fees", "fee", "commission", "commissions", "costs"},
    "broker_id": {"orderid", "order", "brokerid", "executionid", "execid", "fillid", "tradeid", "id", "dealid"},
}


def parse_generic(rows: list[tuple[int, dict[str, str]]], tz: tzinfo, result: ParseResult) -> None:
    def pick(row: dict[str, str], name: str) -> str:
        return next((row[alias] for alias in GENERIC_ALIASES[name] if row.get(alias)), "")

    for number, row in rows:
        try:
            quantity = parse_decimal(pick(row, "quantity"))
            side = normalise_side(pick(row, "side")) or ("SELL" if quantity < 0 else "BUY" if quantity > 0 and not pick(row, "side") else None)
            if side is None:
                raise ValueError(f"buy or sell not recognised: {pick(row, 'side')!r}")
            add(result, number, pick(row, "symbol").upper(), parse_datetime(pick(row, "time"), tz), side,
                parse_decimal(pick(row, "price")), abs(quantity), abs(parse_decimal(pick(row, "fees"))), pick(row, "broker_id") or None)
        except ValueError as problem:
            result.problems.append(RowProblem(number, str(problem)))


IBKR_ASSET_CLASSES = {"STK": "STOCK", "FUT": "FUTURES", "CASH": "FOREX", "OPT": "OPTIONS", "FOP": "OPTIONS",
                      "CRYPTO": "CRYPTO", "CMDTY": "COMMODITY", "CFD": "STOCK", "WAR": "OPTIONS"}


def parse_ibkr_flex(rows: list[tuple[int, dict[str, str]]], tz: tzinfo, result: ParseResult) -> None:
    for number, row in rows:
        if row.get("buy/sell", "").upper() in {"", "BUY/SELL"} or "CA." in row.get("buy/sell", "").upper():
            result.skipped += 1  # repeated headers, cancellations
            continue
        try:
            asset_class = IBKR_ASSET_CLASSES.get(row.get("assetclass", "").upper())
            when = row.get("datetime") or " ".join(part for part in (row.get("tradedate", ""), row.get("tradetime", "")) if part)
            quantity = abs(parse_decimal(row.get("quantity")))
            point_value = parse_decimal(row.get("multiplier"), default=None)
            symbol = row.get("symbol", "").upper()
            if asset_class == "FOREX":
                symbol = symbol.replace(".", "")
                quantity, point_value = quantity / FOREX_LOT, FOREX_LOT
            fees = -(parse_decimal(row.get("ibcommission")) + parse_decimal(row.get("taxes")))
            add(result, number, symbol, parse_datetime(when, tz), normalise_side(row.get("buy/sell")) or "",
                parse_decimal(row.get("tradeprice")), quantity, fees,
                row.get("ibexecid") or row.get("tradeid") or row.get("iborderid") or None,
                asset_class=asset_class, point_value=point_value if point_value and point_value > 0 else None,
                currency=(row.get("currencyprimary") or row.get("currency") or None),
                broker_pnl=parse_decimal(row.get("fifopnlrealized"), default=None))
        except ValueError as problem:
            result.problems.append(RowProblem(number, str(problem)))


def mt_asset_hints(symbol: str) -> tuple[str | None, Decimal | None]:
    if is_currency_pair(symbol):
        return "FOREX", FOREX_LOT
    if symbol.upper().startswith(("XAU", "GOLD")):
        return "COMMODITY", Decimal("100")
    if symbol.upper().startswith(("XAG", "SILVER")):
        return "COMMODITY", Decimal("5000")
    return None, None


def parse_mt5(rows: list[tuple[int, dict[str, str]]], tz: tzinfo, result: ParseResult) -> None:
    for number, row in rows:
        kind = row.get("type", "").strip().lower()
        if kind not in {"buy", "sell"}:
            result.skipped += 1  # balance, credit, deposits, summary lines
            continue
        try:
            costs = parse_decimal(row.get("commission")) + parse_decimal(row.get("fee")) + parse_decimal(row.get("swap"))
            asset_class, point_value = mt_asset_hints(row.get("symbol", ""))
            add(result, number, row.get("symbol", "").upper(), parse_datetime(row.get("time", ""), tz), kind.upper(),
                parse_decimal(row.get("price")), abs(parse_decimal(row.get("volume"))), -costs, row.get("deal") or None,
                asset_class=asset_class, point_value=point_value,
                broker_pnl=parse_decimal(row.get("profit")) + costs)
        except ValueError as problem:
            result.problems.append(RowProblem(number, str(problem)))


def parse_ninjatrader(rows: list[tuple[int, dict[str, str]]], tz: tzinfo, result: ParseResult) -> None:
    for number, row in rows:
        try:
            symbol = row.get("instrument", "").upper()
            root = futures_root(symbol)
            add(result, number, symbol, parse_datetime(row.get("time", ""), tz), normalise_side(row.get("action")) or "",
                parse_decimal(row.get("price")), abs(parse_decimal(row.get("quantity"))), abs(parse_decimal(row.get("commission"))),
                row.get("id") or row.get("orderid") or None,
                asset_class="FUTURES" if root or re.search(r" \d{2}-\d{2}$", symbol) else None,
                point_value=FUTURES_POINT_VALUES.get(root) if root else None)
        except ValueError as problem:
            result.problems.append(RowProblem(number, str(problem)))


def parse_tradovate(rows: list[tuple[int, dict[str, str]]], tz: tzinfo, result: ParseResult) -> None:
    """Performance rows are matched round trips: each becomes its buy fill and its sell fill."""
    for number, row in rows:
        try:
            symbol = row.get("symbol", "").upper()
            root = futures_root(symbol)
            quantity = abs(parse_decimal(row.get("qty")))
            bought = parse_datetime(row.get("boughttimestamp", ""), tz)
            sold = parse_datetime(row.get("soldtimestamp", ""), tz)
            hints = {"asset_class": "FUTURES", "point_value": FUTURES_POINT_VALUES.get(root) if root else None}
            pnl = parse_decimal(row.get("pnl"), default=None)
            buy_pnl, sell_pnl = (pnl, None) if bought > sold else (None, pnl)  # P&L belongs to the closing fill
            add(result, number, symbol, bought, "BUY", parse_decimal(row.get("buyprice")), quantity, Decimal("0"),
                row.get("buyfillid") or None, broker_pnl=buy_pnl, **hints)
            add(result, number, symbol, sold, "SELL", parse_decimal(row.get("sellprice")), quantity, Decimal("0"),
                row.get("sellfillid") or None, broker_pnl=sell_pnl, **hints)
        except ValueError as problem:
            result.problems.append(RowProblem(number, str(problem)))


PARSERS = {
    "generic": parse_generic,
    "ibkr_flex": parse_ibkr_flex,
    "mt5": parse_mt5,
    "ninjatrader": parse_ninjatrader,
    "tradovate": parse_tradovate,
}


def add(result: ParseResult, number: int, symbol: str, executed_at: datetime, side: str, price: Decimal | None,
        quantity: Decimal | None, fees: Decimal | None, broker_id: str | None, **hints) -> None:
    if not symbol:
        raise ValueError("no symbol")
    if side not in {"BUY", "SELL"}:
        raise ValueError("buy or sell not recognised")
    if price is None or price <= 0:
        raise ValueError("price must be more than 0")
    if quantity is None or quantity <= 0:
        raise ValueError("quantity must be more than 0")
    result.executions.append(
        ImportedExecution(symbol=symbol.strip(), executed_at=executed_at, side=side, price=price, quantity=quantity,
                          fees=fees or Decimal("0"), broker_id=(broker_id or "").strip()[:120] or None, row_number=number, **hints)
    )


def find_header(rows: list[list[str]]) -> int:
    """The first row that looks like a header for a known format (or the first row)."""
    for index, row in enumerate(rows[:200]):
        keys = {key(cell) for cell in row}
        if detect_format(keys) != "generic" or ({"symbol", "price"} <= keys) or ({"instrument", "price"} <= keys):
            return index
    return 0


def strip_flex_codes(rows: list[list[str]]) -> list[list[str]]:
    """IBKR 'CSV with section codes': drop BOF/EOF lines and the HEADER/DATA + section columns."""
    if not rows or rows[0][0].strip().upper() not in {"BOF", "HEADER", "DATA", "BOA", "BOS"}:
        return rows
    out = []
    for row in rows:
        code = row[0].strip().upper()
        if code in {"HEADER", "DATA"} and len(row) > 2:
            out.append(row[2:])
    return out


def parse_import(raw: bytes, file_format: str = "auto", tz: tzinfo = UTC) -> ParseResult:
    if not raw.strip():
        raise ImportFormatError("The file is empty.")
    text = decode(raw)
    rows = html_rows(text) if text.lstrip()[:1] == "<" else strip_flex_codes(csv_rows(text))
    if not rows:
        raise ImportFormatError("No rows found. Export the file as CSV (or MetaTrader's HTML report).")
    header_index = find_header(rows)
    header = rows[header_index]
    detected = detect_format({key(cell) for cell in header})
    chosen = detected if file_format == "auto" else file_format
    if chosen not in PARSERS:
        raise ImportFormatError(f"Unknown format: {file_format}")
    body = rows[header_index + 1 :]
    if chosen == "mt5" and text.lstrip()[:1] == "<":
        body = mt5_deals_section(body, len(header))
    result = ParseResult(file_format=chosen)
    PARSERS[chosen](records(header, body, start=header_index + 2), tz, result)
    if not result.executions and not result.problems:
        raise ImportFormatError("No trades found in this file. Check the format, or that the export includes trades.")
    return result


def mt5_deals_section(rows: list[list[str]], width: int) -> list[list[str]]:
    """In an MT5 HTML report the deals table ends where a row of a different shape begins."""
    out = []
    for row in rows:
        if len([cell for cell in row if cell]) <= 2 and out:
            break
        out.append(row[:width])
    return out


# --------------------------------------------------------------------- API compatibility


def parse_broker_csv(raw: bytes) -> list[ImportedExecution]:
    """Fills from a CSV in any supported format (times without an offset read as UTC)."""
    try:
        return parse_import(raw).executions
    except ImportFormatError:
        return []
