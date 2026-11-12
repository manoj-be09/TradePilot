"""The live night condor decision is the frozen research rule, and the runner trades it once."""

import asyncio
from datetime import date, datetime, time, timedelta, timezone
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
            greeks={"delta": Decimal("0.1" if kind == "CE" else "-0.1"), "vega": Decimal(2)},
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


def _ledger(session, key: str, stage: str, override: bool = False, **params) -> None:
    from packages.database.models import LearningHypothesis

    session.add(LearningHypothesis(
        key=key, family="night", kind="rich_condor", origin="GRID", override=override,
        params={**_rule(0.0).params, **params}, forward_from=date(2025, 1, 1), rationale="t",
        preregistration={}, stage=stage, stage_since=datetime(2025, 1, 1, tzinfo=_IST),
        latest_check={}, created_at=datetime(2025, 1, 1, tzinfo=_IST)))


def test_the_probation_runner_trades_what_the_v2_ledger_allows(factory, tmp_path):
    from packages.learning.engine import tradable_rules

    setup(factory, tmp_path / "night.json", "night@tradepilot.test")
    with factory() as session:
        _ledger(session, "in_probation", "PROBATION")
        _ledger(session, "owner_pick", "SHADOW", override=True, entry_time="09:30")
        _ledger(session, "only_shadow", "SHADOW")
        _ledger(session, "retired", "RETIRED", override=True)
        _ledger(session, "same_day", "PROBATION", hold_overnight=False)
        session.commit()
        rules = tradable_rules(session)
    assert [(rule.key, rule.override) for rule in rules] == [("in_probation", False),
                                                             ("owner_pick", True)]

    opened: list = []
    runner = _runner(factory, tmp_path, opened, [])

    def source():
        with factory() as session:
            return tradable_rules(session)

    runner.rules_source = source
    at_entry = datetime.combine(_TODAY, time(9, 21), tzinfo=_IST)
    assert asyncio.run(runner.cycle(at_entry)) == "OPENED"
    [(_body, _email, key, options)] = opened
    assert key == "night_in_probation_v1_20250311"
    assert options["custom_name"] == "Probation · in probation v1"
    # Demoted in the ledger during the day: the 09:30 override is not traded.
    with factory() as session:
        from sqlalchemy import update

        from packages.database.models import LearningHypothesis

        session.execute(update(LearningHypothesis).where(LearningHypothesis.key == "owner_pick")
                        .values(stage="DEMOTED"))
        session.commit()
    later = datetime.combine(_TODAY, time(9, 31), tzinfo=_IST)
    assert asyncio.run(runner.cycle(later)) == "DECIDED"
    assert len(opened) == 1


def test_point_in_time_features_and_filters_fail_closed():
    from packages.learning.features import Context, features_at, passes, session_features

    decided_at = datetime.combine(_TODAY, time(9, 20), tzinfo=_IST)
    values = features_at(_FULL, None, decided_at, _past(), date(2025, 3, 12),
                         Context(frozenset({date(2025, 3, 12)}), {date(2025, 3, 10): 14.5}))
    assert values["realised_vol"] > 0 and values["skew"] > 0
    assert values["term_gap"] is None and values["vix"] == 14.5
    assert values["event_today"] is False and values["event_next"] is True
    assert passes([{"feature": "vix", "max": 15}, {"feature": "event_next", "equals": True}],
                  values)
    assert not passes([{"feature": "vix", "min": 15}], values)
    # Unreadable means no trade.
    assert not passes([{"feature": "term_gap", "max": 2}], values)
    # A book that stops before its expiry cannot say how long is left, so the gap is unread.
    cut = _book(tuple(day for day in _SESSIONS if day <= _TODAY))
    whole = features_at(_FULL, _FULL, decided_at, _past(), None, Context())
    assert whole["term_gap"] == pytest.approx(1.0)
    assert features_at(cut, _FULL, decided_at, _past(), None, Context())["term_gap"] is None
    by_day = session_features(Market([_FULL], 0.0), time(9, 20), Context())
    assert by_day[_TODAY]["realised_vol"] == pytest.approx(values["realised_vol"])


def test_the_runner_applies_a_hypothesis_filter_live(factory, tmp_path):
    from packages.learning.features import Context

    setup(factory, tmp_path / "night.json", "night@tradepilot.test")
    opened: list = []
    rule = EngineRule("filtered", 1, date(2025, 1, 1), {
        **_rule(0.0).params, "filters": [{"feature": "event_today", "equals": False}]}, False)
    runner = _runner(factory, tmp_path, opened, [rule])
    runner.context = Context(frozenset({_TODAY}))
    at_entry = datetime.combine(_TODAY, time(9, 21), tzinfo=_IST)
    asyncio.run(runner.cycle(at_entry))
    assert opened == []
    assert "Filters not met" in runner.state.decisions["filtered"].reason


def test_recorder_minutes_fill_a_past_session_the_history_api_has_not_served(
        factory, tmp_path):
    from packages.database.recorder_models import MarketSnapshot1m, RecordedInstrument

    # The provider serves nothing for the day before today; the recorder has every minute of it.
    runner = _runner(factory, tmp_path, [], [_rule(0.0)])
    previous = date(2025, 3, 10)
    served = runner.provider.get_historical

    async def lagging(key, start, end, interval):
        return [bar for bar in await served(key, start, end, interval)
                if bar.timestamp.astimezone(_IST).date() != previous]

    runner.provider.get_historical = lagging
    with factory() as session:
        for (strike, kind), series in _FULL.bars.items():
            key = f"NSE_FO|{kind}{strike}"
            session.add(RecordedInstrument(trading_day=previous, instrument_key=key, kind="OPT",
                                           expiry=_EXPIRY, strike=strike, option_type=kind,
                                           first_subscribed_at=datetime(2025, 3, 10,
                                                                        tzinfo=_IST)))
            for moment, bar in series.items():
                if moment.date() == previous:
                    price = Decimal(str(bar.close))
                    # The recorder writes UTC minutes.
                    utc = moment.astimezone(timezone.utc)
                    session.add(MarketSnapshot1m(
                        instrument_key=key, bucket_start=utc, trading_day=previous,
                        received_at=utc, ltp_open=Decimal(str(bar.open)),
                        ltp_high=Decimal(str(bar.high)), ltp_low=Decimal(str(bar.low)),
                        ltp_close=price, message_count=1))
        session.commit()
    early = datetime.combine(_TODAY, time(9, 16), tzinfo=_IST)
    assert asyncio.run(runner.cycle(early)) == "READY"
    assert runner.state.moves is not None and runner.state.moves[-1].session == previous
    assert [move.session for move in runner.state.moves] == [move.session for move in _past()]
