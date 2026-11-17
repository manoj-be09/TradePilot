"""The live dip spread follows the research rule: signal, expiry, strikes, one window, one trade."""

import asyncio
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from test_night_condor import factory  # noqa: F401

from apps.dip_runner.main import EXIT_POLICY, NAME, DipRunner, choose_strikes, fell_enough
from apps.night_runner.main import setup
from packages.backtesting.history_backtest import _IST
from packages.market_data.contracts import (
    ExchangeSession,
    HistoricalBar,
    OptionChain,
    OptionContract,
    OptionContractQuote,
    Quote,
)

_TODAY = date(2025, 3, 3)  # Monday
_NEAR, _NEXT = date(2025, 3, 6), date(2025, 3, 13)  # 3 and 8 sessions after today


class Provider:
    def __init__(self, spot: Decimal, five_back: Decimal) -> None:
        self.spot, self.five_back = spot, five_back

    async def get_nfo_session(self, day: date):
        if day.weekday() >= 5:
            return None
        return ExchangeSession("NFO", datetime.combine(day, time(9, 15), tzinfo=_IST),
                               datetime.combine(day, time(15, 30), tzinfo=_IST))

    async def get_option_contracts(self, underlying: str, expiry=None):
        return [OptionContract(instrument_key=f"NSE_FO|{e}CE", trading_symbol="X", expiry=e,
                               strike=Decimal(22000), option_type="CE", lot_size=65,
                               tick_size=Decimal("0.05")) for e in (_NEAR, _NEXT)]

    async def get_option_chain(self, underlying: str, expiry: date):
        at = datetime.combine(_TODAY, time(15, 15), tzinfo=_IST)
        contracts = [OptionContractQuote(
            instrument_token=f"NSE_FO|CE{strike}", expiry=expiry, strike=Decimal(strike),
            option_type="CE", quote=Quote(instrument_token=f"NSE_FO|CE{strike}", timestamp=at,
                                          last_price=Decimal(100)))
            for strike in range(21500, 23050, 50)]
        return OptionChain(underlying="NIFTY", underlying_spot=self.spot, expiry=expiry,
                           as_of=at, contracts=contracts)

    async def get_historical(self, key, start, end, interval):
        days = [date(2025, 2, d) for d in (24, 25, 26, 27, 28)]
        closes = [self.five_back] + [Decimal(22300)] * 4
        return [HistoricalBar(timestamp=datetime.combine(d, time(0), tzinfo=_IST), open=c,
                              high=c, low=c, close=c, volume=1) for d, c in zip(days, closes,
                                                                                 strict=True)]


def _runner(factory, provider, opened):  # noqa: F811
    async def open_trade(body, user, session, key, provider, **options):
        opened.append((body, key, options))

    return DipRunner(factory, provider, "dip@tradepilot.test", open_trade)


def test_signal_needs_a_fall_of_at_least_two_and_a_half_percent_over_five_sessions():
    closes = [Decimal(22600)] + [Decimal(22000)] * 4
    assert fell_enough(Decimal(22000), closes)  # -2.65%
    assert not fell_enough(Decimal(22100), closes)  # -2.2%
    assert not fell_enough(Decimal(20000), closes[1:])  # too little history


def test_runner_buys_the_atm_two_percent_call_spread_of_the_first_expiry_five_sessions_out(
    factory, tmp_path  # noqa: F811
):
    setup(factory, tmp_path / "dip.json", "dip@tradepilot.test")
    opened: list = []
    runner = _runner(factory, Provider(Decimal(22000), Decimal(22600)), opened)
    at = datetime.combine(_TODAY, time(15, 16), tzinfo=_IST)
    assert asyncio.run(runner.cycle(at - timedelta(minutes=5))) == "WAITING"
    assert asyncio.run(runner.cycle(at)) == "OPENED"
    assert asyncio.run(runner.cycle(at + timedelta(minutes=1))) == "DONE"
    [(body, key, options)] = opened
    assert key == "dip_v1_20250313" and body.expiry == _NEXT and body.lots == 1
    assert [(leg.side, leg.strike) for leg in body.custom_legs] == [
        ("BUY", Decimal(22000)), ("SELL", Decimal(22450))]
    assert options == {"exit_policy": EXIT_POLICY, "custom_name": NAME} and "not promoted" in NAME


def test_runner_skips_days_without_a_fall_and_outside_the_window(factory, tmp_path):  # noqa: F811
    setup(factory, tmp_path / "dip.json", "dip@tradepilot.test")
    opened: list = []
    calm = _runner(factory, Provider(Decimal(22200), Decimal(22600)), opened)
    at = datetime.combine(_TODAY, time(15, 16), tzinfo=_IST)
    assert asyncio.run(calm.cycle(at)) == "NO_SIGNAL"
    late = _runner(factory, Provider(Decimal(22000), Decimal(22600)), opened)
    assert asyncio.run(late.cycle(
        datetime.combine(_TODAY, time(15, 26), tzinfo=_IST))) == "WINDOW_PASSED"
    assert opened == []


def test_no_sold_leg_means_no_trade():
    at = datetime.combine(_TODAY, time(15, 15), tzinfo=_IST)
    chain = asyncio.run(Provider(Decimal(22000), Decimal(22600)).get_option_chain("NIFTY", _NEXT))
    missing = chain.model_copy(update={"contracts": [c for c in chain.contracts
                                                     if c.strike != Decimal(22450)]})
    assert choose_strikes(chain) == (Decimal(22000), Decimal(22450))
    assert choose_strikes(missing) is None and at
