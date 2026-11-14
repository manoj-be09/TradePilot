"""Proposing forward versions uses live quotes, arms nothing and skips what it can't price."""

import asyncio
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from apps.forward_runner.main import FROZEN_NAME_PREFIX, propose
from packages.paper_trading.strategies import STRATEGIES

TODAY = date(2026, 11, 3)
EXPIRY = date(2026, 11, 10)


def _quote(bid: str, ask: str) -> SimpleNamespace:
    return SimpleNamespace(bid=Decimal(bid), ask=Decimal(ask))


class _Provider:
    def __init__(self, strikes=None, quality="PASS"):
        self.strikes = strikes or [Decimal(22500 + 50 * step) for step in range(21)]
        self.quality = quality

    async def get_option_contracts(self, _underlying="NIFTY", _expiry=None):
        return [
            SimpleNamespace(expiry=TODAY, lot_size=65),
            SimpleNamespace(expiry=EXPIRY, lot_size=65),
        ]

    async def get_option_chain(self, _underlying, expiry):
        contracts = []
        for strike in self.strikes:
            for option_type in ("CE", "PE"):
                premium = 100 + abs(strike - Decimal(23000)) / 10
                contracts.append(SimpleNamespace(
                    strike=strike, option_type=option_type,
                    quote=_quote(str(premium - 1), str(premium + 1)),
                ))
        return SimpleNamespace(
            expiry=expiry, underlying_spot=Decimal("23010"), contracts=contracts,
            quality=SimpleNamespace(status=self.quality),
        )


def _run(provider) -> tuple[list, list[str], list[str]]:
    requests = []

    async def create(body, _user, _session):
        requests.append(body)

    saved, skipped = asyncio.run(propose(None, object(), provider, create, TODAY))
    return requests, saved, skipped


def test_every_template_with_a_finite_loss_gets_a_forward_version():
    requests, saved, skipped = _run(_Provider())
    assert sorted(saved + skipped) == sorted(item.key for item in STRATEGIES)
    assert len(requests) == len(saved) > 0
    assert all(item.name.startswith(FROZEN_NAME_PREFIX) and item.lots == 1 for item in requests)
    assert all(len(item.name) <= 64 for item in requests)
    assert all(item.stop_loss_amount > 0 and item.trailing_stop_amount > 0 for item in requests)


def test_strategies_whose_strikes_are_not_listed_are_skipped_not_guessed():
    _, saved, skipped = _run(_Provider(strikes=[Decimal(23000)]))
    assert skipped
    assert all(
        all(leg.strike_offset == 0 for leg in item.legs)
        for item in STRATEGIES if item.key in saved
    )


def test_a_failed_chain_saves_nothing():
    requests = []

    async def create(body, _user, _session):
        requests.append(body)

    try:
        asyncio.run(propose(None, object(), _Provider(quality="FAIL"), create, TODAY))
    except ValueError:
        pass
    else:
        raise AssertionError("expected the failed chain to be refused")
    assert requests == []


def test_a_template_filter_saves_only_those_templates_for_a_separate_account():
    requests = []

    async def create(body, _user, _session):
        requests.append(body)

    wide = _Provider(strikes=[Decimal(22000 + 50 * step) for step in range(41)])
    saved, skipped = asyncio.run(propose(None, object(), wide, create, TODAY,
                                         frozenset({"range_iron_condor"})))
    assert (saved, skipped) == (["range_iron_condor"], [])
    rules = {(item.metric, item.operator, item.threshold) for item in requests[0].entry_conditions}
    assert ("atm_iv_pct", "GTE", Decimal(16)) in rules


def test_a_calendar_is_proposed_only_when_named_and_priced_by_its_debit():
    class _TwoExpiries(_Provider):
        async def get_option_contracts(self, _underlying="NIFTY", _expiry=None):
            later = SimpleNamespace(expiry=date(2026, 11, 17), lot_size=65)
            return [*await super().get_option_contracts(), later]

    requests = []

    async def create(body, _user, _session):
        requests.append(body)

    assert "call_calendar" not in _run(_TwoExpiries())[1]
    saved, _ = asyncio.run(propose(None, object(), _TwoExpiries(), create, TODAY,
                                   frozenset({"call_calendar"})))
    assert saved == ["call_calendar"]
    # Buy the far 23000 call at 101, sell the near one at 99: a debit of 2 x 65. The stop is
    # capped at that debit, the most a long call calendar can lose.
    assert requests[0].stop_loss_amount == Decimal("130.00")
    # A put calendar risks more than its debit: the later put may trade below intrinsic.
    put_requests = []

    async def keep(body, _user, _session):
        put_requests.append(body)

    saved, _ = asyncio.run(propose(None, object(), _TwoExpiries(), keep, TODAY,
                                   frozenset({"calendar_put_spread"})))
    assert saved == ["calendar_put_spread"]
    assert put_requests[0].stop_loss_amount > Decimal("130.00")


def test_extra_conditions_are_added_to_every_saved_version():
    requests = []

    async def create(body, _user, _session):
        requests.append(body)

    extra = ({"metric": "iv_minus_rv_points", "operator": "LTE", "threshold": "0"},)
    saved, _ = asyncio.run(propose(None, object(), _Provider(), create, TODAY,
                                   frozenset({"long_straddle", "long_put"}), extra))
    assert sorted(saved) == ["long_put", "long_straddle"]
    for body in requests:
        assert ("iv_minus_rv_points", "LTE", Decimal(0)) in {
            (item.metric, item.operator, item.threshold) for item in body.entry_conditions}
