"""The options ledger independently recomputes recorded expiry cash."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from packages.paper_trading.ledger_reconciliation import reconcile_paper_ledger


def _record(*, closing_cash: str = "20.00", final_value: str = "110.00"):
    owner, account_id, trade_id = uuid4(), uuid4(), uuid4()
    initial = Decimal("1000.00")
    close = Decimal(closing_cash)
    entry = Decimal("-4.00")
    account = SimpleNamespace(
        id=account_id, user_id=owner, initial_cash=initial,
        cash_balance=initial + entry + close,
    )
    trade = SimpleNamespace(
        id=trade_id, account_id=account_id, user_id=owner,
        status="CLOSED", expiry=date(2026, 9, 29),
        entry_cash_flow=entry, realized_pnl=entry + close,
        exit_reason="EXPIRY_SETTLEMENT",
        legs={"items": [{
            "instrument_key": "NSE_FO|test", "option_type": "CE", "side": "BUY",
            "strike": "100.00", "quantity_units": 2,
        }]},
    )
    open_order = SimpleNamespace(
        id=uuid4(), account_id=account_id, user_id=owner,
        trade_id=trade_id, action="OPEN", net_cash_flow=entry,
    )
    close_order = SimpleNamespace(
        id=uuid4(), account_id=account_id, user_id=owner,
        trade_id=trade_id, action="CLOSE", net_cash_flow=close,
        fill_snapshot={"settlement_reference": {
            "settlement_date": "2026-09-29", "final_index_value": final_value,
            "source_reference": "Operator-reviewed exchange close",
        }},
    )
    return account, trade, [open_order, close_order]


def test_valid_expiry_cash_matches_saved_legs_and_index_close():
    account, trade, orders = _record()
    assert reconcile_paper_ledger(account, [trade], orders).status == "MATCHED"


def test_balanced_but_wrong_expiry_cash_is_a_mismatch():
    account, trade, orders = _record(closing_cash="19.00")
    report = reconcile_paper_ledger(account, [trade], orders)
    assert report.cash_difference == Decimal("0.00")
    assert report.status == "MISMATCH"
    assert any("differs from saved legs" in issue for issue in report.issues)


def test_missing_or_invalid_settlement_reference_is_a_mismatch():
    account, trade, orders = _record(final_value="invalid")
    assert reconcile_paper_ledger(account, [trade], orders).status == "MISMATCH"
    orders[1].fill_snapshot = {}
    report = reconcile_paper_ledger(account, [trade], orders)
    assert any("no recorded reference" in issue for issue in report.issues)


@pytest.mark.parametrize("field,value", [
    ("settlement_date", "2026-09-30"),
    ("source_reference", "   "),
    ("final_index_value", "NaN"),
])
def test_balanced_cash_does_not_validate_corrupted_settlement_evidence(field, value):
    account, trade, orders = _record()
    orders[1].fill_snapshot["settlement_reference"][field] = value
    report = reconcile_paper_ledger(account, [trade], orders)
    assert report.cash_difference == Decimal("0.00")
    assert report.status == "MISMATCH"
    assert any("cannot be recomputed" in issue for issue in report.issues)
