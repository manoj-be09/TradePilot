"""Compare the paper SPAN-style margin estimate with Upstox's margin for sample positions.

Usage:
  PYTHONPATH=. python scripts/compare-broker-margin.py --expiry 2026-11-03 [--step 50]

Builds five 1-lot NIFTY positions around the at-the-money strike (short call, short
straddle, bull put spread, iron condor, long front-month future), prices our estimate
from the current option chain, and asks Upstox's margin calculator for the same basket.
Upstox's call only calculates; nothing is ordered. Prints a JSON report with the gap per
position against the roadmap's ±5% calibration gate.
"""

import argparse
import asyncio
import json
from datetime import date
from decimal import Decimal
from typing import Literal

from apps.api.settings import get_settings
from packages.market_data.contracts import BrokerMarginLeg, OptionChain, OptionContractQuote
from packages.market_data.provider import get_market_data_provider
from packages.market_data.upstox import UpstoxMarketDataProvider
from packages.paper_trading.margin import (
    FALLBACK_VOLATILITY_PCT,
    MarginLeg,
    estimate_position_margin,
    futures_margin,
    is_expiry_day,
    money,
    years_to_expiry,
)

GATE_PCT = Decimal(5)
Side = Literal["BUY", "SELL"]
# (label, [(strike offset in steps, option type, side)])
POSITIONS: list[tuple[str, list[tuple[int, Literal["CE", "PE"], Side]]]] = [
    ("A short call", [(0, "CE", "SELL")]),
    ("B short straddle", [(0, "CE", "SELL"), (0, "PE", "SELL")]),
    ("C bull put spread", [(0, "PE", "SELL"), (-2, "PE", "BUY")]),
    ("D iron condor", [(-4, "PE", "SELL"), (-6, "PE", "BUY"),
                       (4, "CE", "SELL"), (6, "CE", "BUY")]),
]


def _contract(chain: OptionChain, strike: Decimal, kind: str) -> OptionContractQuote:
    for item in chain.contracts:
        if item.strike == strike and item.option_type == kind:
            return item
    raise SystemExit(f"No {strike} {kind} in the {chain.expiry} chain")


def _gap(ours: Decimal, broker: Decimal) -> dict[str, object]:
    pct = (ours - broker) / broker * 100 if broker > 0 else None
    return {
        "ours": str(ours), "upstox": str(broker),
        "gap_pct": str(pct.quantize(Decimal("0.1"))) if pct is not None else None,
        "within_gate": pct is not None and abs(pct) <= GATE_PCT,
    }


async def main(args: argparse.Namespace) -> None:
    settings = get_settings()
    provider = get_market_data_provider()
    if not isinstance(provider, UpstoxMarketDataProvider):
        raise SystemExit("No Upstox token is configured")
    parameters = settings.paper_margin_parameters
    chain = await provider.get_option_chain("NIFTY", args.expiry)
    if chain.underlying_spot is None:
        raise SystemExit("The chain has no underlying spot")
    spot = chain.underlying_spot
    step = Decimal(args.step)
    atm = (spot / step).quantize(Decimal(1)) * step
    lot = (await provider.get_option_contracts("NIFTY", args.expiry))[0].lot_size
    years, today = years_to_expiry(args.expiry), is_expiry_day(args.expiry)

    report: dict[str, object] = {
        "as_of": chain.as_of.isoformat(), "spot": str(spot), "atm": str(atm), "lot_size": lot,
        "expiry": args.expiry.isoformat(), "parameters": parameters.snapshot(), "positions": [],
    }
    rows: list[dict[str, object]] = []
    for label, spec in POSITIONS:
        legs, broker_legs = [], []
        for offset, kind, side in spec:
            item = _contract(chain, atm + offset * step, kind)
            legs.append(MarginLeg(
                instrument=kind, quantity_units=lot if side == "BUY" else -lot,
                underlying_price=spot, strike=item.strike, years_to_expiry=years,
                volatility_pct=item.implied_volatility or FALLBACK_VOLATILITY_PCT,
                expires_today=today,
            ))
            broker_legs.append(BrokerMarginLeg(
                instrument_key=item.instrument_token, quantity=lot, transaction_type=side,
            ))
        ours = estimate_position_margin(legs, parameters)
        broker = await provider.get_order_margin(broker_legs)
        # Upstox's total includes premium paid on long legs; ours keeps premium separate.
        premium = sum((leg.net_buy_premium for leg in broker.margins), Decimal(0))
        rows.append({
            "position": label,
            "legs": [f"{side} {atm + offset * step:.0f} {kind}" for offset, kind, side in spec],
            **_gap(ours.total, money(broker.final_margin - premium)),
            "upstox_span": str(sum((leg.span_margin for leg in broker.margins), Decimal(0))),
            "upstox_exposure": str(
                sum((leg.exposure_margin for leg in broker.margins), Decimal(0))
            ),
            "upstox_net_buy_premium": str(premium),
            "our_breakdown": ours.snapshot(),
        })

    futures = sorted(
        (row for row in await provider.search_instruments("NIFTY FUT", exchange="NSE",
                                                         segment="FO")
         if row.instrument_type == "FUT" and row.trading_symbol.startswith("NIFTY FUT ")
         and row.expiry is not None and row.expiry >= date.today() and row.lot_size),
        key=lambda row: row.expiry or date.today(),
    )
    if futures:
        front = futures[0]
        units = front.lot_size or lot
        quote = await provider.get_quote(front.instrument_key)
        ours = futures_margin(quote.last_price, units, parameters)
        broker = await provider.get_order_margin([BrokerMarginLeg(
            instrument_key=front.instrument_key, quantity=units, transaction_type="BUY",
        )])
        rows.append({
            "position": "E long future", "legs": [f"BUY {front.trading_symbol}"],
            **_gap(ours.total, broker.final_margin),
            "upstox_span": str(broker.margins[0].span_margin),
            "upstox_exposure": str(broker.margins[0].exposure_margin),
            "our_breakdown": ours.snapshot(),
        })
    report["positions"] = rows
    report["all_within_gate"] = all(row["within_gate"] for row in rows)
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Compare paper margin with Upstox's")
    parser.add_argument("--expiry", type=date.fromisoformat, required=True)
    parser.add_argument("--step", default="50")
    asyncio.run(main(parser.parse_args()))
