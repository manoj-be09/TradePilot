"""The live far put spread uses the research strike rule and opens once, on the entry session."""

import asyncio
from datetime import date, datetime, time, timedelta

from test_night_condor import _EXPIRY, _FULL, FakeProvider, _chain, factory  # noqa: F401

from apps.far_put_runner.main import (
    EXIT_POLICY,
    NAME,
    FarPutRunner,
    choose_strikes,
)
from apps.night_runner.main import setup
from packages.backtesting.global_strategies import strike_at_delta
from packages.backtesting.history_backtest import _IST

# Expiry is Thursday 13 March 2025; three days before is Monday 10 March, a session.
_ENTRY_DAY = date(2025, 3, 10)


class EntryDayProvider(FakeProvider):
    async def get_option_chain(self, underlying, expiry):
        return _chain(datetime.combine(_ENTRY_DAY, time(9, 20), tzinfo=_IST))


def _runner(factory, opened):  # noqa: F811
    async def open_trade(body, user, session, key, provider, **options):
        opened.append((body, user.email, key, options))

    return FarPutRunner(factory, EntryDayProvider(), "FarPut@TradePilot.test", open_trade)


def test_live_strikes_match_the_research_rule_and_never_leave_a_naked_put():
    decided_at = datetime.combine(_ENTRY_DAY, time(9, 20), tzinfo=_IST)
    sold, bought = choose_strikes(_chain(decided_at), decided_at)
    assert sold == strike_at_delta(_FULL, "PE", decided_at, 22000.0, 0.05)
    assert sold - bought == 300 and sold < 22000
    missing_wing = _chain(decided_at).model_copy(update={"contracts": [
        item for item in _chain(decided_at).contracts
        if not (item.option_type == "PE" and item.strike == bought)]})
    assert choose_strikes(missing_wing, decided_at) is None


def test_runner_opens_one_spread_on_the_entry_session_and_holds_to_expiry(factory, tmp_path):  # noqa: F811
    setup(factory, tmp_path / "far.json", "farput@tradepilot.test")
    opened: list = []
    runner = _runner(factory, opened)
    at = datetime.combine(_ENTRY_DAY, time(9, 21), tzinfo=_IST)
    assert asyncio.run(runner.cycle(at - timedelta(minutes=3))) == "WAITING"
    assert asyncio.run(runner.cycle(at)) == "OPENED"
    assert asyncio.run(runner.cycle(at + timedelta(minutes=1))) == "DONE"
    [(body, email, key, options)] = opened
    assert email == "farput@tradepilot.test" and key == "far_put_v1_20250313"
    assert options == {"exit_policy": EXIT_POLICY, "custom_name": NAME}
    assert EXIT_POLICY.square_off_minutes_before_close == 25 and "not promoted" in NAME
    assert [(leg.option_type, leg.side) for leg in body.custom_legs] == [
        ("PE", "SELL"), ("PE", "BUY")]
    assert body.expiry == _EXPIRY and body.lots == 1


def test_runner_trades_only_the_first_session_within_three_days_and_only_in_the_window(
    factory, tmp_path  # noqa: F811
):
    setup(factory, tmp_path / "far.json", "farput@tradepilot.test")
    opened: list = []
    later = _runner(factory, opened)
    assert asyncio.run(later.cycle(
        datetime.combine(date(2025, 3, 11), time(9, 21), tzinfo=_IST))) == "NOT_ENTRY_DAY"
    early_week = _runner(factory, opened)
    assert asyncio.run(early_week.cycle(
        datetime.combine(date(2025, 3, 7), time(9, 21), tzinfo=_IST))) == "NOT_ENTRY_DAY"
    late = _runner(factory, opened)
    assert asyncio.run(late.cycle(
        datetime.combine(_ENTRY_DAY, time(9, 45), tzinfo=_IST))) == "WINDOW_PASSED"
    assert asyncio.run(late.cycle(
        datetime.combine(date(2025, 3, 8), time(9, 21), tzinfo=_IST))) == "NO_SESSION"
    assert opened == []
