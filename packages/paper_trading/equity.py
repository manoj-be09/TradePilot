"""Versioned, long-only daily equity rules. No broker order path exists here."""

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_FLOOR, Decimal
from typing import Any
from zoneinfo import ZoneInfo

from packages.backtesting.performance import PerformanceTrade, summarize
from packages.market_data.contracts import HistoricalBar, Quote

ZONE = ZoneInfo("Asia/Kolkata")
VERSION = "equity-daily-v1"
FEE = Decimal("0.001")
SLIPPAGE = Decimal("0.0005")
CALCULATION_VERSION = "equity-calculations-v2"


@dataclass(frozen=True)
class EquityExitPolicy:
    """Bounded, versioned paper/replay exit rules; variants are research-only."""

    version: str
    atr_multiplier: Decimal
    minimum_distance_fraction: Decimal
    target_r: Decimal
    trail_activation_r: Decimal
    trail_distance_r: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.version, str) or not self.version or len(self.version) > 80:
            raise ValueError("A bounded policy version is required")
        bounds = (
            (self.atr_multiplier, Decimal("0.5"), Decimal("5")),
            (self.minimum_distance_fraction, Decimal("0.001"), Decimal("0.05")),
            (self.target_r, Decimal("0.5"), Decimal("5")),
            (self.trail_activation_r, Decimal("0.25"), Decimal("5")),
            (self.trail_distance_r, Decimal("0.25"), Decimal("5")),
        )
        if any(not value.is_finite() or not low <= value <= high
               for value, low, high in bounds):
            raise ValueError("Equity exit policy parameters are outside safe research bounds")


DEFAULT_EXIT_POLICY = EquityExitPolicy(
    version="equity-exit-v1",
    atr_multiplier=Decimal("2"),
    minimum_distance_fraction=Decimal("0.005"),
    target_r=Decimal("2"),
    trail_activation_r=Decimal("1"),
    trail_distance_r=Decimal("1"),
)


def position_exit_policy(position: dict[str, Any]) -> EquityExitPolicy:
    """Old open positions use their original v1 rules; unknown versions fail closed."""
    version = position.get("exit_policy_version", DEFAULT_EXIT_POLICY.version)
    fields = position.get("exit_policy")
    if fields is None:
        if version != DEFAULT_EXIT_POLICY.version:
            raise ValueError("Missing equity exit policy parameters")
        return DEFAULT_EXIT_POLICY
    if not isinstance(fields, dict):
        raise ValueError("Invalid equity exit policy parameters")
    try:
        policy = EquityExitPolicy(
            version=version,
            atr_multiplier=Decimal(fields["atr_multiplier"]),
            minimum_distance_fraction=Decimal(fields["minimum_distance_fraction"]),
            target_r=Decimal(fields["target_r"]),
            trail_activation_r=Decimal(fields["trail_activation_r"]),
            trail_distance_r=Decimal(fields["trail_distance_r"]),
        )
    except (KeyError, TypeError, ArithmeticError) as error:
        raise ValueError("Invalid equity exit policy parameters") from error
    if version == DEFAULT_EXIT_POLICY.version and policy != DEFAULT_EXIT_POLICY:
        raise ValueError("Default equity exit policy parameters do not match v1")
    return policy
STRATEGIES = {
    "trend": ("Trend continuation", "Close above rising 20-day average and 50-day average"),
    "breakout": ("Volume breakout", "Close above prior 20-day high; volume above 1.5× average"),
    "pullback": ("Trend pullback", "Recover above prior 5-day average while above 50-day average"),
    "reversion": ("Mean reversion", "Uptick after a close below the lower 20-day, 2σ band"),
    "momentum": ("Price momentum", "Over 3% 10-day momentum and a new 10-day closing high"),
    "channel20": ("20-day channel breakout · adapted", "Close above the previous 20-session high"),
    "channel55": ("55-day channel breakout · adapted", "Close above the previous 55-session high"),
    "connors_r2": ("Connors R2 pullback · adapted", "Three falling RSI(2) readings above SMA200"),
    "band_reclaim": ("Bollinger band reclaim · adapted", "Recover inside the lower 20-day band"),
    "sma_cross": ("20/50 SMA crossover · adapted", "20-day average crosses above 50-day average"),
}
ENTRY_CRITERIA = {
    "trend": "Latest close > current 20-close SMA > current 50-close SMA; "
             "the 20-close SMA must exceed its prior-session value.",
    "breakout": "Latest close > maximum high of the previous 20 bars; latest volume > "
                "1.5 times their average volume, which must be positive.",
    "pullback": "Latest close > current 50-close SMA; previous close <= prior-session "
                "5-close SMA < latest close. The threshold is the prior SMA, not today's SMA.",
    "reversion": "Previous close < its 20-close SMA minus twice the population standard "
                 "deviation; latest close > previous close. Re-entry inside the band is "
                 "not required by this v1 rule.",
    "momentum": "Latest close > 1.03 times the close 10 bars earlier and > every close "
                "in the previous 10 bars.",
    "channel20": "Latest completed close exceeds every high in the prior 20 sessions. "
                 "Daily long-only channel entry; no volume filter or Turtle winner-skip rule.",
    "channel55": "Latest completed close exceeds every high in the prior 55 sessions. "
                 "Daily long-only channel entry; no intraday trigger or pyramiding.",
    "connors_r2": "Latest close exceeds its 200-close SMA. Wilder-smoothed RSI(2) was below "
                  "65 two sessions ago and declined strictly on each subsequent close. "
                  "Platform entry waits for the following session, rather than buying the close.",
    "band_reclaim": "Previous close was below its 20-close SMA minus 2 population standard "
                    "deviations; latest close crosses above its own lower band and exceeds "
                    "the previous close. This is a platform rule, not a complete Bollinger system.",
    "sma_cross": "Prior 20-close SMA <= prior 50-close SMA, current 20-close SMA > current "
                 "50-close SMA, and latest close > current 20-close SMA. "
                 "Periods are platform choices.",
}
MINIMUM_BARS = {key: 200 if key == "connors_r2" else 56 if key == "channel55" else 51
                for key in STRATEGIES}
EXIT_CRITERIA = (
    "Shared by these platform equity rules: stop at entry minus 1R, target at entry plus 2R. "
    "After a +1R move, trail 1R behind the highest observed bid; the stop never loosens. "
    "Replay applies high-based trailing changes on the next bar. "
    "There is no separate indicator-based exit in these v1 rules."
)
STOP_CRITERIA = (
    "1R = max(2 times the simple average of 14 true ranges, 0.5% of signal close). "
    "ATR uses SMA smoothing. Quantity fits both allocation and the rounded net stop-loss "
    "scenario including illustrative fees and slippage. Gaps can exceed planned risk."
)


def money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))


def completed_bars(bars: list[HistoricalBar], now: datetime) -> list[HistoricalBar]:
    """Exclude today's partial daily bar; reject malformed/duplicate price history."""
    result = sorted(bars, key=lambda bar: bar.timestamp)
    seen: set[str] = set()
    accepted = []
    for bar in result:
        if bar.timestamp.tzinfo is None:
            raise ValueError("Daily candle time must include a timezone")
        day = bar.timestamp.astimezone(ZONE).date()
        if day >= now.astimezone(ZONE).date():
            continue
        if day.isoformat() in seen or not (
            0 < bar.low <= min(bar.open, bar.close) <= max(bar.open, bar.close) <= bar.high
        ):
            raise ValueError("Daily candle history is invalid or duplicated")
        seen.add(day.isoformat())
        accepted.append(bar)
    return accepted


def rsi2(closes: list[Decimal]) -> list[Decimal]:
    """Wilder RSI(2), seeded by two changes; flat series is neutral (50)."""
    if len(closes) < 3:
        return []
    changes = [b - a for a, b in zip(closes, closes[1:], strict=False)]
    gain = sum((max(x, Decimal(0)) for x in changes[:2]), Decimal(0)) / 2
    loss = sum((max(-x, Decimal(0)) for x in changes[:2]), Decimal(0)) / 2
    values = []
    for index in range(1, len(changes)):
        if index > 1:
            gain = (gain + max(changes[index], Decimal(0))) / 2
            loss = (loss + max(-changes[index], Decimal(0))) / 2
        values.append(Decimal(50) if gain == loss == 0 else 100 * gain / (gain + loss))
    return values


def signal(key: str, bars: list[HistoricalBar]) -> bool:
    if key not in STRATEGIES or len(bars) < MINIMUM_BARS[key]:
        return False
    closes = [b.close for b in bars]
    last, previous = closes[-1], closes[-2]
    mean20 = sum(closes[-20:], Decimal(0)) / 20
    mean50 = sum(closes[-50:], Decimal(0)) / 50
    if key == "trend":
        return last > mean20 > mean50 and mean20 > sum(closes[-21:-1], Decimal(0)) / 20
    if key == "breakout":
        volume = Decimal(sum(b.volume for b in bars[-21:-1])) / 20
        return (
            volume > 0
            and last > max(b.high for b in bars[-21:-1])
            and (bars[-1].volume > volume * Decimal("1.5"))
        )
    if key == "pullback":
        return last > mean50 and previous <= sum(closes[-6:-1], Decimal(0)) / 5 < last
    if key == "reversion":
        window = closes[-21:-1]
        avg = sum(window, Decimal(0)) / 20
        deviation = (sum(((x - avg) ** 2 for x in window), Decimal(0)) / 20).sqrt()
        return previous < avg - 2 * deviation and last > previous
    if key == "momentum":
        return last > closes[-11] * Decimal("1.03") and last > max(closes[-11:-1])
    if key in {"channel20", "channel55"}:
        lookback = 20 if key == "channel20" else 55
        return last > max(b.high for b in bars[-lookback - 1:-1])
    if key == "connors_r2":
        readings = rsi2(closes)
        return (last > sum(closes[-200:], Decimal(0)) / 200
                and readings[-1] < readings[-2] < readings[-3] < 65)
    if key == "sma_cross":
        prior20 = sum(closes[-21:-1], Decimal(0)) / 20
        prior50 = sum(closes[-51:-1], Decimal(0)) / 50
        return prior20 <= prior50 and last > mean20 > mean50
    # band_reclaim: both band values use only candles available at their own close.
    prior_window = closes[-21:-1]
    prior_mean = sum(prior_window, Decimal(0)) / 20
    prior_std = (sum(((x - prior_mean) ** 2 for x in prior_window), Decimal(0)) / 20).sqrt()
    current_std = (sum(((x - mean20) ** 2 for x in closes[-20:]), Decimal(0)) / 20).sqrt()
    return (previous < prior_mean - 2 * prior_std
            and last > mean20 - 2 * current_std and last > previous)


def entry_threshold(key: str, bars: list[HistoricalBar]) -> Decimal | None:
    """Return the latest close threshold used by a long entry rule, when defined.

    This is explanatory state for the paper UI; it does not change signal
    evaluation or make an entry eligible.
    """
    if key not in STRATEGIES or len(bars) < MINIMUM_BARS[key]:
        return None
    closes = [bar.close for bar in bars]
    previous = closes[-2]
    mean20 = sum(closes[-20:], Decimal(0)) / 20
    mean50 = sum(closes[-50:], Decimal(0)) / 50
    if key == "trend":
        return mean20
    if key == "breakout":
        return max(bar.high for bar in bars[-21:-1])
    if key == "pullback":
        return max(mean50, sum(closes[-6:-1], Decimal(0)) / 5)
    if key == "reversion":
        window = closes[-21:-1]
        avg = sum(window, Decimal(0)) / 20
        deviation = (sum(((x - avg) ** 2 for x in window), Decimal(0)) / 20).sqrt()
        return max(previous, avg - 2 * deviation)
    if key == "momentum":
        return max(closes[-11] * Decimal("1.03"), max(closes[-11:-1]))
    if key in {"channel20", "channel55"}:
        lookback = 20 if key == "channel20" else 55
        return max(bar.high for bar in bars[-lookback - 1:-1])
    if key == "connors_r2":
        return sum(closes[-200:], Decimal(0)) / 200
    if key == "sma_cross":
        return mean20
    current_std = (sum(((x - mean20) ** 2 for x in closes[-20:]), Decimal(0)) / 20).sqrt()
    return max(previous, mean20 - 2 * current_std)


def average_true_range(bars: list[HistoricalBar]) -> Decimal:
    if len(bars) < 15:
        raise ValueError("14 true ranges require at least 15 candles")
    ranges = [
        max(b.high - b.low, abs(b.high - a.close), abs(b.low - a.close))
        for a, b in zip(bars[-15:-1], bars[-14:], strict=True)
    ]
    return sum(ranges) / Decimal(14)


def risk_distance(
    bars: list[HistoricalBar], policy: EquityExitPolicy = DEFAULT_EXIT_POLICY
) -> Decimal:
    return max(
        average_true_range(bars) * policy.atr_multiplier,
        bars[-1].close * policy.minimum_distance_fraction,
    )


def open_position(
    price: Decimal, distance: Decimal, budget: Decimal, risk_budget: Decimal,
    *, policy: EquityExitPolicy = DEFAULT_EXIT_POLICY,
) -> dict[str, Any] | None:
    if any(not value.is_finite() or value <= 0 for value in (price, distance, budget, risk_budget)):
        return None
    entry = money(price * (1 + SLIPPAGE))
    if distance >= entry:
        return None
    # Size against the SAME rounded exit fill used by proceeds(), not an approximation.
    stop_fill = money((entry - distance) * (1 - SLIPPAGE))
    per_share_cost = entry * (1 + FEE)
    per_share_risk = per_share_cost - stop_fill * (1 - FEE)
    quantity = int(
        min(budget / per_share_cost, risk_budget / per_share_risk).to_integral_value(
            rounding=ROUND_FLOOR
        )
    )
    # Total cash flows round separately; verify the final monetary limits as well.
    while quantity > 0:
        cost = money(per_share_cost * quantity)
        stop_net = money(stop_fill * quantity * (1 - FEE))
        if cost <= budget and cost - stop_net <= risk_budget:
            break
        quantity -= 1
    if quantity < 1:
        return None
    position = {
        "calculation_version": CALCULATION_VERSION,
        "exit_policy_version": policy.version,
        "exit_policy": {
            "atr_multiplier": str(policy.atr_multiplier),
            "minimum_distance_fraction": str(policy.minimum_distance_fraction),
            "target_r": str(policy.target_r),
            "trail_activation_r": str(policy.trail_activation_r),
            "trail_distance_r": str(policy.trail_distance_r),
        },
        "reference_price": str(price),
        "entry_price": str(entry),
        "quantity": quantity,
        "cost": str(cost),
        "distance": str(distance),
        "stop": str(entry - distance),
        "target": str(entry + policy.target_r * distance),
        "high_water": str(entry),
        "trailing_active": False,
    }
    _, stop_net = proceeds(position, entry - distance)
    target_price = entry + policy.target_r * distance
    _, target_net = proceeds(position, target_price)
    position["stop_scenario_net"] = str(money(stop_net - cost))
    position["target_scenario_net"] = str(money(target_net - cost))
    stop_fill, _ = proceeds(position, entry - distance)
    target_fill, _ = proceeds(position, target_price)
    position["stop_exit_fill"] = str(stop_fill)
    position["target_exit_fill"] = str(target_fill)
    position["entry_fee"] = str(cost - entry * quantity)
    position["stop_exit_fee"] = str(stop_fill * quantity - stop_net)
    position["target_exit_fee"] = str(target_fill * quantity - target_net)
    return position


def exit_reason(position: dict[str, Any], price: Decimal) -> str | None:
    if price <= Decimal(position["stop"]):
        return "TRAILING_STOP" if position["trailing_active"] else "STOP_LOSS"
    if price >= Decimal(position["target"]):
        return "PROFIT_TARGET"
    return None


def trail(position: dict[str, Any], price: Decimal) -> dict[str, Any]:
    policy = position_exit_policy(position)
    updated = dict(position)
    high = max(Decimal(position["high_water"]), price)
    updated["high_water"] = str(high)
    if high >= (Decimal(position["entry_price"])
                + policy.trail_activation_r * Decimal(position["distance"])):
        updated["trailing_active"] = True
        updated["stop"] = str(max(
            Decimal(position["stop"]),
            high - policy.trail_distance_r * Decimal(position["distance"]),
        ))
    return updated


def proceeds(position: dict[str, Any], price: Decimal) -> tuple[Decimal, Decimal]:
    fill = money(price * (1 - SLIPPAGE))
    net = money(fill * position["quantity"] * (1 - FEE))
    return fill, net


def quote_problem(
    quote: Quote, key: str, now: datetime, *,
    indicative: bool = False, side: str = "BUY", quantity: int = 1,
) -> str | None:
    """Strict fills need exchange book time; opt-in indicative fills never claim it."""
    if quote.instrument_token != key:
        return "Quote instrument mismatch"
    if quantity < 1:
        return "Saved paper position has invalid quantity; manual review required"
    verified_book = quote.book_timestamp_basis == "exchange" and quote.book_as_of is not None
    if not verified_book and not indicative:
        return "Waiting for an exchange-timestamped bid/ask book"
    if not verified_book and quote.timestamp_basis != "provider_response":
        return "Indicative paper fill needs a provider response timestamp"
    stamps = (quote.timestamp, quote.book_as_of, quote.last_trade_time) if verified_book else (
        quote.timestamp, quote.last_trade_time,
    )
    for stamp in stamps:
        if stamp is None or stamp.tzinfo is None or not -5 <= (now - stamp).total_seconds() <= 60:
            return "Waiting for a verified exchange trade within 60 seconds"
    if not verified_book and not -5 <= (now - quote.timestamp).total_seconds() <= 5:
        return "Indicative paper fill needs a provider response within 5 seconds"
    if quote.bid is None or quote.ask is None or not 0 < quote.bid <= quote.ask:
        return "Waiting for a valid two-sided quote"
    if (quote.ask - quote.bid) / quote.ask > Decimal("0.005"):
        return "Waiting for spread at or below 0.5%"
    displayed = quote.ask_quantity if side == "BUY" else quote.bid_quantity
    if displayed is None or displayed < quantity:
        return "Paper fill needs enough displayed depth at the selected price"
    return None


@dataclass(frozen=True)
class ReplayTrade:
    signal_at: datetime
    entry_at: datetime
    exit_at: datetime
    net_pnl: Decimal
    initial_risk: Decimal | None = None


def replay_events(
    key: str, bars: list[HistoricalBar], budget: Decimal, risk_budget: Decimal,
    *, policy: EquityExitPolicy = DEFAULT_EXIT_POLICY,
) -> tuple[list[ReplayTrade], bool]:
    """Chronological daily OHLC replay; stop first on ambiguous bars; no look-ahead."""
    position = None
    signal_at = entry_at = None
    trades: list[ReplayTrade] = []
    for index in range(51, len(bars)):
        bar = bars[index]
        history = bars[:index]
        if position is None and signal(key, history):
            # The signal becomes actionable on the following candle, never its own close.
            if abs(bar.open / history[-1].close - 1) <= Decimal("0.02"):
                position = open_position(
                    bar.open, risk_distance(history, policy), budget, risk_budget,
                    policy=policy,
                )
                if position is not None:
                    signal_at, entry_at = history[-1].timestamp, bar.timestamp
        if position is None:
            continue
        stop, target = Decimal(position["stop"]), Decimal(position["target"])
        exit_price = None
        if bar.open <= stop:
            exit_price = bar.open
        elif bar.open >= target:
            exit_price = bar.open
        elif bar.low <= stop:
            exit_price = stop
        elif bar.high >= target:
            exit_price = target
        if exit_price is not None:
            _, net = proceeds(position, exit_price)
            assert signal_at is not None and entry_at is not None
            trades.append(ReplayTrade(
                signal_at=signal_at, entry_at=entry_at, exit_at=bar.timestamp,
                net_pnl=net - Decimal(position["cost"]),
                initial_risk=-Decimal(position["stop_scenario_net"]),
            ))
            position = None
            signal_at = entry_at = None
        else:
            # Today's high can tighten only tomorrow's stop; OHLC cannot establish tick order.
            position = trail(position, bar.high)
    return trades, position is not None


def replay(
    key: str, bars: list[HistoricalBar], budget: Decimal, risk_budget: Decimal
) -> dict[str, Any]:
    trades, open_trade_excluded = replay_events(key, bars, budget, risk_budget)
    return {
        **summarize([
            PerformanceTrade(trade.net_pnl, trade.initial_risk, trade.exit_at)
            for trade in trades
        ]),
        "open_trade_excluded": open_trade_excluded,
    }
