from datetime import date
from decimal import Decimal

import pytest

from packages.core.charges import (
    SCHEDULE_2024_10,
    UPSTOX_PLUS,
    exercise_charges,
    order_charges,
    schedule_for,
)


def test_schedule_selection_by_trade_date() -> None:
    assert schedule_for(date(2025, 6, 1)).schedule_id == "nse-fo-2024-10-01"
    assert schedule_for(date(2026, 10, 27)).schedule_id == "nse-fo-2026-04-01"
    with pytest.raises(ValueError):
        schedule_for(date(2023, 1, 1))


def test_option_sell_2024_schedule_components() -> None:
    # 65 units at ₹100 premium: turnover ₹6,500.
    c = order_charges(instrument="OPT", side="SELL", price=Decimal("100"), quantity=65,
                      trade_date=date(2025, 6, 2))
    assert c.turnover == Decimal("6500.00")
    assert c.brokerage == Decimal("20")
    assert c.stt == Decimal("6.50")            # 0.1% of premium
    assert c.transaction == Decimal("2.28")    # 0.03503%
    assert c.ipft == Decimal("0.03")           # ₹0.50 per lakh
    assert c.stamp == Decimal("0")             # buy side only
    assert c.sebi == Decimal("0.01")           # ₹10 per crore
    assert c.gst == Decimal("4.02")            # 18% of (20 + 2.28 + 0.03) = 4.0158
    assert c.total == Decimal("32.84")
    assert c.verified


def test_option_2026_rates_come_from_repo_review_and_buy_side_stamp() -> None:
    sell = order_charges(instrument="OPT", side="SELL", price=Decimal("100"), quantity=65,
                         trade_date=date(2026, 10, 27))
    assert sell.stt == Decimal("9.75")  # 0.15%
    assert sell.verified
    buy = order_charges(instrument="OPT", side="BUY", price=Decimal("100"), quantity=65,
                        trade_date=date(2026, 10, 27))
    assert buy.stt == 0 and buy.stamp == Decimal("0.20")


def test_futures_brokerage_cap_and_unverified_2026_stt() -> None:
    small = order_charges(instrument="FUT", side="BUY", price=Decimal("10"), quantity=65,
                          trade_date=date(2025, 6, 2))
    assert small.brokerage == Decimal("0.33")  # 0.05% of ₹650 is below ₹20
    big = order_charges(instrument="FUT", side="SELL", price=Decimal("25000"), quantity=65,
                        trade_date=date(2025, 6, 2))
    assert big.brokerage == Decimal("20") and big.stt == Decimal("325.00")  # 0.02%
    assert big.verified
    later = order_charges(instrument="FUT", side="SELL", price=Decimal("25000"), quantity=65,
                          trade_date=date(2026, 10, 27))
    assert later.stt == Decimal("812.50") and not later.verified


def test_plan_and_exercise_charges() -> None:
    plus = order_charges(instrument="OPT", side="BUY", price=Decimal("50"), quantity=65,
                         trade_date=date(2025, 6, 2), plan=UPSTOX_PLUS)
    assert plus.brokerage == Decimal("30")
    ex = exercise_charges(settlement_price=Decimal("25100"), quantity=65,
                          trade_date=date(2025, 6, 2), schedule=SCHEDULE_2024_10)
    assert ex.stt == Decimal("2039.38") and not ex.verified
    with pytest.raises(ValueError):
        order_charges(instrument="OPT", side="BUY", price=Decimal("1"), quantity=0,
                      trade_date=date(2025, 6, 2))
