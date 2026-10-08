from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from tradzlog_api.services.imports import ImportedExecution, ImportFormatError, futures_root, parse_decimal, parse_import
from tradzlog_api.services.trade_import import default_instrument, fingerprints, from_row, to_row, walk
from tradzlog_db.models import AssetClass, Direction, ExecutionType

FIXTURES = Path(__file__).parent / "fixtures" / "imports"
NEW_YORK = ZoneInfo("America/New_York")
D = Decimal


def parsed(name: str):
    return parse_import((FIXTURES / name).read_bytes(), "auto", NEW_YORK)


# --------------------------------------------------------------------- parsers


def test_ibkr_flex_trades() -> None:
    result = parsed("ibkr_flex_trades.csv")
    assert result.file_format == "ibkr_flex" and result.skipped == 1 and not result.problems  # the "(Ca.)" cancellation
    first, _, close, es_short, _, fx = result.executions
    assert (first.symbol, first.side, first.quantity, first.price, first.fees) == ("AAPL", "BUY", D("100"), D("180.50"), D("1.00"))
    assert first.executed_at == datetime(2026, 3, 2, 14, 31, 5, tzinfo=UTC)  # 09:31:05 New York (EST)
    assert close.side == "SELL" and close.quantity == D("150") and close.broker_pnl == D("347.00")
    assert (es_short.asset_class, es_short.point_value) == ("FUTURES", D("50"))
    assert (fx.symbol, fx.quantity, fx.point_value, fx.asset_class) == ("EURUSD", D("0.2"), D("100000"), "FOREX")  # units -> lots


def test_mt5_deals_csv_skips_balance_rows_and_nets_costs() -> None:
    result = parsed("mt5_deals.csv")
    assert result.file_format == "mt5" and result.skipped == 1
    buy, sell, gold_sell, _ = result.executions
    assert (buy.symbol, buy.quantity, buy.point_value, buy.fees, buy.broker_id) == ("EURUSD", D("0.50"), D("100000"), D("3.50"), "1001")
    assert sell.fees == D("4.30") and sell.broker_pnl == D("95.70")  # profit 100 less commission 3.50 and swap 0.80
    assert (gold_sell.asset_class, gold_sell.point_value) == ("COMMODITY", D("100"))


def test_mt5_html_report_utf16() -> None:
    html = """<html><body><table>
      <tr><th colspan="14">Positions</th></tr>
      <tr><td>Time</td><td>Position</td><td>Symbol</td></tr>
      <tr><td colspan="14">Deals</td></tr>
      <tr><td>Time</td><td>Deal</td><td>Symbol</td><td>Type</td><td>Direction</td><td>Volume</td><td>Price</td>
          <td>Order</td><td>Commission</td><td>Fee</td><td>Swap</td><td>Profit</td><td>Balance</td><td>Comment</td></tr>
      <tr><td>2026.03.02 10:15:30</td><td>1001</td><td>GBPUSD</td><td>buy</td><td>in</td><td>1.00</td><td>1.27000</td>
          <td>1</td><td>-7.00</td><td>0.00</td><td>0.00</td><td>0.00</td><td>9993.00</td><td></td></tr>
      <tr><td>2026.03.02 11:15:30</td><td>1002</td><td>GBPUSD</td><td>sell</td><td>out</td><td>1.00</td><td>1.27100</td>
          <td>2</td><td>-7.00</td><td>0.00</td><td>0.00</td><td>100.00</td><td>10086.00</td><td></td></tr>
      <tr><td colspan="12"></td><td>10086.00</td><td></td></tr>
    </table></body></html>"""
    result = parse_import(html.encode("utf-16"), "auto", UTC)
    assert result.file_format == "mt5" and [fill.side for fill in result.executions] == ["BUY", "SELL"]
    assert result.executions[1].broker_pnl == D("93.00")


def test_ninjatrader_and_tradovate() -> None:
    nt = parsed("ninjatrader_executions.csv")
    assert nt.file_format == "ninjatrader"
    assert [(fill.side, fill.point_value, fill.fees) for fill in nt.executions] == [("BUY", D("20"), D("2.04")), ("SELL", D("20"), D("2.04"))]
    assert nt.executions[0].executed_at == datetime(2026, 3, 2, 14, 31, 22, tzinfo=UTC)  # 9:31:22 AM New York

    tv = parsed("tradovate_performance.csv")
    assert tv.file_format == "tradovate" and len(tv.executions) == 4
    assert {fill.point_value for fill in tv.executions} == {D("5")}
    # The second round trip was a short (sold first): its P&L belongs to the closing buy.
    assert [(fill.side, fill.broker_pnl) for fill in tv.executions[2:]] == [("BUY", D("-10.00")), ("SELL", None)]


def test_generic_csv_with_signed_quantities_and_row_problems() -> None:
    raw = b"Ticker,Date/Time,Qty,Price,Commission\nTSLA,2026-03-02 10:00,10,200,1\nTSLA,2026-03-02 11:00,-10,210,1\nTSLA,someday,5,1,0\n"
    result = parse_import(raw, "auto", UTC)
    assert result.file_format == "generic"
    assert [(fill.side, fill.quantity) for fill in result.executions] == [("BUY", D("10")), ("SELL", D("10"))]
    assert len(result.problems) == 1 and "date/time" in result.problems[0].reason and result.problems[0].row_number == 4


def test_unreadable_files_are_rejected_clearly() -> None:
    with pytest.raises(ImportFormatError, match="empty"):
        parse_import(b"   ", "auto")
    with pytest.raises(ImportFormatError, match="No trades"):
        parse_import(b"just,some,words\n", "auto")


def test_number_and_symbol_helpers() -> None:
    assert parse_decimal("$(1,234.50)") == D("-1234.50")
    assert parse_decimal("") == D("0") and parse_decimal("", default=None) is None
    assert [futures_root(s) for s in ("ESZ6", "MNQH27", "NQ 03-26", "AAPL")] == ["ES", "MNQ", "NQ", None]


# --------------------------------------------------------------------- round trips

T0 = datetime(2026, 3, 2, 14, 0, tzinfo=UTC)


def fill(side: str, qty: str, price: str, minutes: int, fees: str = "0", symbol: str = "AAPL") -> ImportedExecution:
    return ImportedExecution(symbol=symbol, executed_at=T0 + timedelta(minutes=minutes), side=side, price=D(price),
                             quantity=D(qty), fees=D(fees), row_number=minutes)


def legs(trade) -> list[tuple[str, str]]:
    return [(leg.type.value, format(leg.quantity.normalize(), "f")) for leg in trade.legs]


def plan(fills, open_trade=None):
    return walk([(item, f"fp{index}") for index, item in enumerate(fills)], open_trade)


def test_scale_in_partial_exit_and_close() -> None:
    (trade,) = plan([fill("BUY", "100", "10", 0), fill("BUY", "50", "11", 1), fill("SELL", "60", "12", 2), fill("SELL", "90", "13", 3)])
    assert trade.direction == Direction.LONG and trade.closed_at == T0 + timedelta(minutes=3)
    assert legs(trade) == [("ENTRY", "100"), ("ADD", "50"), ("PARTIAL_EXIT", "60"), ("EXIT", "90")]


def test_short_trade_and_open_position_left_open() -> None:
    short, still_open = plan([fill("SELL", "2", "100", 0), fill("BUY", "2", "90", 1), fill("BUY", "5", "95", 2)])
    assert short.direction == Direction.SHORT and short.closed_at is not None and legs(short) == [("ENTRY", "2"), ("EXIT", "2")]
    assert still_open.direction == Direction.LONG and still_open.closed_at is None


def test_flip_closes_then_opens_the_other_way_splitting_fees() -> None:
    long_trade, short_trade = plan([fill("BUY", "100", "10", 0, fees="1"), fill("SELL", "150", "12", 1, fees="3")])
    assert legs(long_trade) == [("ENTRY", "100"), ("EXIT", "100")] and long_trade.legs[1].fees == D("2")
    assert short_trade.direction == Direction.SHORT and legs(short_trade) == [("ENTRY", "50")] and short_trade.legs[0].fees == D("1")
    assert long_trade.legs[1].fingerprint == short_trade.legs[0].fingerprint  # both halves of one source row


def test_fills_are_ordered_by_time_not_file_order() -> None:
    (trade,) = plan([fill("SELL", "1", "12", 5), fill("BUY", "1", "10", 1)])
    assert trade.direction == Direction.LONG and legs(trade) == [("ENTRY", "1"), ("EXIT", "1")]


def test_import_continues_an_open_trade() -> None:
    existing = SimpleNamespace(direction=Direction.LONG, executions=[SimpleNamespace(type=ExecutionType.ENTRY, quantity=D("100"))])
    (continued,) = plan([fill("SELL", "100", "12", 10)], open_trade=existing)
    assert continued.existing is existing and legs(continued) == [("EXIT", "100")]


def test_fingerprints_are_stable_and_tell_identical_rows_apart() -> None:
    rows = [fill("BUY", "1", "10", 0), fill("BUY", "1", "10", 0)]
    first, second = fingerprints("acct", rows)
    assert first != second
    assert fingerprints("acct", rows) == [first, second]  # same file again -> same prints (so: duplicates)
    assert fingerprints("other-account", rows)[0] != first


def test_pending_rows_round_trip_through_json() -> None:
    original = fill("SELL", "1.5", "101.25", 3, fees="0.75")
    assert from_row(to_row(original)) == original


def test_new_instrument_defaults() -> None:
    assert default_instrument(fill("BUY", "1", "1", 0, symbol="MESZ6")).point_value == D("5")
    forex = default_instrument(fill("BUY", "1", "1", 0, symbol="GBPJPY"))
    assert (forex.asset_class, forex.point_value, forex.needs_review) == (AssetClass.FOREX, D("100000"), False)
    unknown = default_instrument(ImportedExecution(symbol="US30", executed_at=T0, side="BUY", price=D("1"), quantity=D("1"), asset_class="FUTURES"))
    assert unknown.needs_review and unknown.point_value == D("1")
    assert not default_instrument(fill("BUY", "1", "1", 0, symbol="AAPL")).needs_review
