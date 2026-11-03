"""The live night condor decision is the frozen research rule, and the runner trades it once."""

import asyncio
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from math import log, sqrt
from statistics import NormalDist

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from apps.night_runner.main import NightRunner, custom_name, setup
from packages.backtesting.history_backtest import _IST
from packages.backtesting.published_strategies import Bar, Book, Market
from packages.backtesting.rich_premium import (
    condor_strikes,
    read_richness,
    session_moves,
)
from packages.backtesting.rule_engine import ENGINE_RULES, EngineRule
from packages.database.base import Base
from packages.database.models import ResearchRuleVersion
from packages.market_data.contracts import (
    ExchangeSession,
    HistoricalBar,
    OptionChain,
    OptionContract,
    OptionContractQuote,
    Quote,
)
from packages.paper_trading.night_condor import NIGHT_EXIT_POLICY, chain_book, decide

_NORMAL = NormalDist()
_EXPIRY = date(2025, 3, 13)
_SESSIONS = tuple(day for offset in range(39)
                  if (day := date(2025, 2, 3) + timedelta(days=offset)).weekday() < 5)
_TODAY = date(2025, 3, 11)
_STRIKES = tuple(range(21000, 23050, 50))
_FORWARD = 22000.0


def _years(moment: datetime) -> float:
    expires = datetime.combine(_EXPIRY, time(15, 30), tzinfo=_IST)
    return (expires - moment).total_seconds() / (365 * 86400)


def _price(kind: str, forward: float, strike: float, moment: datetime) -> float:
    root = 0.15 * sqrt(_years(moment))
    d1 = log(forward / strike) / root + root / 2
    call = forward * _NORMAL.cdf(d1) - strike * _NORMAL.cdf(d1 - root)
    return max(call if kind == "CE" else call - forward + strike, 0.05)


def _book(sessions=_SESSIONS) -> Book:
    """Parity alternates every bar, with a wiggle that grows by day so variance is not flat."""
    bars: dict = {}
    for number, day in enumerate(sessions):
        wiggle = 0.0003 + 0.00002 * number
        for at in range(75):
            moment = datetime.combine(day, time(9, 15), tzinfo=_IST) + timedelta(minutes=5 * at)
            forward = _FORWARD * (1 + wiggle if at % 2 else 1)
            for strike in _STRIKES:
                for kind in ("CE", "PE"):
                    price = _price(kind, forward, float(strike), moment)
                    bars.setdefault((Decimal(strike), kind), {})[moment] = Bar(price, price,
                                                                               price, price)
    return Book(_EXPIRY, bars)


_FULL = _book()


def _chain(decided_at: datetime) -> OptionChain:
    moment = decided_at - timedelta(minutes=5)
    contracts = [
        OptionContractQuote(
            instrument_token=f"NSE_FO|{kind}{strike}", expiry=_EXPIRY, strike=strike,
            option_type=kind, quote=Quote(instrument_token=f"NSE_FO|{kind}{strike}",
                                          timestamp=decided_at,
                                          last_price=Decimal(str(series[moment].close))),
        )
        for (strike, kind), series in _FULL.bars.items()
    ]
    return OptionChain(underlying="NIFTY", underlying_spot=Decimal(22000), expiry=_EXPIRY,
                       as_of=decided_at, contracts=contracts)


def _rule(threshold: float, entry: str = "09:20", key: str = "test_night") -> EngineRule:
    return EngineRule(key, 1, date(2025, 1, 1), {
        "short_delta": 0.15, "threshold": threshold, "hold_overnight": True,
        "entry_time": entry, "into_expiry": True,
    })


def _past() -> list:
    return [move for move in session_moves(Market([_FULL], 0.0)) if move.session < _TODAY]


def test_live_decision_matches_the_research_rule_on_the_same_prices():
    decided_at = datetime.combine(_TODAY, time(9, 20), tzinfo=_IST)
    past = _past()
    expected = read_richness(_FULL, decided_at, past[-22:])
    assert expected is not None
    live = chain_book(_chain(decided_at), decided_at, (_TODAY, date(2025, 3, 12), _EXPIRY))
    chosen = decide(_rule(expected - 0.01), past, live, decided_at, date(2025, 3, 10),
                    date(2025, 3, 12))
    assert chosen.richness == pytest.approx(expected, rel=1e-12)
    assert chosen.strikes == condor_strikes(_FULL, decided_at, 0.15)
    assert chosen.strikes is not None
    skipped = decide(_rule(expected + 0.01), past, live, decided_at, date(2025, 3, 10),
                     date(2025, 3, 12))
    assert skipped.strikes is None and "below" in skipped.reason


def test_live_decision_fails_closed_on_gaps_and_before_a_break():
    decided_at = datetime.combine(_TODAY, time(9, 20), tzinfo=_IST)
    past = _past()
    live = chain_book(_chain(decided_at), decided_at, (_TODAY, date(2025, 3, 12), _EXPIRY))
    stale = decide(_rule(0.0), past[:-1], live, decided_at, date(2025, 3, 10), date(2025, 3, 12))
    assert stale.strikes is None and "incomplete" in stale.reason
    short = decide(_rule(0.0), past[-21:], live, decided_at, date(2025, 3, 10), date(2025, 3, 12))
    assert short.strikes is None
    # A holiday on Wednesday makes Tuesday the session before a break.
    gap = decide(_rule(0.0), past, live, decided_at, date(2025, 3, 10), _EXPIRY)
    assert gap.strikes is None and "break" in gap.reason


class FakeProvider:
    provider_id = "upstox"

    def __init__(self) -> None:
        self.history_calls = 0

    async def get_nfo_session(self, day: date):
        if day not in _SESSIONS:
            return None
        return ExchangeSession("NFO", datetime.combine(day, time(9, 15), tzinfo=_IST),
                               datetime.combine(day, time(15, 30), tzinfo=_IST))

    async def get_option_contracts(self, underlying: str, expiry=None):
        return [OptionContract(instrument_key=f"NSE_FO|{kind}{strike}",
                               trading_symbol=f"NIFTY{strike}{kind}", expiry=_EXPIRY,
                               strike=strike, option_type=kind, lot_size=65,
                               tick_size=Decimal("0.05"))
                for strike, kind in _FULL.bars]

    async def get_option_chain(self, underlying: str, expiry: date):
        return _chain(datetime.combine(_TODAY, time(9, 20), tzinfo=_IST))

    async def get_historical(self, key: str, start: datetime, end: datetime, interval: str):
        self.history_calls += 1
        kind, strike = key.split("|")[1][:2], Decimal(key.split("|")[1][2:])
        return [HistoricalBar(timestamp=moment, open=Decimal(str(bar.open)),
                              high=Decimal(str(bar.high)), low=Decimal(str(bar.low)),
                              close=Decimal(str(bar.close)), volume=1)
                for moment, bar in _FULL.bars[(strike, kind)].items()
                if start <= moment <= end]


@pytest.fixture
def factory(tmp_path, monkeypatch):
    # The synthetic expiry is still live for all its sessions, so fetch them all from the provider.
    monkeypatch.setattr("apps.night_runner.main.ACTIVE_LOOKBACK", timedelta(days=60))
    engine = create_engine(f"sqlite:///{(tmp_path / 'app.db').as_posix()}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _runner(factory, tmp_path, opened, rules):
    async def open_trade(body, user, session, key, provider, **options):
        opened.append((body, user.email, key, options))

    async def no_wait(_seconds):
        return None

    url = f"sqlite:///{(tmp_path / 'app.db').as_posix()}"
    return NightRunner(factory, FakeProvider(), "night@tradepilot.test", [url], factory,
                       open_trade, rules, no_wait)


def test_runner_opens_one_condor_in_the_override_account_and_never_repeats(factory, tmp_path):
    assert setup(factory, tmp_path / "night.json", "Night@TradePilot.test")
    assert not setup(factory, tmp_path / "night.json", "night@tradepilot.test")
    opened: list = []
    rule = _rule(0.0)
    runner = _runner(factory, tmp_path, opened, [rule])
    early = datetime.combine(_TODAY, time(9, 16), tzinfo=_IST)
    assert asyncio.run(runner.cycle(early)) == "READY"
    assert runner.state.moves is not None and len(runner.state.moves) >= 22
    at_entry = datetime.combine(_TODAY, time(9, 21), tzinfo=_IST)
    assert asyncio.run(runner.cycle(at_entry)) == "OPENED"
    assert asyncio.run(runner.cycle(at_entry + timedelta(minutes=1))) == "DONE"
    [(body, email, key, options)] = opened
    assert email == "night@tradepilot.test"
    assert key == "night_test_night_v1_20250311"
    assert options == {"exit_policy": NIGHT_EXIT_POLICY, "custom_name": custom_name(rule)}
    assert "not promoted" in custom_name(rule)
    assert [(leg.option_type, leg.side) for leg in body.custom_legs] == [
        ("CE", "SELL"), ("PE", "SELL"), ("CE", "BUY"), ("PE", "BUY")]
    assert [leg.strike for leg in body.custom_legs] == condor_strikes(_FULL, at_entry, 0.15)
    assert body.lots == 1 and body.strategy_key == "custom" and body.expiry == _EXPIRY


def test_runner_skips_a_demoted_version_and_retries_a_blocked_entry(factory, tmp_path):
    setup(factory, tmp_path / "night.json", "night@tradepilot.test")
    with factory() as session:
        session.add(ResearchRuleVersion(rule_key="test_night", version=1, params={},
                                        forward_from=date(2025, 1, 1), stage="DEMOTED",
                                        stage_since=datetime(2025, 3, 1, tzinfo=_IST),
                                        created_at=datetime(2025, 1, 1, tzinfo=_IST)))
        session.commit()
    opened: list = []
    runner = _runner(factory, tmp_path, opened, [_rule(0.0)])
    at_entry = datetime.combine(_TODAY, time(9, 21), tzinfo=_IST)
    assert asyncio.run(runner.cycle(at_entry)) == "DECIDED"
    assert opened == []

    attempts: list = []

    async def blocked(*args, **options):
        attempts.append(args[3])
        raise HTTPException(409, "Kill switch")

    # A version the engine has not demoted is still traded under the override.
    other = _runner(factory, tmp_path, [], [_rule(0.0, "09:30", "test_night_930")])
    other.open_trade = blocked
    second = datetime.combine(_TODAY, time(9, 31), tzinfo=_IST)
    assert asyncio.run(other.cycle(second)) == "BLOCKED"
    assert asyncio.run(other.cycle(second + timedelta(minutes=1))) == "BLOCKED"
    assert asyncio.run(other.cycle(second + timedelta(minutes=20))) == "DONE"
    # The same key each time, so a retry can never open a second condor.
    assert len(set(attempts)) == 1 and len(attempts) == 2


def test_runner_is_off_outside_the_entry_windows_and_on_closed_days(factory, tmp_path):
    runner = _runner(factory, tmp_path, [], [_rule(0.0)])
    assert asyncio.run(runner.cycle(datetime.combine(date(2025, 3, 8), time(9, 21),
                                                     tzinfo=_IST))) == "NO_SESSION"
    late = datetime.combine(_TODAY, time(11, 0), tzinfo=_IST)
    assert asyncio.run(runner.cycle(late)) == "DONE"
    assert runner.provider.history_calls == 0


def test_the_override_trades_only_the_two_engine_rules():
    assert [rule.key for rule in ENGINE_RULES] == ["very_rich_night_condor",
                                                   "very_rich_night_condor_920"]
    assert NIGHT_EXIT_POLICY.exits_next_session
    with pytest.raises(ValueError):
        type(NIGHT_EXIT_POLICY)(20, "unknown")


def test_runner_alerts_the_relay_account_once_per_topic_per_day(factory, tmp_path):
    from sqlalchemy import select

    from packages.database.models import UserNotification

    setup(factory, tmp_path / "relay.json", "phone@tradepilot.test")
    runner = _runner(factory, tmp_path, [], [_rule(0.0)])
    runner.alert_email = "Phone@TradePilot.test"
    runner.state.day = _TODAY
    runner.alert("data.x", "Skipped", "gap")
    runner.alert("data.x", "Skipped", "gap again")
    runner.alert("opened.x", "Opened", "strikes")
    with factory() as session:
        titles = [row.title for row in session.scalars(select(UserNotification))]
    assert sorted(titles) == ["Opened", "Skipped"]


def test_quote_evidence_reports_mid_and_age_for_each_leg():
    from apps.night_runner.main import quote_evidence

    decided_at = datetime.combine(_TODAY, time(9, 20), tzinfo=_IST)
    strikes = condor_strikes(_FULL, decided_at, 0.15)
    lines = quote_evidence(_chain(decided_at), strikes)
    assert len(lines) == 4 and all("ltp" in line and "age ?" in line for line in lines)
