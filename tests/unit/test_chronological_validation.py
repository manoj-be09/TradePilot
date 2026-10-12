from datetime import datetime, timedelta, timezone
from decimal import Decimal

from packages.market_data.contracts import HistoricalBar
from packages.regime.chronological_validation import review_fixed_equity_rules


def _bars() -> list[HistoricalBar]:
    start = datetime(2024, 1, 1, 5, 30, tzinfo=timezone.utc)
    return [
        HistoricalBar(
            timestamp=start + timedelta(days=index),
            open=Decimal(100 + index), high=Decimal(102 + index),
            low=Decimal(99 + index), close=Decimal(101 + index), volume=10000,
        )
        for index in range(760)
    ]


def _review(bars: list[HistoricalBar]):
    return review_fixed_equity_rules(
        bars, provider="fixture", symbol="TEST",
        budget=Decimal(10000), risk_budget=Decimal(200),
    )


def test_reserved_latest_window_never_reaches_replay():
    original = _bars()
    first = _review(original)
    changed = [*original[:608], *[
        bar.model_copy(update={
            "open": bar.open * 10, "high": bar.high * 10,
            "low": bar.low * 10, "close": bar.close * 10,
        }) for bar in original[608:]
    ]]
    second = _review(changed)
    assert first.status == second.status == "ready"
    assert first.reserved_bars == 152
    assert first.rows == second.rows
    assert first.ranking_changed is False and first.parameters_changed is False
    assert first.reserved_from == second.reserved_from


def test_cross_window_trade_is_excluded_and_small_samples_are_suppressed(monkeypatch):
    from packages.paper_trading.equity import ReplayTrade

    bars = _bars()

    def fixture_events(_key, seen, _budget, _risk_budget):
        assert len(seen) == 608  # Reserved newest fifth was not passed to replay.
        def trade(signal_index, exit_index):
            return ReplayTrade(
                bars[signal_index].timestamp, bars[signal_index + 1].timestamp,
                bars[exit_index].timestamp, Decimal(10), Decimal(20),
            )
        return [trade(300, 310), trade(455, 460), trade(480, 482)], False

    monkeypatch.setattr(
        "packages.regime.chronological_validation.replay_events", fixture_events
    )
    result = _review(bars)
    assert result.status == "ready"
    assert all(row.training_trades == 1 for row in result.rows)
    assert all(row.validation_trades == 1 for row in result.rows)
    assert all(row.boundary_trades_excluded == 1 for row in result.rows)
    assert all(row.status == "insufficient" for row in result.rows)
    assert all(row.training_smoothed_win_pct is None for row in result.rows)
    assert all(row.validation_win_pct is None for row in result.rows)


def test_sufficient_earlier_windows_calculate_validation_error(monkeypatch):
    from packages.paper_trading.equity import ReplayTrade

    bars = _bars()

    def fixture_events(_key, seen, _budget, _risk_budget):
        assert len(seen) == 608
        events = []
        for i in range(20):
            for start, wins in ((220, 10), (460, 15)):
                signal_index = start + i * 5
                events.append(ReplayTrade(
                    signal_at=bars[signal_index].timestamp,
                    entry_at=bars[signal_index + 1].timestamp,
                    exit_at=bars[signal_index + 2].timestamp,
                    net_pnl=Decimal(10 if i < wins else -10),
                    initial_risk=Decimal(20),
                ))
        return events, False

    monkeypatch.setattr(
        "packages.regime.chronological_validation.replay_events", fixture_events
    )
    row = _review(bars).rows[0]
    assert row.status == "reviewable"
    assert row.training_smoothed_win_pct == 50
    assert row.validation_win_pct == 75
    assert row.validation_average_net_pnl == 5
    assert row.validation_brier == 0.25


def test_short_or_gapped_history_is_unavailable():
    bars = _bars()
    assert _review(bars[:399]).status == "unavailable"
    gapped = [*bars[:400], *bars[420:]]
    result = _review(gapped)
    assert result.status == "unavailable"
    assert "gap" in result.reason
