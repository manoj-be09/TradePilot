"""Independent arithmetic and boundary fixtures for the five equity paper rules."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from packages.market_data.contracts import HistoricalBar
from packages.paper_trading.equity import (
    DEFAULT_EXIT_POLICY,
    EquityExitPolicy,
    ReplayTrade,
    average_true_range,
    exit_reason,
    open_position,
    position_exit_policy,
    proceeds,
    replay,
    replay_events,
    risk_distance,
    rsi2,
    signal,
    trail,
)


def candles(closes):
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    return [HistoricalBar(
        timestamp=start + timedelta(days=i), open=D(str(c)), high=D(str(c)) + 1,
        low=D(str(c)) - 1, close=D(str(c)), volume=100,
    ) for i, c in enumerate(closes)]


def test_all_five_entry_rules_and_exact_thresholds():
    rising = candles(range(100, 151))
    assert signal("trend", rising)
    assert not signal("trend", candles([100] * 51))
    assert not signal("trend", candles(range(151, 100, -1)))
    breakout = candles([100] * 50 + [102])
    breakout[-1] = breakout[-1].model_copy(update={"volume": 151})
    assert signal("breakout", breakout)
    assert not signal("breakout", [*breakout[:-1], breakout[-1].model_copy(
        update={"volume": 150})])  # Strictly greater than 1.5x, not equal.
    assert not signal("breakout", [*breakout[:-1], breakout[-1].model_copy(
        update={"close": D(101)})])  # Equal to the preceding high is not a breakout.
    assert not signal("breakout", [b.model_copy(update={"volume": 0}) for b in breakout])
    pullback = candles([90] * 45 + [110] * 4 + [100, 109])
    assert signal("pullback", pullback)  # Prior SMA5 = 108; previous close = 100.
    assert not signal("pullback", candles([90] * 45 + [110] * 4 + [100, 108]))
    reversion = candles([100] * 49 + [80, 81])
    assert signal("reversion", reversion)  # Uptick only; still below the prior lower band.
    assert not signal("reversion", candles([100] * 49 + [80, 80]))
    assert not signal("reversion", candles([100] * 50 + [101]))
    assert not signal("momentum", candles([100] * 50 + [103]))  # Exactly 3% is excluded.
    assert signal("momentum", candles([100] * 50 + [D('103.00000000000000001')]))
    assert not signal("momentum", candles([100] * 49 + [110, 105]))
    assert not signal("unknown", rising)
    assert not signal("trend", rising[:50])


def test_atr_includes_gaps_uses_simple_mean_and_enforces_minimum_distance():
    bars = candles([100] * 14 + [110])
    # Thirteen true ranges of 2, plus gap true range 11 => 37/14.
    assert average_true_range(bars) == D(37) / 14
    assert risk_distance(bars) == D(37) / 14 * 2
    flat = [b.model_copy(update={"high": D(100), "low": D(100)})
            for b in candles([100] * 15)]
    assert risk_distance(flat) == D('.50')
    with pytest.raises(ValueError):
        risk_distance(bars[:14])


def test_hand_calculated_fills_fees_quantity_and_net_scenarios():
    p = open_position(D(100), D(5), D(1000), D(50))
    assert p is not None
    # Entry 100.05; nine shares; entry notional 900.45 + 0.90 fees.
    assert p['quantity'] == 9
    assert D(p['cost']) == D('901.35')
    assert D(p['entry_fee']) == D('.90')
    assert D(p['stop']) == D('95.05')
    assert D(p['target']) == D('110.05')
    # Stop: 95.00 x 9 - 0.86 fees = 854.14; target: 109.99 x 9 - .99 = 988.92.
    assert proceeds(p, D('95.05')) == (D('95.00'), D('854.14'))
    assert proceeds(p, D('110.05')) == (D('109.99'), D('988.92'))
    assert D(p['stop_scenario_net']) == D('-47.21')
    assert D(p['target_scenario_net']) == D('87.57')


def test_low_price_rounding_cannot_exceed_planned_stop_risk():
    # Old approximation bought 1,333 shares and lost 15.98 against a risk budget of 10.
    p = open_position(D(1), D('.005'), D(10000), D(10))
    assert p is not None
    assert p['quantity'] == 834
    assert D(p['stop_scenario_net']) == D('-10.00')
    assert D(p['cost']) <= 10000


@pytest.mark.parametrize('price', ['0.10', '1', '1.01', '7.37', '100', '1500', '24000'])
@pytest.mark.parametrize('risk', ['10', '200', '1000'])
def test_sizing_and_scenarios_reconcile_with_execution(price, risk):
    price, risk = D(price), D(risk)
    p = open_position(price, price * D('.005'), D(25000), risk)
    if p is None:
        return
    assert 0 < D(p['cost']) <= 25000
    assert 0 < -D(p['stop_scenario_net']) <= risk
    for prefix, price_field in [('stop', 'stop'), ('target', 'target')]:
        fill, net = proceeds(p, D(p[price_field]))
        assert D(p[f'{prefix}_scenario_net']) == net - D(p['cost'])
        assert D(p[f'{prefix}_exit_fee']) == fill * p['quantity'] - net


@pytest.mark.parametrize('field,value', [
    ('price', '0'), ('price', 'NaN'), ('price', 'Infinity'), ('distance', '0'),
    ('distance', '-1'), ('distance', '101'), ('budget', '0'), ('risk_budget', '-1'),
])
def test_invalid_sizing_input_returns_no_position(field, value):
    args = dict(price=D(100), distance=D(5), budget=D(1000), risk_budget=D(50))
    args[field] = D(value)
    assert open_position(**args) is None


def test_trailing_is_monotone_but_price_breakeven_is_not_net_breakeven():
    p = open_position(D(100), D(5), D(1000), D(50))
    assert p is not None
    assert not trail(p, D('105.04'))['trailing_active']
    moved = trail(p, D('105.05'))
    assert moved['trailing_active'] and D(moved['stop']) == D('100.05')
    # Moving the trigger to the entry fill still leaves exit slippage and both-side fees.
    assert proceeds(moved, D(moved['stop']))[1] < D(moved['cost'])
    assert trail(moved, D('104'))['stop'] == moved['stop']
    assert D(trail(moved, D('107'))['stop']) == D('102')
    assert exit_reason(moved, D('100')) == 'TRAILING_STOP'


def test_exit_policy_is_pinned_to_position_and_rejects_invalid_changes():
    policy = EquityExitPolicy(
        version='equity-exit-research-target4-trailhalf',
        atr_multiplier=D('1.5'), minimum_distance_fraction=D('.005'),
        target_r=D('4'), trail_activation_r=D('1.5'),
        trail_distance_r=D('.5'),
    )
    p = open_position(D(100), D(5), D(1000), D(50), policy=policy)
    assert p is not None
    assert position_exit_policy(p) == policy
    assert D(p['target']) == D(p['entry_price']) + D(20)
    assert not trail(p, D('107.54'))['trailing_active']
    moved = trail(p, D('107.55'))
    assert moved['trailing_active']
    assert D(moved['stop']) == D('105.05')
    assert position_exit_policy(open_position(D(100), D(5), D(1000), D(50))) == DEFAULT_EXIT_POLICY
    with pytest.raises(ValueError, match='parameters do not match'):
        position_exit_policy({**p, 'exit_policy_version': DEFAULT_EXIT_POLICY.version})
    with pytest.raises(ValueError, match='Missing'):
        position_exit_policy({'exit_policy_version': 'unknown'})
    with pytest.raises(ValueError, match='bounds'):
        EquityExitPolicy('unbounded', D('100'), D('.005'), D(2), D(1), D(1))


def test_research_exit_policy_changes_replay_without_changing_default():
    history = candles(range(100, 151))
    next_bar = candles([150])[0].model_copy(update={
        'timestamp': history[-1].timestamp + timedelta(days=1),
        'high': D(159), 'low': D(149),
    })
    baseline, baseline_open = replay_events(
        'trend', [*history, next_bar], D(10000), D(200)
    )
    candidate = EquityExitPolicy(
        version='equity-exit-research-target4',
        atr_multiplier=D(2), minimum_distance_fraction=D('.005'),
        target_r=D(4), trail_activation_r=D(1), trail_distance_r=D(1),
    )
    research, research_open = replay_events(
        'trend', [*history, next_bar], D(10000), D(200), policy=candidate
    )
    assert len(baseline) == 1 and not baseline_open
    assert research == [] and research_open


def test_closed_trade_metrics_include_cost_losses_and_exclude_open_trade(monkeypatch):
    stamp = datetime(2025, 1, 1, tzinfo=timezone.utc)
    trades = [ReplayTrade(stamp, stamp, stamp, D(pnl))
              for pnl in ['100', '-150', '0', '25']]
    monkeypatch.setattr('packages.paper_trading.equity.replay_events',
                        lambda *args: (trades, True))
    result = replay('trend', [], D(10000), D(200))
    expected = dict(closed_trades=4, win_rate=50.0, average_net_pnl=-6.25,
                          best_trade=100.0, worst_trade=-150.0, closed_trade_drawdown=150.0,
                          open_trade_excluded=True, net_pnl=-25.0)
    assert {key: result[key] for key in expected} == expected


def test_replay_next_bar_gap_fill_stop_priority_and_open_trade_exclusion():
    history = candles(range(100, 151))
    next_bar = candles([150])[0].model_copy(update={
        'timestamp': history[-1].timestamp + timedelta(days=1),
        'high': D(160), 'low': D(140),
    })
    for key in ('trend', 'momentum'):
        events, still_open = replay_events(key, [*history, next_bar], D(10000), D(200))
        assert len(events) == 1 and not still_open
        assert events[0].signal_at == history[-1].timestamp
        assert events[0].entry_at == next_bar.timestamp
        assert events[0].net_pnl < 0  # Both boundaries touched; stop wins.
    quiet = next_bar.model_copy(update={'high': D(151), 'low': D(149)})
    events, still_open = replay_events('trend', [*history, quiet], D(10000), D(200))
    assert events == [] and still_open
    gap = quiet.model_copy(update={'timestamp': quiet.timestamp + timedelta(days=1),
                                 'open': D(130), 'high': D(131),
                                 'low': D(129), 'close': D(130)})
    events, still_open = replay_events('trend', [*history, quiet, gap], D(10000), D(200))
    assert len(events) == 1 and not still_open
    assert events[0].net_pnl < -200  # Gap loss is not falsely capped to the planned stop.


@pytest.mark.parametrize('key,closes', [
    ('channel20', [100] * 50 + [102]),
    ('channel55', [100] * 55 + [102]),
    ('connors_r2', [100] * 190 + [120, 121, 122, 123, 124, 125, 126, 125, 124, 123]),
    ('band_reclaim', [100] * 49 + [80, 90]),
    ('sma_cross', [100] * 50 + [110]),
])
def test_new_adaptations_signal_and_replay_real_entry(key, closes):
    bars = candles(closes)
    assert signal(key, bars)
    assert not signal(key, candles([100] * len(bars)))
    last = bars[-1]
    next_bar = last.model_copy(update={
        'timestamp': last.timestamp + timedelta(days=1),
        'open': last.close, 'low': last.close / 2, 'high': last.close * 2,
    })
    trades, is_open = replay_events(key, [*bars, next_bar], D(10000), D(200))
    assert len(trades) == 1 and not is_open
    assert trades[0].entry_at == next_bar.timestamp
    assert trades[0].net_pnl < 0


def test_rsi_wilder_seed_recursive_smoothing_and_missing_warmup():
    readings = rsi2([D(100), D(102), D(101), D(100)])
    assert readings[0] == 100 - D(100) / 3
    assert readings[1] == 40
    assert rsi2([D(100)] * 4) == [50, 50]
    assert rsi2([D(100), D(101)]) == []
    assert not signal('connors_r2', candles(range(100, 299)))
    assert not signal('channel55', candles(range(100, 155)))
    assert not signal('band_reclaim', candles([100] * 49 + [80, 81]))
