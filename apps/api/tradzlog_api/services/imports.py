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

# Bump whenever parsing changes what a file produces: uploads read by an older version can't be
# confirmed (their stored rows would import the old, wrong reading) and must be uploaded again.
PARSER_VERSION = 3

FORMATS: dict[str, str] = {
    "auto": "Detect automatically",
    "generic": "Generic CSV (one fill per row)",
    "ibkr_flex": "Interactive Brokers (Flex Query: Trades)",
    "mt5": "MetaTrader 5 (Deals, CSV or HTML report)",
    "ninjatrader": "NinjaTrader (Executions)",
    "tradovate": "Tradovate (Performance)",
    "tastytrade": "tastytrade (History: Transactions)",
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
    date_note: str = ""  # set when slash dates were ambiguous and a date order was assumed


@dataclass(frozen=True)
class Clock:
    """How to read the file's times: its timezone and, for 03/04-style dates, the date order."""

    tz: tzinfo = UTC
    day_first: bool = False

    def __call__(self, value: str) -> datetime:
        return parse_datetime(value, self.tz, self.day_first)


SLASH_DATE = re.compile(r"(?<!\d)(\d{1,2})/(\d{1,2})/(\d{4})(?!\d)")


def detect_day_first(rows: list[list[str]]) -> bool | None:
    """True/False when some date settles the order (a part above 12), None if every date is ambiguous."""
    for row in rows:
        for cell in row:
            for first, second, _ in SLASH_DATE.findall(cell):
                if int(first) > 12 >= int(second):
                    return True
                if int(second) > 12 >= int(first):
                    return False
    return None


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


ISO_FORMATS = (
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y%m%d %H%M%S", "%Y%m%d %H:%M:%S",
    "%Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M", "%Y-%m-%d", "%Y%m%d",
)
MONTH_FIRST = ("%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %I:%M %p", "%m/%d/%Y")
DAY_FIRST = ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y %I:%M:%S %p", "%d/%m/%Y %I:%M %p", "%d/%m/%Y")
DATE_ORDERS = {"auto": "Detect from the file", "mdy": "Month first (03/04/2026 = 4 March)", "dmy": "Day first (03/04/2026 = 3 April)"}


def parse_datetime(value: str, tz: tzinfo = UTC, day_first: bool = False) -> datetime:
    """A timestamp in UTC; values without an offset are read in ``tz``. ``day_first`` decides
    whether 03/04/2026 is 3 April (day first, e.g. UK) or 4 March (month first, US)."""
    text = re.sub(r"\s+", " ", value.strip().replace(";", " ").replace(",", " ")).strip()
    slash = (DAY_FIRST + MONTH_FIRST) if day_first else (MONTH_FIRST + DAY_FIRST)
    for fmt in ISO_FORMATS + slash:
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


# OCC option symbol: root (up to 6, often space-padded), YYMMDD, C/P, strike x1000 (8 digits).
OCC_OPTION = re.compile(r"^[A-Z0-9.]{1,6}\s*\d{6}[CP]\d{8}$")
EQUITY_OPTION_MULTIPLIER = Decimal("100")


def is_occ_option(symbol: str) -> bool:
    return bool(OCC_OPTION.match(symbol.upper().strip()))


ASSET_CLASS_WORDS = {
    "STK": "STOCK", "STOCK": "STOCK", "STOCKS": "STOCK", "EQUITY": "STOCK", "ETF": "STOCK", "CFD": "STOCK",
    "OPT": "OPTIONS", "OPTION": "OPTIONS", "OPTIONS": "OPTIONS", "EQUITYANDINDEXOPTIONS": "OPTIONS", "FOP": "OPTIONS",
    "FUT": "FUTURES", "FUTURE": "FUTURES", "FUTURES": "FUTURES", "CASH": "FOREX", "FOREX": "FOREX", "FX": "FOREX",
    "CRYPTO": "CRYPTO", "CRYPTOCURRENCY": "CRYPTO", "CMDTY": "COMMODITY", "COMMODITY": "COMMODITY", "COMMODITIES": "COMMODITY",
}


def asset_class_from(value: str | None) -> str | None:
    return ASSET_CLASS_WORDS.get(re.sub(r"[^A-Z]", "", (value or "").upper()))


def is_currency_pair(symbol: str) -> bool:
    letters = re.sub(r"[^A-Z]", "", symbol.upper())[:6]
    return len(letters) == 6 and letters[:3] in CURRENCIES and letters[3:] in CURRENCIES


# --------------------------------------------------------------------- formats


def detect_format(header_keys: set[str]) -> str:
    if {"instrumenttype", "averageprice", "value"} <= header_keys and header_keys & {"rootsymbol", "underlyingsymbol"}:
        return "tastytrade"
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
    "broker_id": {"orderid", "order", "brokerid", "executionid", "execid", "ibexecid", "fillid", "tradeid", "id", "dealid"},
    "multiplier": {"multiplier", "mult", "contractsize", "pointvalue"},
    "asset_class": {"assetclass", "assetcategory", "sectype", "securitytype", "instrumenttype", "assettype"},
    "broker_pnl": {"realizedp/l", "realizedpl", "realizedpnl", "fifopnlrealized", "realisedpnl", "realizedprofit", "netpnl"},
    "currency": {"currency", "currencyprimary"},
}


def parse_generic(rows: list[tuple[int, dict[str, str]]], clock: Clock, result: ParseResult) -> None:
    def pick(row: dict[str, str], name: str) -> str:
        return next((row[alias] for alias in GENERIC_ALIASES[name] if row.get(alias)), "")

    for number, row in rows:
        try:
            quantity = parse_decimal(pick(row, "quantity"))
            side = normalise_side(pick(row, "side")) or ("SELL" if quantity < 0 else "BUY" if quantity > 0 and not pick(row, "side") else None)
            if side is None:
                raise ValueError(f"buy or sell not recognised: {pick(row, 'side')!r}")
            multiplier = parse_decimal(pick(row, "multiplier"), default=None)
            add(result, number, pick(row, "symbol").upper(), clock(pick(row, "time")), side,
                parse_decimal(pick(row, "price")), abs(quantity), abs(parse_decimal(pick(row, "fees"))), pick(row, "broker_id") or None,
                asset_class=asset_class_from(pick(row, "asset_class")),
                point_value=multiplier if multiplier and multiplier > 0 else None,
                currency=pick(row, "currency").upper() or None,
                broker_pnl=parse_decimal(pick(row, "broker_pnl"), default=None))
        except ValueError as problem:
            result.problems.append(RowProblem(number, str(problem)))


IBKR_ASSET_CLASSES = {"STK": "STOCK", "FUT": "FUTURES", "CASH": "FOREX", "OPT": "OPTIONS", "FOP": "OPTIONS",
                      "CRYPTO": "CRYPTO", "CMDTY": "COMMODITY", "CFD": "STOCK", "WAR": "OPTIONS"}


def parse_ibkr_flex(rows: list[tuple[int, dict[str, str]]], clock: Clock, result: ParseResult) -> None:
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
            add(result, number, symbol, clock(when), normalise_side(row.get("buy/sell")) or "",
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


def parse_mt5(rows: list[tuple[int, dict[str, str]]], clock: Clock, result: ParseResult) -> None:
    for number, row in rows:
        kind = row.get("type", "").strip().lower()
        if kind not in {"buy", "sell"}:
            result.skipped += 1  # balance, credit, deposits, summary lines
            continue
        try:
            costs = parse_decimal(row.get("commission")) + parse_decimal(row.get("fee")) + parse_decimal(row.get("swap"))
            asset_class, point_value = mt_asset_hints(row.get("symbol", ""))
            add(result, number, row.get("symbol", "").upper(), clock(row.get("time", "")), kind.upper(),
                parse_decimal(row.get("price")), abs(parse_decimal(row.get("volume"))), -costs, row.get("deal") or None,
                asset_class=asset_class, point_value=point_value,
                broker_pnl=parse_decimal(row.get("profit")) + costs)
        except ValueError as problem:
            result.problems.append(RowProblem(number, str(problem)))


def parse_ninjatrader(rows: list[tuple[int, dict[str, str]]], clock: Clock, result: ParseResult) -> None:
    for number, row in rows:
        try:
            symbol = row.get("instrument", "").upper()
            root = futures_root(symbol)
            add(result, number, symbol, clock(row.get("time", "")), normalise_side(row.get("action")) or "",
                parse_decimal(row.get("price")), abs(parse_decimal(row.get("quantity"))), abs(parse_decimal(row.get("commission"))),
                row.get("id") or row.get("orderid") or None,
                asset_class="FUTURES" if root or re.search(r" \d{2}-\d{2}$", symbol) else None,
                point_value=FUTURES_POINT_VALUES.get(root) if root else None)
        except ValueError as problem:
            result.problems.append(RowProblem(number, str(problem)))


def parse_tradovate(rows: list[tuple[int, dict[str, str]]], clock: Clock, result: ParseResult) -> None:
    """Performance rows are matched round trips: each becomes its buy fill and its sell fill."""
    for number, row in rows:
        try:
            symbol = row.get("symbol", "").upper()
            root = futures_root(symbol)
            quantity = abs(parse_decimal(row.get("qty")))
            bought = clock(row.get("boughttimestamp", ""))
            sold = clock(row.get("soldtimestamp", ""))
            hints = {"asset_class": "FUTURES", "point_value": FUTURES_POINT_VALUES.get(root) if root else None}
            pnl = parse_decimal(row.get("pnl"), default=None)
            buy_pnl, sell_pnl = (pnl, None) if bought > sold else (None, pnl)  # P&L belongs to the closing fill
            add(result, number, symbol, bought, "BUY", parse_decimal(row.get("buyprice")), quantity, Decimal("0"),
                row.get("buyfillid") or None, broker_pnl=buy_pnl, **hints)
            add(result, number, symbol, sold, "SELL", parse_decimal(row.get("sellprice")), quantity, Decimal("0"),
                row.get("sellfillid") or None, broker_pnl=sell_pnl, **hints)
        except ValueError as problem:
            result.problems.append(RowProblem(number, str(problem)))


TASTY_ASSET_CLASSES = {"equity": "STOCK", "equityoption": "OPTIONS", "future": "FUTURES", "futureoption": "OPTIONS",
                       "cryptocurrency": "CRYPTO"}
TASTY_CLOSING_EVENTS = ("expiration", "assignment", "exercise", "cashsettled")


TASTY_PRICE = re.compile(r"@\s*([\d,]*\.?\d+)\s*$")


def parse_tastytrade(rows: list[tuple[int, dict[str, str]]], clock: Clock, result: ParseResult) -> None:
    """History -> Transactions CSV. Trades and receive/deliver events (expirations, assignments,
    stock delivered on assignment) become fills; money movements are skipped.

    Learned from a real export: the fill price is in the description ("... @ 7607.75"); "Value" is
    the cash effect, which for futures is the day's settlement rather than the trade value; and for
    options on futures the Multiplier column says 1, so the contract size is derived from the cash
    value (value / (quantity x price)), or the underlying future's known size."""
    for number, row in rows:
        kind = key(row.get("type", ""))
        if kind not in {"trade", "receivedeliver"}:
            result.skipped += 1
            continue
        try:
            sub_type = key(row.get("subtype", ""))
            action = row.get("action", "").upper()
            asset_class = TASTY_ASSET_CLASSES.get(key(row.get("instrumenttype", "")))
            if kind == "receivedeliver" and sub_type.startswith(TASTY_CLOSING_EVENTS):
                side = "CLOSE"  # expiration/assignment closes the option, whichever way it faced
            elif "BUY" in action or "SELL" in action:
                side = "BUY" if "BUY" in action else "SELL"
            else:
                raise ValueError(f"buy or sell not recognised: {row.get('action', '')!r}")
            quantity = abs(parse_decimal(row.get("quantity")))
            value = abs(parse_decimal(row.get("value")))
            multiplier = parse_decimal(row.get("multiplier"), default=None)
            quoted = TASTY_PRICE.search(row.get("description", ""))
            futures_based = asset_class == "FUTURES" or row.get("underlyingsymbol", "").startswith("/")
            if value and quantity and not futures_based:
                # Stocks and equity options: the cash value is exact (the description rounds to cents).
                price = value / (quantity * (multiplier or Decimal("1")))
            elif quoted:
                # Futures and options on futures: the description holds the fill price.
                price = parse_decimal(quoted.group(1))
            else:
                price = Decimal("0")  # expired worthless / assigned option leg

            underlying = row.get("underlyingsymbol", "").lstrip("./")
            symbol = row.get("symbol", "").upper().lstrip("./")
            if asset_class == "FUTURES":
                root = futures_root(symbol)
                point_value = FUTURES_POINT_VALUES.get(root) if root else None
            elif asset_class == "OPTIONS" and row.get("underlyingsymbol", "").startswith("/"):
                if value and quantity and price:
                    point_value = (value / (quantity * price)).quantize(Decimal("0.0001"))
                else:
                    root = futures_root(underlying)
                    point_value = FUTURES_POINT_VALUES.get(root) if root else None
            else:
                point_value = multiplier if multiplier and multiplier > 0 else None

            fees = -(parse_decimal(row.get("commissions")) + parse_decimal(row.get("fees")))
            add(result, number, symbol, clock(row.get("date", "")), side, price.quantize(Decimal("0.00000001")), quantity,
                fees, row.get("order") or None, asset_class=asset_class, point_value=point_value,
                currency=(row.get("currency") or "USD").upper())
        except ValueError as problem:
            result.problems.append(RowProblem(number, str(problem)))


PARSERS = {
    "generic": parse_generic,
    "ibkr_flex": parse_ibkr_flex,
    "mt5": parse_mt5,
    "ninjatrader": parse_ninjatrader,
    "tradovate": parse_tradovate,
    "tastytrade": parse_tastytrade,
}


def add(result: ParseResult, number: int, symbol: str, executed_at: datetime, side: str, price: Decimal | None,
        quantity: Decimal | None, fees: Decimal | None, broker_id: str | None, **hints) -> None:
    if not symbol:
        raise ValueError("no symbol")
    if side not in {"BUY", "SELL", "CLOSE"}:
        raise ValueError("buy or sell not recognised")
    # Expired and assigned options close at 0; everything else needs a real price.
    if price is None or price < 0 or (price == 0 and side != "CLOSE"):
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


def parse_import(raw: bytes, file_format: str = "auto", tz: tzinfo = UTC, date_order: str = "auto") -> ParseResult:
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
    detected_day_first = detect_day_first(body)
    if date_order in {"mdy", "dmy"}:
        day_first = date_order == "dmy"
    else:
        day_first = bool(detected_day_first)
    result = ParseResult(file_format=chosen)
    if date_order == "auto" and detected_day_first is None and any(SLASH_DATE.search(cell) for row in body[:200] for cell in row):
        result.date_note = ("Every date in this file could be read either way (like 03/04/2026); they were read month first. "
                            "If your broker writes the day first, choose Date order: day first and upload again.")
    PARSERS[chosen](records(header, body, start=header_index + 2), Clock(tz, day_first), result)
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
