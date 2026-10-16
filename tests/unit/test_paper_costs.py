from decimal import Decimal
from types import SimpleNamespace

import pytest

from packages.paper_trading.costs import MODEL_ID, cost_snapshot, summarize_costs


def order(action, price, units=25):
    fills = [{"instrument_key": "TEST", "quantity_units": units, "fill_price": price}]
    return SimpleNamespace(action=action, status="EXECUTED",
                           net_cash_flow=Decimal(price) * units * (-1 if action == "OPEN" else 1),
                           fill_snapshot={
        "fills": fills, "modeled_costs": cost_snapshot(fills),
    })


def test_costs_use_gross_leg_turnover_not_net_spread_cash():
    fills = [{"fill_price": "101", "quantity_units": 25},
             {"fill_price": "100", "quantity_units": 25}]
    snapshot = cost_snapshot(fills)
    assert snapshot["model_id"] == MODEL_ID
    assert snapshot["total_charge"] == "5.03"
    assert snapshot["rate"] == "0.001"
    assert snapshot["complete_broker_costs"] is False
    assert len(snapshot["legs"]) == 2


def test_complete_costs_partial_legacy_and_corruption():
    opened, closed = order("OPEN", "101"), order("CLOSE", "110")
    result = summarize_costs("CLOSED", Decimal("225"), [opened, closed])
    assert result["coverage"] == "COMPLETE"
    assert result["recorded_modeled_charges"] == "5.28"
    assert result["pnl_after_modeled_charges"] == "219.72"
    assert summarize_costs("CLOSED", Decimal("999"), [opened, closed])[
        "pnl_after_modeled_charges"] is None
    assert summarize_costs("OPEN", None, [opened])["pnl_after_modeled_charges"] is None
    del opened.fill_snapshot["modeled_costs"]
    partial = summarize_costs("CLOSED", Decimal("225"), [opened, closed])
    assert partial["coverage"] == "PARTIAL"
    assert partial["pnl_after_modeled_charges"] is None
    closed.fill_snapshot["modeled_costs"]["total_charge"] = "0.00"
    assert summarize_costs("CLOSED", Decimal("225"), [opened, closed])["coverage"] == "MISSING"


def test_unknown_version_settlement_and_duplicate_actions_withhold_result():
    opened, closed = order("OPEN", "101"), order("CLOSE", "110")
    closed.fill_snapshot["modeled_costs"]["model_id"] = "unknown-v2"
    assert summarize_costs("CLOSED", Decimal("225"), [opened, closed])[
        "pnl_after_modeled_charges"] is None
    closed.fill_snapshot = {"modeled_costs": {"coverage": "UNAVAILABLE"},
                            "fills": [{"intrinsic_value": "0", "quantity_units": 25}]}
    assert summarize_costs("CLOSED", Decimal("225"), [opened, closed])[
        "pnl_after_modeled_charges"] is None
    assert summarize_costs("CLOSED", Decimal("225"), [opened, opened])[
        "pnl_after_modeled_charges"] is None


@pytest.mark.parametrize("price,units", [("NaN", 25), ("-1", 25), ("1", 0), ("1", "1.5")])
def test_invalid_inputs_are_not_zero_cost(price, units):
    with pytest.raises(ValueError):
        cost_snapshot([{"fill_price": price, "quantity_units": units}])


def test_cost_evidence_round_trips_without_changing_ledger(tmp_path):
    from datetime import date
    from uuid import uuid4

    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from packages.database.base import Base
    from packages.database.models import PaperAccount, PaperOrder, PaperTrade
    from packages.paper_trading.ledger_reconciliation import reconcile_paper_ledger

    engine = create_engine(f"sqlite:///{tmp_path / 'costs.sqlite'}")
    Base.metadata.create_all(engine)
    owner = uuid4()
    with Session(engine) as session:
        account = PaperAccount(user_id=owner, initial_cash=Decimal("10000"),
                               cash_balance=Decimal("10225"), kill_switch_enabled=False)
        session.add(account)
        session.flush()
        trade = PaperTrade(account_id=account.id, user_id=owner, strategy_key="long_call",
                           strategy_name="Long call", underlying="NIFTY", expiry=date(2030, 1, 1),
                           reference_strike=Decimal("25000"), lots=1, lot_size=25,
                           max_loss=Decimal("2525"), entry_cash_flow=Decimal("-2525"),
                           realized_pnl=Decimal("225"), status="CLOSED", legs={"items": []})
        session.add(trade)
        session.flush()
        for action, price, flow in [("OPEN", "101", "-2525"), ("CLOSE", "110", "2750")]:
            evidence = order(action, price)
            session.add(PaperOrder(account_id=account.id, trade_id=trade.id, user_id=owner,
                                   action=action, status="EXECUTED", net_cash_flow=Decimal(flow),
                                   fill_snapshot=evidence.fill_snapshot))
        session.commit()
    with Session(engine) as session:
        account = session.scalar(select(PaperAccount))
        trade = session.scalar(select(PaperTrade))
        orders = list(session.scalars(select(PaperOrder)))
        assert summarize_costs(trade.status, trade.realized_pnl, orders)[
            "pnl_after_modeled_charges"] == "219.72"
        assert account.cash_balance == Decimal("10225")
        assert trade.realized_pnl == Decimal("225")
        assert reconcile_paper_ledger(account, [trade], orders).status == "MATCHED"
        assert len(orders) == 2
    engine.dispose()
