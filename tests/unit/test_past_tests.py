"""Saved walk-forward results load for ranking, and bad files never break the strategy list."""

import json
from datetime import date
from decimal import Decimal

from packages.backtesting.past_tests import load_past_tests


def _result(**overrides):
    return {
        "model_id": "nifty-history-walk-forward-v1",
        "base_config": {
            "strategy_key": "long_put", "strategy_version": "1.0.0", "half_spread_pct": "0.25",
        },
        "out_of_sample": {
            "closed_trades": 82, "win_rate": 29.26829268292683, "net_pnl": 103487.71,
            "average_net_pnl": 1262.045243902439, "closed_trade_drawdown": 74757.12,
            "first_exit_at": "2025-04-07T15:20:00+05:30",
            "last_exit_at": "2026-10-26T14:05:00+05:30",
        },
    } | overrides


def test_loads_a_result_with_its_period_in_india_time(tmp_path):
    (tmp_path / "wf-long_put.json").write_text(json.dumps(_result()), encoding="utf-8")
    test = load_past_tests(tmp_path)["long_put"]
    assert test.trades == 82
    assert test.win_rate_pct == Decimal("29.27")
    assert test.average_net_pnl == Decimal("1262.05")
    assert test.first_exit == date(2025, 4, 7)
    assert test.last_exit == date(2026, 10, 26)


def test_a_missing_directory_gives_no_results(tmp_path):
    assert load_past_tests(tmp_path / "absent") == {}


def test_bad_files_are_skipped_and_good_ones_kept(tmp_path):
    (tmp_path / "wf-broken.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "wf-empty.json").write_text("{}", encoding="utf-8")
    (tmp_path / "wf-long_put.json").write_text(json.dumps(_result()), encoding="utf-8")
    assert list(load_past_tests(tmp_path)) == ["long_put"]


def test_another_model_or_no_trades_is_not_used(tmp_path):
    (tmp_path / "wf-a.json").write_text(json.dumps(_result(model_id="other")), encoding="utf-8")
    empty = _result()
    empty["out_of_sample"]["closed_trades"] = 0
    (tmp_path / "wf-b.json").write_text(json.dumps(empty), encoding="utf-8")
    assert load_past_tests(tmp_path) == {}


def test_only_result_files_are_read(tmp_path):
    (tmp_path / "cal-long_put.json").write_text(json.dumps(_result()), encoding="utf-8")
    assert load_past_tests(tmp_path) == {}
