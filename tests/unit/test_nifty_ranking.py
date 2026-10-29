from datetime import datetime, timedelta, timezone
from decimal import Decimal

from packages.core.risk import AccountRiskState, RiskLimits
from packages.market_data.contracts import OptionChain, OptionContractQuote, Quote
from packages.market_data.quality import assess_option_chain
from packages.paper_trading.strategies import STRATEGY_REGISTRY
from packages.ranking.selector import RankingInputs, rank_nifty_strategies
from packages.ranking.specialists import (
    STANCE_STRATEGIES,
    VolatilityContext,
    mapped_keys,
    specialist_views,
)
from packages.regime.classifier import MarketRegimeResult

LIMITS = RiskLimits(
    max_open_trades=10, max_trade_risk_pct=2, max_portfolio_risk_pct=10, daily_loss_limit_pct=5,
)


def _account(initial: str = "1000000") -> AccountRiskState:
    return AccountRiskState(
        initial_cash=Decimal(initial), cash_balance=Decimal(initial), kill_switch_enabled=False,
        open_trade_max_losses=(), realized_pnl_today=Decimal(0),
    )


def _chain(now: datetime, *, exchange_book: bool = True, iv: int = 20) -> OptionChain:
    expiry = (now + timedelta(days=7)).date()
    contracts = []
    for strike in (90, 95, 100, 105, 110):
        for option_type in ("CE", "PE"):
            premium = (
                Decimal("6") if strike == 100
                else Decimal("3") if strike in (95, 105)
                else Decimal("1")
            )
            spread = Decimal("0.01") if premium == 1 else Decimal("0.1")
            token = f"{strike}-{option_type}"
            contracts.append(OptionContractQuote(
                instrument_token=token, expiry=expiry, strike=strike, option_type=option_type,
                quote=Quote(
                    instrument_token=token, timestamp=now, timestamp_basis="exchange",
                    book_as_of=now if exchange_book else None,
                    book_timestamp_basis="exchange" if exchange_book else "unknown",
                    last_price=premium, bid=premium, ask=premium + spread,
                    bid_quantity=200, ask_quantity=200, volume=100,
                ),
                open_interest=100, previous_open_interest=90, open_interest_change=10, change=0,
                bid_quantity=200, ask_quantity=200, implied_volatility=iv,
            ))
    chain = OptionChain(
        underlying="NIFTY", underlying_spot=100, expiry=expiry, as_of=now,
        timestamp_basis="exchange", contracts=contracts,
    )
    return chain.model_copy(update={"quality": assess_option_chain(chain)})


def _regime(now: datetime, label: str = "TRENDING_UP", bias: str = "UP",
            adx: int = 30, rv: str = "14") -> MarketRegimeResult:
    return MarketRegimeResult(
        as_of=now - timedelta(minutes=5), regime=label, directional_bias=bias, adx_14=adx,
        realized_volatility_percentile=50, realized_volatility_annualized_percent=Decimal(rv),
        prior_20_bar_high=Decimal(108), prior_20_bar_low=Decimal(92),
        reasons=["Synthetic classifier fixture"], method=["Unit-test fixture"],
    )


def _inputs(now: datetime, **overrides) -> RankingInputs:
    values = {
        "chain": _chain(now), "reference_strike": Decimal(100), "regime": _regime(now),
        "evaluated_at": now, "lot_size": 65, "account": _account(), "limits": LIMITS,
    }
    values.update(overrides)
    return RankingInputs(**values)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def test_every_paper_template_has_exactly_one_specialist():
    keys = list(mapped_keys())
    assert len(keys) == len(set(keys))
    assert set(keys) == {strategy.key for strategy in STRATEGY_REGISTRY.list()}


def test_uptrend_selects_a_bullish_template_and_never_states_pop():
    now = _now()
    result = rank_nifty_strategies(_inputs(now))
    views = {item.stance: item.conviction for item in result.specialists}
    assert views["BULLISH"] > 0 and views["BEARISH"] == 0 and views["RANGE"] == 0
    assert result.action == "PAPER_CANDIDATE"
    assert result.selected_strategy_key in STANCE_STRATEGIES["BULLISH"]
    assert result.probability_of_profit is None
    top = result.candidates[0]
    assert top.status == "ELIGIBLE" and top.strategy_key == result.selected_strategy_key
    assert top.estimated_round_trip_costs_per_lot and top.estimated_round_trip_costs_per_lot > 0
    assert top.maximum_loss_per_lot and top.maximum_loss_per_lot > 0
    assert len(result.candidates) == len(STRATEGY_REGISTRY.list())


def test_ranking_is_deterministic():
    now = _now()
    first = rank_nifty_strategies(_inputs(now))
    second = rank_nifty_strategies(_inputs(now))
    assert first.model_dump() == second.model_dump()


def test_unknown_regime_abstains():
    now = _now()
    result = rank_nifty_strategies(_inputs(now, regime=None))
    assert result.action == "NO_TRADE" and result.selected_strategy_key is None
    assert all(item.status != "ELIGIBLE" for item in result.candidates)
    assert all(item.conviction == 0 for item in result.specialists)


def test_missing_exchange_book_time_blocks_unless_locally_allowed():
    now = _now()
    blocked = rank_nifty_strategies(_inputs(now, chain=_chain(now, exchange_book=False)))
    assert blocked.action == "NO_TRADE"
    allowed = rank_nifty_strategies(_inputs(
        now, chain=_chain(now, exchange_book=False), allow_unverified_book_time=True,
    ))
    assert allowed.action == "PAPER_CANDIDATE"
    assert any("not exchange-verified" in reason for reason in allowed.reasons)


def test_account_risk_cap_blocks_every_candidate():
    now = _now()
    result = rank_nifty_strategies(_inputs(now, account=_account("1000")))
    assert result.action == "NO_TRADE"
    assert any(
        "per-trade risk cap" in reason
        for item in result.candidates for reason in item.reasons
    )


def test_missing_account_state_cannot_produce_a_candidate():
    now = _now()
    result = rank_nifty_strategies(_inputs(now, account=None))
    assert result.action == "NO_TRADE"


def test_stale_chain_blocks():
    now = _now()
    result = rank_nifty_strategies(_inputs(now, evaluated_at=now + timedelta(minutes=10)))
    assert result.action == "NO_TRADE"
    assert any("not current" in reason for reason in result.reasons)


def test_zero_weight_turns_off_a_specialist():
    now = _now()
    result = rank_nifty_strategies(_inputs(now, weights={"BULLISH": Decimal(0)}))
    assert result.action == "NO_TRADE"


def test_range_specialist_prefers_rich_options():
    now = _now()
    regime = _regime(now, "RANGE_BOUND", "NEUTRAL", adx=15)
    rich = specialist_views(regime, VolatilityContext(Decimal(18), Decimal(14)))
    cheap = specialist_views(regime, VolatilityContext(Decimal(12), Decimal(14)))
    rich_range = next(view for view in rich if view.stance == "RANGE")
    cheap_range = next(view for view in cheap if view.stance == "RANGE")
    assert rich_range.conviction > cheap_range.conviction
    assert all(view.conviction == 0 for view in rich if view.stance in ("BULLISH", "BEARISH"))


def test_volatility_specialist_backs_off_expensive_options():
    now = _now()
    regime = _regime(now, "HIGH_VOLATILITY", "NEUTRAL", adx=22)
    cheap = specialist_views(regime, VolatilityContext(Decimal(13), Decimal(14)))
    dear = specialist_views(regime, VolatilityContext(Decimal(20), Decimal(14)))
    assert next(v for v in cheap if v.stance == "VOLATILITY").conviction > next(
        v for v in dear if v.stance == "VOLATILITY"
    ).conviction


def test_unlimited_upside_is_valued_at_a_one_sigma_move_not_capped_maximum():
    now = _now()
    result = rank_nifty_strategies(_inputs(now))
    long_call = next(item for item in result.candidates if item.strategy_key == "long_call")
    spread = next(item for item in result.candidates if item.strategy_key == "bull_call_spread")
    assert long_call.maximum_profit_unbounded
    assert long_call.reward_basis == "ONE_SIGMA_MOVE"
    assert spread.reward_basis == "EXPIRY_MAXIMUM"
    # A 1-sigma move (about 2.8 points here) does not recover the 6.1 premium.
    assert long_call.status != "ELIGIBLE"
    assert any("exceed the modeled reward" in reason for reason in long_call.reasons)
