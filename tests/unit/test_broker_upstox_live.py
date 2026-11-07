"""Live-trading foundations: order states, hedge-first legs, pre-trade controls, halt, reads."""

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from packages.broker_upstox.controls import LegQuote, LiveEntryContext, LiveLimits, check_live_entry
from packages.broker_upstox.halt import live_halted, set_live_halt
from packages.broker_upstox.orders import (
    FilledLeg,
    IllegalOrderTransition,
    LiveOrderRequest,
    OrderState,
    advance,
    client_tag,
    flatten_plan,
    hedge_first,
    round_to_tick,
    state_from_broker,
)
from packages.broker_upstox.portfolio import PortfolioReader, reconcile_positions
from packages.core.risk import AccountRiskState, EntryIntent, RiskLimits
from packages.database.base import Base
from packages.database.models import LiveHaltEvent
from packages.market_data.contracts import MarketDataProviderError

S = OrderState
DEPLOYMENT = UUID("12345678-1234-5678-1234-567812345678")
NOW = datetime(2026, 11, 9, 4, 0, tzinfo=timezone.utc)
CE, PE = "NSE_FO|1001", "NSE_FO|1002"


# --- state machine ---------------------------------------------------------------------

@pytest.mark.parametrize("path", [
    [S.NEW, S.SENT, S.ACKNOWLEDGED, S.PARTIAL, S.PARTIAL, S.FILLED],
    [S.NEW, S.SENT, S.UNKNOWN, S.FILLED],
    [S.NEW, S.SENT, S.UNKNOWN, S.REJECTED],
    [S.NEW, S.SENT, S.ACKNOWLEDGED, S.CANCELLED],
    [S.NEW, S.SENT, S.REJECTED],
])
def test_legal_paths(path):
    state = path[0]
    for nxt in path[1:]:
        state = advance(state, nxt)
    assert state == path[-1]


@pytest.mark.parametrize("current,new", [
    (S.NEW, S.FILLED),          # nothing fills before it is sent
    (S.UNKNOWN, S.SENT),        # an unknown order is never re-sent
    (S.FILLED, S.CANCELLED),    # terminal states stay put
    (S.FILLED, S.FILLED),
    (S.CANCELLED, S.ACKNOWLEDGED),
    (S.PARTIAL, S.ACKNOWLEDGED),
    (S.PARTIAL, S.REJECTED),
])
def test_illegal_transitions(current, new):
    with pytest.raises(IllegalOrderTransition):
        advance(current, new)


@pytest.mark.parametrize("status,qty,filled,expected", [
    ("open", 65, 0, S.ACKNOWLEDGED),
    ("put order req received", 65, 0, S.ACKNOWLEDGED),
    ("open", 130, 65, S.PARTIAL),
    ("Complete", 65, 65, S.FILLED),
    ("complete", 130, 65, S.UNKNOWN),       # says complete but is not fully filled
    ("cancelled", 130, 65, S.CANCELLED),
    ("rejected", 65, 0, S.REJECTED),
    ("rejected", 65, 65, S.UNKNOWN),
    ("something new", 65, 0, S.UNKNOWN),    # unrecognised status fails closed
    ("open", 65, 70, S.UNKNOWN),
])
def test_broker_status_mapping(status, qty, filled, expected):
    assert state_from_broker(status, qty, filled) == expected


def test_client_tag_is_deterministic_and_short():
    tag = client_tag(DEPLOYMENT, 3, 2)
    assert tag == client_tag(DEPLOYMENT, 3, 2) != client_tag(DEPLOYMENT, 3, 3)
    assert len(tag) <= 40


# --- legs ------------------------------------------------------------------------------

def _leg(key, side, qty=65, price="100.00"):
    return LiveOrderRequest(key, side, qty, Decimal(price), client_tag(DEPLOYMENT, 0, 0))


def test_hedge_first_puts_buys_before_sells_and_keeps_order():
    legs = [_leg("a", "SELL"), _leg("b", "BUY"), _leg("c", "SELL"), _leg("d", "BUY")]
    assert [leg.instrument_key for leg in hedge_first(legs)] == ["b", "d", "a", "c"]


def test_flatten_plan_closes_only_filled_legs_shorts_first():
    plan = flatten_plan([
        FilledLeg(CE, "BUY", 65, Decimal("100")),
        FilledLeg(PE, "SELL", 65, Decimal("50")),
        FilledLeg("NSE_FO|1003", "BUY", 0, Decimal("10")),
    ], deployment_id=DEPLOYMENT, attempt=1, slippage_pct=Decimal("1"))
    assert [(o.instrument_key, o.side, o.quantity) for o in plan] == [
        (PE, "BUY", 65), (CE, "SELL", 65)]
    assert plan[0].limit_price == Decimal("50.50")
    assert plan[1].limit_price == Decimal("99.00")


def test_round_to_tick_stays_marketable():
    assert round_to_tick(Decimal("10.01"), "BUY") == Decimal("10.05")
    assert round_to_tick(Decimal("10.04"), "SELL") == Decimal("10.00")
    assert round_to_tick(Decimal("0.01"), "SELL") == Decimal("0.05")


# --- pre-trade controls ----------------------------------------------------------------

LIMITS = LiveLimits(
    account=RiskLimits(max_open_trades=2, max_trade_risk_pct=2, max_portfolio_risk_pct=10,
                       daily_loss_limit_pct=5),
    max_lots_per_order=1, max_lots_per_day=2, max_orders_per_minute=4, freeze_quantity=1800,
    max_slippage_pct=Decimal("2"), max_quote_age=timedelta(seconds=5),
)


def _context(**changes):
    base = dict(
        halted=False,
        account=AccountRiskState(initial_cash=Decimal("200000"), cash_balance=Decimal("200000"),
                                 kill_switch_enabled=False, open_trade_max_losses=(),
                                 realized_pnl_today=Decimal(0)),
        lot_size=65,
        quotes={CE: LegQuote(Decimal("99"), Decimal("100"), NOW - timedelta(seconds=1)),
                PE: LegQuote(Decimal("49"), Decimal("50"), NOW - timedelta(seconds=1))},
        now=NOW, orders_last_minute=0, lots_today=0,
        margin_required=Decimal("20000"), available_funds=Decimal("50000"),
    )
    base.update(changes)
    return LiveEntryContext(**base)


SPREAD = [_leg(CE, "BUY", price="100.00"), _leg(PE, "SELL", price="49.00")]
INTENT = EntryIntent(max_loss=Decimal("3000"), entry_cash_flow=Decimal("-3315"))


def _codes(legs=SPREAD, intent=INTENT, **changes):
    return [r.code for r in check_live_entry(legs, intent, _context(**changes), LIMITS)]


def test_a_clean_entry_passes():
    assert _codes() == []


@pytest.mark.parametrize("changes,code", [
    (dict(halted=True), "LIVE_HALTED"),
    (dict(lots_today=2), "MAX_LOTS_PER_DAY"),
    (dict(orders_last_minute=3), "ORDER_RATE"),
    (dict(margin_required=None), "MARGIN_UNKNOWN"),
    (dict(available_funds=None), "MARGIN_UNKNOWN"),
    (dict(margin_required=Decimal("60000")), "MARGIN"),
    (dict(quotes={PE: LegQuote(Decimal("49"), Decimal("50"), NOW)}), "QUOTE_INVALID"),
    (dict(quotes={CE: LegQuote(Decimal("101"), Decimal("100"), NOW),
                  PE: LegQuote(Decimal("49"), Decimal("50"), NOW)}), "QUOTE_INVALID"),
    (dict(quotes={CE: LegQuote(Decimal("99"), Decimal("100"), None),
                  PE: LegQuote(Decimal("49"), Decimal("50"), NOW)}), "QUOTE_STALE"),
    (dict(quotes={CE: LegQuote(Decimal("99"), Decimal("100"), NOW - timedelta(seconds=6)),
                  PE: LegQuote(Decimal("49"), Decimal("50"), NOW)}), "QUOTE_STALE"),
])
def test_context_failures(changes, code):
    assert code in _codes(**changes)


@pytest.mark.parametrize("legs,code", [
    ([], "NO_LEGS"),
    ([_leg(CE, "BUY", qty=60)], "LOT_MULTIPLE"),
    ([_leg(CE, "BUY", qty=130)], "MAX_LOTS_PER_ORDER"),
    ([_leg(CE, "BUY", price="100.03")], "TICK_SIZE"),
    ([_leg(CE, "BUY", price="0")], "TICK_SIZE"),
    ([_leg(CE, "BUY", price="102.05")], "LIMIT_PRICE"),
    ([_leg(PE, "SELL", price="48.00")], "LIMIT_PRICE"),
])
def test_leg_failures(legs, code):
    assert code in _codes(legs=legs)


def test_freeze_quantity():
    limits = LiveLimits(**{**LIMITS.__dict__, "max_lots_per_order": 50, "max_lots_per_day": 50})
    legs = [_leg(CE, "BUY", qty=65 * 28)]
    codes = [r.code for r in check_live_entry(legs, INTENT, _context(), limits)]
    assert codes == ["FREEZE_QUANTITY"]


def test_account_limits_apply_to_live_capital():
    assert "PER_TRADE_LIMIT" in _codes(intent=EntryIntent(Decimal("4001"), Decimal("-4001")))
    losing = _context().account.__dict__ | {"realized_pnl_today": Decimal("-9000")}
    assert "DAILY_LOSS_BUDGET" in _codes(account=AccountRiskState(**losing))


# --- kill switch -----------------------------------------------------------------------

def _session():
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine, tables=[LiveHaltEvent.__table__])
    return sessionmaker(engine)()


def test_halt_fails_closed_and_latest_event_wins():
    session = _session()
    assert live_halted(session)  # no event yet
    set_live_halt(session, halted=False, actor="owner", reason="pilot approved")
    assert not live_halted(session)
    set_live_halt(session, halted=True, actor="owner", reason="stop")
    assert live_halted(session)
    with pytest.raises(ValueError):
        set_live_halt(session, halted=False, actor="owner", reason=" ")


def test_halt_reads_as_halted_when_the_table_is_missing():
    engine = create_engine("sqlite://")
    assert live_halted(sessionmaker(engine)())


# --- read-only positions and funds -----------------------------------------------------

def _reader(payloads, seen):
    def handler(request):
        seen.append((request.method, request.url.path))
        return httpx.Response(200, json={"status": "success", "data": payloads[request.url.path]})
    return PortfolioReader("token", transport=httpx.MockTransport(handler))


def test_reads_fo_positions_and_funds_with_get_only():
    seen = []
    reader = _reader({
        "/v2/portfolio/short-term-positions": [
            {"instrument_token": CE, "quantity": 65},
            {"instrument_token": CE, "quantity": -65},
            {"instrument_token": PE, "quantity": -65},
            {"instrument_token": "NSE_EQ|INE002A01018", "quantity": 10},
        ],
        "/v2/user/get-funds-and-margin": {"equity": {"available_margin": 123456.5}},
    }, seen)
    assert asyncio.run(reader.fo_positions()) == {PE: -65}
    assert asyncio.run(reader.available_margin()) == Decimal("123456.5")
    assert {method for method, _ in seen} == {"GET"}


def test_malformed_broker_rows_fail_closed():
    reader = _reader({"/v2/portfolio/short-term-positions": [{"quantity": 65}],
                      "/v2/user/get-funds-and-margin": {"equity": {}}}, [])
    with pytest.raises(MarketDataProviderError):
        asyncio.run(reader.fo_positions())
    with pytest.raises(MarketDataProviderError):
        asyncio.run(reader.available_margin())


def test_reconcile_lists_every_difference():
    diffs = reconcile_positions({CE: 65, PE: -65}, {CE: 65, "NSE_FO|9": 65})
    assert [(d.instrument_key, d.broker_quantity, d.expected_quantity) for d in diffs] == [
        (PE, -65, 0), ("NSE_FO|9", 0, 65)]
    assert reconcile_positions({CE: 65}, {CE: 65}) == []


def test_portfolio_reader_refuses_any_non_get_call():
    reader = PortfolioReader("token", transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    with pytest.raises(MarketDataProviderError):
        asyncio.run(reader._send("POST", "/v2/order/place", body={}))
