from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from packages.market_data.contracts import HistoricalBar
from packages.paper_trading.equity import STRATEGIES, ReplayTrade
from packages.regime.outlook import describe_market
from packages.regime.shadow_fit import compare_shadow_fit

IST = ZoneInfo("Asia/Kolkata")


def bars(count: int = 760) -> list[HistoricalBar]:
    start = datetime(2024, 1, 1, tzinfo=IST)
    result = []
    for index in range(count):
        close = Decimal(20000 + index * 3 + index % 7)
        result.append(HistoricalBar(
            timestamp=start + timedelta(days=index), open=close,
            high=close + 10, low=close - 10, close=close, volume=100,
        ))
    return result


def test_shadow_comparison_uses_only_benchmark_history_at_each_signal(monkeypatch):
    history = bars()
    current = describe_market(
        history, provider="test", instrument_key="NIFTY",
        expected_session=history[-1].timestamp.date(),
    )
    assert current.status == "ready"
    observed_dates = []

    def recorded_replay(key, stock, budget, risk_budget):
        del key, budget, risk_budget
        return [ReplayTrade(
            signal_at=stock[index].timestamp,
            entry_at=stock[index + 1].timestamp,
            exit_at=stock[index + 2].timestamp,
            net_pnl=Decimal("12.50"),
        ) for index in range(400, 420)], False

    def recorded_regime(prefix, *, provider, instrument_key, expected_session):
        del provider, instrument_key
        assert prefix[-1].timestamp.date() == expected_session
        assert all(bar.timestamp.date() <= expected_session for bar in prefix)
        observed_dates.append(expected_session)
        return current

    monkeypatch.setattr("packages.regime.shadow_fit.replay_events", recorded_replay)
    monkeypatch.setattr("packages.regime.shadow_fit.describe_market", recorded_regime)
    result = compare_shadow_fit(
        history, history, current, provider="test", symbol="TEST", nifty_key="NIFTY",
        budget=Decimal("10000"), risk_budget=Decimal("200"),
    )
    assert result.status == "ready"
    assert len(result.rows) == len(STRATEGIES)
    assert len(observed_dates) == 20  # Cached across strategies.
    assert max(observed_dates) < result.benchmark_session
    assert all(row.matching_trades == 20 for row in result.rows)
    assert all(row.matching_average_net_pnl == Decimal("12.50") for row in result.rows)
    assert not result.ranking_changed
    assert not result.out_of_sample_validated


def test_shadow_comparison_abstains_from_small_matched_sample(monkeypatch):
    history = bars()
    current = describe_market(
        history, provider="test", instrument_key="NIFTY",
        expected_session=history[-1].timestamp.date(),
    )

    def few_trades(key, stock, budget, risk_budget):
        del key, budget, risk_budget
        return [ReplayTrade(
            signal_at=stock[index].timestamp,
            entry_at=stock[index + 1].timestamp,
            exit_at=stock[index + 2].timestamp,
            net_pnl=Decimal("100"),
        ) for index in range(400, 403)], False

    monkeypatch.setattr("packages.regime.shadow_fit.replay_events", few_trades)
    result = compare_shadow_fit(
        history, history, current, provider="test", symbol="TEST", nifty_key="NIFTY",
        budget=Decimal("10000"), risk_budget=Decimal("200"),
    )
    assert all(row.status == "insufficient" for row in result.rows)
    assert all(row.matching_average_net_pnl is None for row in result.rows)
    assert all(row.total_closed_trades == 3 for row in result.rows)


def test_shadow_comparison_requires_two_years():
    history = bars(450)
    current = describe_market(
        history, provider="test", instrument_key="NIFTY",
        expected_session=history[-1].timestamp.date(),
    )
    result = compare_shadow_fit(
        history, history, current, provider="test", symbol="TEST", nifty_key="NIFTY",
        budget=Decimal("10000"), risk_budget=Decimal("200"),
    )
    assert result.status == "unavailable"
    assert result.rows == []
