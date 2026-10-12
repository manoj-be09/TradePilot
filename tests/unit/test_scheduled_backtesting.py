from datetime import date, datetime, time, timezone
from decimal import Decimal

import pytest

from packages.backtesting.replay import BacktestDataError, run_scheduled_expiry_replay
from packages.market_data.contracts import ExpiredOptionContract, HistoricalBar
from packages.paper_trading.strategies import get_strategy


class ScheduledProvider:
    provider_id = "fixture-feed"

    expiries = [date(2025, 4, 10), date(2025, 4, 17)]
    sessions = [
        date(2025, 4, 8), date(2025, 4, 9), date(2025, 4, 10),
        date(2025, 4, 15), date(2025, 4, 16), date(2025, 4, 17),
    ]

    async def get_expired_option_expiries(self, underlying):
        assert underlying == "NIFTY"
        return self.expiries

    async def get_historical(self, instrument_key, start, end, interval):
        assert instrument_key == "NSE_INDEX|Nifty 50" and interval == "1d"
        return [self.bar(day, Decimal(25012)) for day in self.sessions]

    async def get_expired_option_contracts(self, underlying, expiry):
        assert underlying == "NIFTY"
        return [
            ExpiredOptionContract(
                instrument_key=f"{expiry}|{strike}|CE",
                trading_symbol=f"NIFTY {strike} CE", expiry=expiry,
                strike=Decimal(strike), option_type="CE", lot_size=25,
                tick_size=Decimal("0.05"),
            )
            for strike in (24950, 25000, 25050)
        ]

    async def get_expired_historical(self, instrument_key, start, end, interval):
        assert interval == "1d"
        expiry_text, strike_text, _ = instrument_key.split("|")
        expiry = date.fromisoformat(expiry_text)
        assert expiry == end
        if strike_text == "25000":
            entry, exit_price = (
                (Decimal(100), Decimal(130))
                if expiry == self.expiries[0]
                else (Decimal(100), Decimal(70))
            )
        else:
            entry, exit_price = (
                (Decimal(80), Decimal(60))
                if expiry == self.expiries[0]
                else (Decimal(80), Decimal(90))
            )
        return [self.bar(start, entry), self.bar(end, exit_price)]

    @staticmethod
    def bar(day, price):
        return HistoricalBar(
            timestamp=datetime.combine(day, time.min, timezone.utc),
            open=price, high=price, low=price, close=price, volume=100,
        )


def run(provider, *, start_date=date(2025, 4, 10), end_date=date(2025, 4, 17)):
    strategy = get_strategy("bull_call_spread")
    assert strategy is not None
    coroutine = run_scheduled_expiry_replay(
            provider, strategy, start_date=start_date,
            end_date=end_date, entry_sessions_before_expiry=1,
            lots=1, fee_per_order=Decimal(20),
            slippage_per_unit_per_side=Decimal("0.50"),
    )
    try:
        coroutine.send(None)
    except StopIteration as done:
        return done.value
    pytest.fail("Fixture provider unexpectedly suspended")


def test_scheduled_replay_uses_sessions_costs_and_closed_trade_drawdown():
    first = run(ScheduledProvider())
    second = run(ScheduledProvider())
    assert first.trade_count == 2
    assert first.provider == "fixture-feed"
    assert [(trade.entry_date, trade.expiry) for trade in first.trades] == [
        (date(2025, 4, 9), date(2025, 4, 10)),
        (date(2025, 4, 16), date(2025, 4, 17)),
    ]
    assert [trade.estimated_cost for trade in first.trades] == [Decimal(130)] * 2
    assert first.gross_pnl == Decimal("250.00")
    assert first.net_pnl == Decimal("-10.00")
    assert first.max_closed_trade_drawdown == Decimal("1130.00")
    assert first.winning_trades == 1
    assert first.observed_input_digest == second.observed_input_digest
    assert first.run_id != second.run_id


def test_scheduled_replay_fails_instead_of_silently_skipping_missing_session():
    provider = ScheduledProvider()
    provider.sessions = provider.sessions[:-1]
    with pytest.raises(BacktestDataError, match="sessions are incomplete"):
        run(provider)


def test_scheduled_replay_limits_provider_request_count():
    provider = ScheduledProvider()
    provider.expiries = [date(2025, 4, day) for day in (3, 10, 17, 24, 30)]
    with pytest.raises(BacktestDataError, match="at most four expiries"):
        run(provider, start_date=date(2025, 4, 1), end_date=date(2025, 4, 30))


def test_scheduled_replay_rejects_duplicate_sessions_and_attributes_provider():
    provider = ScheduledProvider()
    provider.sessions = [*provider.sessions, provider.sessions[0]]
    with pytest.raises(BacktestDataError, match="duplicate daily candles"):
        run(provider)
    provider.sessions = ScheduledProvider.sessions
    provider.provider_id = "second-feed"
    assert run(provider).provider == "second-feed"
