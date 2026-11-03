"""Allocation orders due rules by Thompson draws and keeps the book inside its caps."""

import random
from decimal import Decimal

from packages.learning.allocation import (
    Leg,
    refusal,
    saved_legs,
    stress_loss,
    thompson_draw,
)

_SPOT = 22_000.0


def _condor(centre: int = 22_000, credit_each: float = 40.0) -> list[Leg]:
    return [Leg("CE", "SELL", Decimal(centre + 500), 65, credit_each),
            Leg("PE", "SELL", Decimal(centre - 500), 65, credit_each),
            Leg("CE", "BUY", Decimal(centre + 800), 65, 10.0),
            Leg("PE", "BUY", Decimal(centre - 800), 65, 10.0)]


def _greeks(delta: float = 0.1, vega: float = 2.0) -> dict:
    found = {}
    for strike in (21_200, 21_500, 22_500, 22_800, 21_000, 23_000):
        found[(Decimal(strike), "CE")] = {"delta": Decimal(str(delta)), "vega": Decimal(str(vega))}
        found[(Decimal(strike), "PE")] = {"delta": Decimal(str(-delta)),
                                          "vega": Decimal(str(vega))}
    return found


def test_gap_stress_is_the_condor_wing_less_its_credit():
    # A 5% move runs through both short strikes on one side: 300 points less the net credit.
    assert stress_loss(_condor(), _SPOT) == 65 * 300 - 65 * 60


def test_the_book_must_fit_delta_vega_and_gap_caps():
    assert refusal(_condor(), [], _SPOT, _greeks(), 1_000_000) is None
    # Six condors held already: a seventh breaks the gap test at 10% of ₹10 lakh.
    held = [leg for _ in range(6) for leg in _condor()]
    assert "5% gap" in refusal(_condor(), held, _SPOT, _greeks(), 1_000_000)
    lopsided = _greeks()
    lopsided[(Decimal(22_500), "CE")] = {"delta": Decimal("0.9"), "vega": Decimal(2)}
    assert "net delta" in refusal(_condor(), [], _SPOT, lopsided, 1_000_000)
    rich_vega = {key: {**value, "vega": Decimal(30)} if key[0] in (Decimal(22_500),
                                                                   Decimal(21_500)) else value
                 for key, value in _greeks().items()}
    assert "net vega" in refusal(_condor(), [], _SPOT, rich_vega, 1_000_000)
    assert "greeks unknown" in refusal(_condor(), [], _SPOT, {}, 1_000_000)


def test_thompson_draws_favour_the_better_record_but_not_always():
    rng = random.Random(5)
    strong, weak = [2_000.0, 2_500.0] * 20, [200.0, -300.0] * 20
    wins = sum(thompson_draw(strong, rng) > thompson_draw(weak, rng) for _ in range(500))
    assert wins > 450
    # Untried rules draw from a wide prior, so they still get a turn.
    fresh = sum(thompson_draw([], rng) > thompson_draw(weak, rng) for _ in range(500))
    assert 150 < fresh < 350


def test_saved_legs_read_the_stored_paper_trade_shape():
    [leg] = saved_legs([{"option_type": "CE", "side": "SELL", "strike": "22500.00",
                         "quantity_units": 65, "entry_price": "41.5"}])
    assert leg == Leg("CE", "SELL", Decimal("22500.00"), 65, 41.5) and leg.sign == -1
