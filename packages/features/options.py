from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from statistics import median

from pydantic import BaseModel

from packages.market_data.contracts import OptionChain, OptionContractQuote


class OptionChainFeatures(BaseModel):
    underlying: str
    expiry: date
    as_of: datetime
    put_call_ratio_oi: Decimal | None
    put_call_ratio_volume: Decimal | None
    call_open_interest: int | None
    put_open_interest: int | None
    call_open_interest_change: int | None
    put_open_interest_change: int | None
    call_volume: int | None
    put_volume: int | None
    atm_strike: Decimal | None
    atm_call_iv_pct: Decimal | None
    atm_put_iv_pct: Decimal | None
    atm_iv_skew_put_minus_call_points: Decimal | None
    largest_call_oi_strike: Decimal | None
    largest_put_oi_strike: Decimal | None
    largest_call_oi_share_pct: Decimal | None
    largest_put_oi_share_pct: Decimal | None
    two_sided_quote_count: int
    median_relative_spread_pct: Decimal | None
    contracts_with_delta: int
    contracts_with_gamma: int
    contracts_with_theta: int
    contracts_with_vega: int
    iv_rank_pct: Decimal | None
    iv_percentile_pct: Decimal | None
    term_structure: None = None
    unavailable: list[str]
    methodology: list[str]


def _sum_optional(values: list[int | None]) -> int | None:
    present = [value for value in values if value is not None]
    return sum(present) if present else None


def _side_total(contracts: list[OptionContractQuote], side: str, field: str) -> int | None:
    matching = [contract for contract in contracts if contract.option_type == side]
    values = [
        getattr(contract, field) if field != "volume" else contract.quote.volume
        for contract in matching
    ]
    if not matching or any(value is None for value in values):
        return None
    return _sum_optional(values)


def _oi_concentration(
    contracts: list[OptionContractQuote], side: str
) -> tuple[Decimal | None, Decimal | None]:
    by_strike: dict[Decimal, int] = defaultdict(int)
    matching = [contract for contract in contracts if contract.option_type == side]
    if not matching or any(contract.open_interest is None for contract in matching):
        return None, None
    for contract in matching:
        if contract.open_interest is not None:
            by_strike[contract.strike] += contract.open_interest
    total = sum(by_strike.values())
    if not by_strike or total == 0:
        return None, None
    strike, interest = max(by_strike.items(), key=lambda item: item[1])
    return strike, Decimal(interest) * 100 / total


def _greek_count(contracts: list[OptionContractQuote], greek: str) -> int:
    return sum(contract.greeks.get(greek) is not None for contract in contracts)


def calculate_option_features(chain: OptionChain) -> OptionChainFeatures:
    contracts = chain.contracts
    call_oi = _side_total(contracts, "CE", "open_interest")
    put_oi = _side_total(contracts, "PE", "open_interest")
    call_volume = _side_total(contracts, "CE", "volume")
    put_volume = _side_total(contracts, "PE", "volume")
    call_oi_change = _side_total(contracts, "CE", "open_interest_change")
    put_oi_change = _side_total(contracts, "PE", "open_interest_change")

    put_call_ratio_oi = (
        Decimal(put_oi) / Decimal(call_oi)
        if put_oi is not None and call_oi is not None and call_oi > 0
        else None
    )
    put_call_ratio_volume = (
        Decimal(put_volume) / Decimal(call_volume)
        if put_volume is not None and call_volume is not None and call_volume > 0
        else None
    )

    atm_strike = None
    atm_call_iv = atm_put_iv = atm_skew = None
    spot = chain.underlying_spot
    if spot is not None and contracts:
        strikes = sorted({contract.strike for contract in contracts})
        atm_strike = min(strikes, key=lambda strike: abs(strike - spot))
        atm = {
            contract.option_type: contract
            for contract in contracts
            if contract.strike == atm_strike
        }
        call = atm.get("CE")
        put = atm.get("PE")
        call_iv = call.implied_volatility if call is not None else None
        put_iv = put.implied_volatility if put is not None else None
        atm_call_iv = call_iv
        atm_put_iv = put_iv
        if isinstance(call_iv, Decimal) and isinstance(put_iv, Decimal):
            atm_skew = put_iv - call_iv

    largest_call_strike, largest_call_share = _oi_concentration(contracts, "CE")
    largest_put_strike, largest_put_share = _oi_concentration(contracts, "PE")
    relative_spreads: list[float] = []
    two_sided_quote_count = 0
    for contract in contracts:
        bid = contract.quote.bid
        ask = contract.quote.ask
        if bid is None or ask is None or bid <= 0 or ask <= 0 or bid > ask:
            continue
        two_sided_quote_count += 1
        midpoint = (bid + ask) / Decimal(2)
        relative_spreads.append(float((ask - bid) / midpoint * 100))
    median_spread = Decimal(str(median(relative_spreads))) if relative_spreads else None

    return OptionChainFeatures(
        underlying=chain.underlying,
        expiry=chain.expiry,
        as_of=chain.as_of,
        put_call_ratio_oi=put_call_ratio_oi,
        put_call_ratio_volume=put_call_ratio_volume,
        call_open_interest=call_oi,
        put_open_interest=put_oi,
        call_open_interest_change=call_oi_change,
        put_open_interest_change=put_oi_change,
        call_volume=call_volume,
        put_volume=put_volume,
        atm_strike=atm_strike,
        atm_call_iv_pct=atm_call_iv,
        atm_put_iv_pct=atm_put_iv,
        atm_iv_skew_put_minus_call_points=atm_skew,
        largest_call_oi_strike=largest_call_strike,
        largest_put_oi_strike=largest_put_strike,
        largest_call_oi_share_pct=largest_call_share,
        largest_put_oi_share_pct=largest_put_share,
        two_sided_quote_count=two_sided_quote_count,
        median_relative_spread_pct=median_spread,
        contracts_with_delta=_greek_count(contracts, "delta"),
        contracts_with_gamma=_greek_count(contracts, "gamma"),
        contracts_with_theta=_greek_count(contracts, "theta"),
        contracts_with_vega=_greek_count(contracts, "vega"),
        iv_rank_pct=None,
        iv_percentile_pct=None,
        unavailable=[
            "IV rank and IV percentile need stored historical option-chain snapshots.",
            "Term structure needs option-chain data for multiple expiries at a common timestamp.",
        ],
        methodology=[
            "Put/call ratios divide put totals by call totals across the returned contracts.",
            "Skew is ATM put IV minus call IV, in provider IV points.",
            "OI concentration is the largest strike's share of known open interest for that side.",
            "Median relative spread uses (ask - bid) / midpoint for valid two-sided quotes.",
        ],
    )
