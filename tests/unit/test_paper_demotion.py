from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from packages.database import models  # noqa: F401
from packages.database.base import Base
from packages.paper_trading.demotion import (
    DemotionLimits,
    drawdown_baseline,
    evaluate_demotion,
    max_drawdown,
    record_drawdown_baseline,
)

LIMITS = DemotionLimits(loss_streak=5, drawdown_multiple=Decimal(2),
                        fallback_drawdown_per_lot=Decimal(60000))


def _pnl(*values):
    return [Decimal(value) for value in values]


def test_five_consecutive_losses_demote_even_if_a_win_follows():
    check = evaluate_demotion(_pnl(-10, -10, -10, -10, -10, 500), LIMITS, None)
    assert check.reason == "LOSS_STREAK"
    assert check.longest_loss_streak == 5


def test_breakeven_or_win_resets_the_streak():
    check = evaluate_demotion(_pnl(-10, -10, -10, -10, 0, -10, -10), LIMITS, None)
    assert check.reason is None
    assert check.longest_loss_streak == 4


def test_drawdown_uses_twice_the_backtest_baseline_per_lot():
    # Peak 3000 then down to -2100: drawdown 5100 per lot against a 2 x 2500 limit.
    check = evaluate_demotion(_pnl(3000, -2600, 500, -3000), LIMITS, Decimal(2500))
    assert check.reason == "DRAWDOWN"
    assert check.max_drawdown_per_lot == Decimal("5100.00")
    assert check.drawdown_limit_per_lot == Decimal("5000.00")
    assert check.limit_source == "BACKTEST"
    within = evaluate_demotion(_pnl(3000, -2600, 500, -3000), LIMITS, Decimal(2600))
    assert within.reason is None


def test_missing_baseline_falls_back_to_the_capital_share():
    check = evaluate_demotion(_pnl(-30000, 100, -30101), LIMITS, None)
    assert check.limit_source == "FALLBACK"
    assert check.drawdown_limit_per_lot == Decimal("60000.00")
    assert check.reason == "DRAWDOWN"
    assert evaluate_demotion(_pnl(-30000, 100, -29000), LIMITS, Decimal(0)).reason is None


def test_no_trades_is_not_a_breach_and_limits_are_validated():
    assert evaluate_demotion([], LIMITS, None).reason is None
    with pytest.raises(ValueError):
        DemotionLimits(loss_streak=1, drawdown_multiple=Decimal(2),
                       fallback_drawdown_per_lot=Decimal(1))


def test_max_drawdown_starts_from_a_zero_peak():
    assert max_drawdown(_pnl(-100, 50, -200)) == Decimal(250)
    assert max_drawdown(_pnl(100, 200)) == Decimal(0)


def test_backtest_baseline_is_saved_and_used_by_the_drawdown_rule():
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with sessionmaker(engine)() as session:
        with pytest.raises(ValueError):
            record_drawdown_baseline(session, "bull_put_spread", _pnl(*[-1] * 9),
                                     source="test", details={})
        record_drawdown_baseline(
            session, "bull_put_spread", _pnl(500, -1200, 300, -900, 400, 200, -100, 50, 60, 70),
            source="test", details={"from": "2024-10-01"},
        )
        session.commit()
        baseline = drawdown_baseline(session, "bull_put_spread")
        assert baseline is not None
        assert baseline.max_drawdown_per_lot == Decimal("1800.00")
        assert baseline.trades == 10
    check = evaluate_demotion(_pnl(-3700), LIMITS, baseline.max_drawdown_per_lot)
    assert check.limit_source == "BACKTEST"
    assert check.drawdown_limit_per_lot == Decimal("3600.00")
    assert check.reason == "DRAWDOWN"
