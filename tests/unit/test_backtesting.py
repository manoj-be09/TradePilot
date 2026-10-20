from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

import pytest

from packages.backtesting.replay import BacktestDataError, run_single_expiry_replay
from packages.market_data.contracts import ExpiredOptionContract, HistoricalBar
from packages.paper_trading.strategies import get_strategy


class FakeHistoricalProvider:
    provider_id = "fixture-feed"

    def __init__(self, expiry: date, entry: date, exit: date) -> None:
        self.expiry = expiry
        self.entry = entry
        self.exit = exit
        self.contracts = [
            ExpiredOptionContract(
                instrument_key=f"NSE_FO|{strike}{option_type}|{expiry:%d-%m-%Y}",
                trading_symbol=f"NIFTY {strike} {option_type}",
                expiry=expiry,
                strike=Decimal(strike),
                option_type=option_type,
                lot_size=25,
                tick_size=Decimal("0.05"),
            )
            for strike in (24900, 24950, 25000, 25050, 25100)
            for option_type in ("CE", "PE")
        ]

    async def get_historical(self, instrument_key, start, end, interval):
        assert instrument_key == "NSE_INDEX|Nifty 50"
        assert interval == "1d"
        return [
            HistoricalBar(
                timestamp=datetime.combine(self.entry - timedelta(days=1), time.min, timezone.utc),
                open=25000,
                high=25100,
                low=24900,
                close=25012,
                volume=1000,
            ),
            HistoricalBar(
                timestamp=datetime.combine(self.entry, time.min, timezone.utc),
                open=25012,
                high=25200,
                low=25000,
                close=25150,
                volume=1000,
            ),
        ]

    async def get_expired_option_contracts(self, underlying, expiry):
        assert underlying == "NIFTY"
        return self.contracts

    async def get_expired_historical(self, instrument_key, start, end, interval):
        contract = next(item for item in self.contracts if item.instrument_key == instrument_key)
        if contract.strike == Decimal(25000):
            entry_price, exit_price = Decimal(100), Decimal(130)
        elif contract.strike == Decimal(25050):
            entry_price, exit_price = Decimal(80), Decimal(60)
        else:
            entry_price = exit_price = Decimal(10)
        return [
            HistoricalBar(
                timestamp=datetime.combine(self.entry, time.min, timezone.utc),
                open=entry_price,
                high=entry_price,
                low=entry_price,
                close=entry_price,
                volume=100,
                open_interest=500,
            ),
            HistoricalBar(
                timestamp=datetime.combine(self.exit, time.min, timezone.utc),
                open=exit_price,
                high=exit_price,
                low=exit_price,
                close=exit_price,
                volume=100,
                open_interest=600,
            ),
        ]


def _finish_immediate(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as done:
        return done.value
    pytest.fail("Fixture provider unexpectedly suspended")


def test_single_expiry_replay_uses_prior_spot_and_historical_option_ohlc():
    expiry = date.today() - timedelta(days=7)
    entry = expiry - timedelta(days=5)
    provider = FakeHistoricalProvider(expiry, entry, expiry)
    strategy = get_strategy("bull_call_spread")
    assert strategy is not None

    result = _finish_immediate(
        run_single_expiry_replay(
            provider,
            strategy,
            expiry=expiry,
            entry_date=entry,
            exit_date=expiry,
            lots=1,
            fee_per_order=Decimal("20"),
            slippage_per_unit_per_side=Decimal("0.50"),
        )
    )

    assert result.mode == "historical_single_expiry_replay"
    assert result.provider == "fixture-feed"
    assert result.entry_reference_spot == Decimal("25012")
    assert result.reference_strike == Decimal("25000")
    assert result.gross_pnl == Decimal("1250.00")
    assert result.estimated_cost == Decimal("130.00")
    assert result.net_pnl == Decimal("1120.00")
    assert result.return_on_risk_net_pct == Decimal("177.78")
    assert result.maximum_loss_at_entry == Decimal("500.00")
    assert [(leg.side, leg.strike) for leg in result.legs] == [
        ("BUY", Decimal("25000")),
        ("SELL", Decimal("25050")),
    ]
    assert any("not repeated signal-based" in item for item in result.assumptions)

    same = _finish_immediate(
        run_single_expiry_replay(
            provider, strategy, expiry=expiry, entry_date=entry, exit_date=expiry,
            lots=1, fee_per_order=Decimal("20"),
            slippage_per_unit_per_side=Decimal("0.50"),
        )
    )
    changed_cost = _finish_immediate(
        run_single_expiry_replay(
            provider, strategy, expiry=expiry, entry_date=entry, exit_date=expiry,
            lots=1, fee_per_order=Decimal("21"),
            slippage_per_unit_per_side=Decimal("0.50"),
        )
    )
    assert result.observed_input_digest is not None
    assert len(result.observed_input_digest) == 64
    assert same.observed_input_digest == result.observed_input_digest
    assert same.run_id != result.run_id
    assert changed_cost.observed_input_digest != result.observed_input_digest


def test_ratio_strategy_replay_scales_leg_quantities():
    expiry = date.today() - timedelta(days=7)
    entry = expiry - timedelta(days=5)
    provider = FakeHistoricalProvider(expiry, entry, expiry)
    strategy = get_strategy("strip")
    assert strategy is not None

    result = _finish_immediate(
        run_single_expiry_replay(
            provider, strategy, expiry=expiry, entry_date=entry,
            exit_date=expiry, lots=1,
        )
    )

    assert [(leg.side, leg.quantity_units) for leg in result.legs] == [
        ("BUY", 50),
        ("BUY", 25),
    ]
    assert result.maximum_loss_at_entry > 0

def test_replay_rejects_duplicate_or_invalid_daily_option_bars():
    expiry = date.today() - timedelta(days=7)
    entry = expiry - timedelta(days=5)
    strategy = get_strategy("bull_call_spread")
    assert strategy is not None

    class BadProvider(FakeHistoricalProvider):
        duplicate = True

        async def get_expired_historical(self, instrument_key, start, end, interval):
            bars = await super().get_expired_historical(instrument_key, start, end, interval)
            return [*bars, bars[0]] if self.duplicate else [
                bars[0].model_copy(update={"high": Decimal("1")}), bars[1],
            ]

    provider = BadProvider(expiry, entry, expiry)
    with pytest.raises(BacktestDataError, match="duplicate daily candles"):
        _finish_immediate(run_single_expiry_replay(
            provider, strategy, expiry=expiry, entry_date=entry, exit_date=expiry, lots=1,
        ))
    provider.duplicate = False
    with pytest.raises(BacktestDataError, match="invalid daily candle"):
        _finish_immediate(run_single_expiry_replay(
            provider, strategy, expiry=expiry, entry_date=entry, exit_date=expiry, lots=1,
        ))


def test_replay_digest_identifies_the_data_provider():
    expiry = date.today() - timedelta(days=7)
    entry = expiry - timedelta(days=5)
    strategy = get_strategy("bull_call_spread")
    assert strategy is not None
    first = FakeHistoricalProvider(expiry, entry, expiry)
    second = FakeHistoricalProvider(expiry, entry, expiry)
    second.provider_id = "other-feed"
    a = _finish_immediate(run_single_expiry_replay(
        first, strategy, expiry=expiry, entry_date=entry, exit_date=expiry, lots=1,
    ))
    b = _finish_immediate(run_single_expiry_replay(
        second, strategy, expiry=expiry, entry_date=entry, exit_date=expiry, lots=1,
    ))
    assert a.provider == "fixture-feed" and b.provider == "other-feed"
    assert a.observed_input_digest != b.observed_input_digest
