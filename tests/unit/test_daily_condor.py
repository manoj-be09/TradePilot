"""The daily-price condor reads only earlier sessions, stays inside its risk, and fails closed."""

import io
import zipfile
from datetime import date
from math import log

import pytest

from packages.backtesting.daily_condor import (
    FULL_WING_RUPEES,
    WING_SHARE,
    DailyMarket,
    DailyQuote,
    IndexDay,
    daily_readings,
    forecast,
    forward_from_open,
    nearest_expiry,
    read_opening,
    run_night_condor,
    session_variances,
    sessions_left,
    top_share_by_trailing_rank,
    trade_night_condor,
)
from packages.history.nse_daily import file_name, file_url, nifty_options, read_member

_DAYS = (date(2022, 3, 7), date(2022, 3, 8), date(2022, 3, 9), date(2022, 3, 10), date(2022, 3, 11),
         date(2022, 3, 14), date(2022, 3, 15), date(2022, 3, 16), date(2022, 3, 17))
_EXPIRY = date(2022, 3, 17)
_FLAT = 0.0


def _price(kind: str, strike: int, spot: float, session: date) -> float:
    """A simple smile-free shape: value falls away from the money and with time."""
    left = max(1, (_EXPIRY - session).days + 1)
    distance = strike - spot if kind == "CE" else spot - strike
    intrinsic = max(0.0, -distance)
    extrinsic = 40.0 * left ** 0.5 * 2.718 ** (-max(distance, 0.0) / 150)
    return round(intrinsic + max(0.05, extrinsic), 2)


def _chain(spot: float, session: date) -> dict[tuple[int, str], DailyQuote]:
    chain = {}
    for strike in range(int(spot) // 50 * 50 - 1000, int(spot) // 50 * 50 + 1050, 50):
        for kind in ("CE", "PE"):
            value = _price(kind, strike, spot, session)
            chain[(strike, kind)] = DailyQuote(value, value)
    return chain


def _market(spot: float = 15000.0, days: tuple[date, ...] = _DAYS) -> DailyMarket:
    index = {day: IndexDay(spot, spot * 1.005, spot * 0.995, spot) for day in days}
    chains = {day: {_EXPIRY: _chain(spot, day)} for day in days}
    return DailyMarket(days, chains, index)


def _no_spread(_: float) -> float:
    return 0.0


def test_variance_is_gap_squared_plus_the_range_estimate() -> None:
    market = DailyMarket(_DAYS[:2], {}, {
        _DAYS[0]: IndexDay(100.0, 101.0, 99.0, 100.0),
        _DAYS[1]: IndexDay(102.0, 104.0, 101.0, 103.0),
    })
    ranged = 0.5 * log(104 / 101) ** 2 - (2 * log(2) - 1) * log(103 / 102) ** 2
    expected = log(102 / 100) ** 2 + ranged
    assert session_variances(market)[_DAYS[1]] == pytest.approx(expected)


def test_forecast_needs_five_sessions_and_blends_last_week_month() -> None:
    assert forecast([0.1] * 4) is None
    past = [0.0] * 17 + [0.01] * 4 + [0.02]
    assert forecast(past) == pytest.approx((0.02 + (0.04 + 0.02) / 5 + 0.06 / 22) / 3)


def test_breaks_count_as_one_more_session() -> None:
    assert sessions_left(_market(), _DAYS[4], _EXPIRY) == 6
    assert sessions_left(_market(), _EXPIRY, _EXPIRY) == 1


def test_reading_uses_only_earlier_sessions() -> None:
    market = _market()
    baseline = daily_readings(market)
    changed_index = dict(market.index)
    changed_index[_DAYS[7]] = IndexDay(15000.0, 16000.0, 14000.0, 15000.0)
    changed = daily_readings(DailyMarket(market.sessions, market.chains, changed_index))
    assert [r.reading for r in baseline if r.session <= _DAYS[7]] == [
        r.reading for r in changed if r.session <= _DAYS[7]]
    assert [r.reading for r in baseline if r.session > _DAYS[7]] != [
        r.reading for r in changed if r.session > _DAYS[7]]


def test_session_before_a_break_is_marked_but_expiry_day_is_not() -> None:
    earlier = (date(2022, 3, 1), date(2022, 3, 2), date(2022, 3, 3), date(2022, 3, 4))
    days = (*earlier, *_DAYS, date(2022, 3, 21))
    market = _market(days=days)
    chains = {day: chain for day, chain in market.chains.items() if day != days[-1]}
    by_day = {r.session: r for r in daily_readings(DailyMarket(days, chains, market.index))}
    assert by_day[date(2022, 3, 11)].before_break is True
    assert by_day[date(2022, 3, 14)].before_break is False
    assert by_day[_EXPIRY].before_break is False


def test_reading_is_none_without_a_traded_centre() -> None:
    market = _market()
    chain = dict(market.chains[_DAYS[6]][_EXPIRY])
    chain[(15000, "CE")] = DailyQuote(None, None)
    chain[(15000, "PE")] = DailyQuote(None, None)
    thin = DailyMarket(market.sessions, {**market.chains, _DAYS[6]: {_EXPIRY: chain}}, market.index)
    assert forward_from_open(chain) is not None
    assert read_opening(thin, _DAYS[6], _EXPIRY, [0.0001] * 6) is None


@pytest.mark.parametrize("spot", [9000.0, 15000.0, 24500.0])
def test_scaled_wing_keeps_the_same_rupee_risk(spot: float) -> None:
    trade = trade_night_condor(_market(spot), _DAYS[2], 0.15, _no_spread)
    assert trade is not None
    call, put, far_call, far_put = trade.strikes
    assert far_call - call == put - far_put
    assert (far_call - call) * trade.units == pytest.approx(FULL_WING_RUPEES, rel=0.01)
    assert abs((far_call - call) / spot - WING_SHARE) * spot <= 25


def test_exit_is_next_session_close_or_today_on_expiry() -> None:
    market = _market()
    assert trade_night_condor(market, _DAYS[2], 0.15, _no_spread).exit_session == _DAYS[3]
    assert trade_night_condor(market, _EXPIRY, 0.15, _no_spread).exit_session == _EXPIRY


def _crashed_market(spot: float = 15000.0) -> DailyMarket:
    market = _market(spot)
    crash = spot * 0.85
    closes = {}
    for key in market.chains[_DAYS[3]][_EXPIRY]:
        value = _price(key[1], key[0], crash, _DAYS[3])
        closes[key] = DailyQuote(value, value)
    index = {**market.index, _DAYS[3]: IndexDay(crash, crash, crash, crash)}
    return DailyMarket(market.sessions, {**market.chains, _DAYS[3]: {_EXPIRY: closes}}, index)


def test_a_crash_cannot_lose_more_than_the_wing_and_charges() -> None:
    trade = trade_night_condor(_crashed_market(), _DAYS[2], 0.15, _no_spread)
    assert trade is not None
    assert trade.gross < 0
    assert trade.gross >= -FULL_WING_RUPEES * 1.05


def test_quiet_sold_leg_still_marks_at_value() -> None:
    market = _crashed_market()
    trade = trade_night_condor(market, _DAYS[2], 0.15, _no_spread)
    assert trade is not None
    _, put, _, far_put = trade.strikes
    exit_chain = dict(market.chains[_DAYS[3]][_EXPIRY])
    exit_chain[(put, "PE")] = DailyQuote(None, None)
    chains = {**market.chains, _DAYS[3]: {_EXPIRY: exit_chain}}
    quiet = DailyMarket(market.sessions, chains, market.index)
    result = trade_night_condor(quiet, _DAYS[2], 0.15, _no_spread)
    assert result is not None
    exit_chain[(far_put, "PE")] = DailyQuote(None, None)
    both = trade_night_condor(quiet, _DAYS[2], 0.15, _no_spread)
    assert both is not None and both.gross < result.gross


def test_wing_that_did_not_trade_skips_the_trade_when_fixed() -> None:
    market = _market()
    trade = trade_night_condor(market, _DAYS[2], 0.15, _no_spread, None)
    assert trade is not None
    chain = dict(market.chains[_DAYS[2]][_EXPIRY])
    chain[(trade.strikes[2], "CE")] = DailyQuote(None, 10.0)
    thin = DailyMarket(market.sessions, {**market.chains, _DAYS[2]: {_EXPIRY: chain}}, market.index)
    trades, skipped = run_night_condor(thin, [_DAYS[2]], 0.15, _no_spread, None)
    assert trades == [] and skipped == [_DAYS[2]]


def test_no_trade_after_the_last_session_or_without_a_chain() -> None:
    days = _DAYS[:3]
    market = _market(days=days)
    assert trade_night_condor(market, days[-1], 0.15, _no_spread) is None
    empty = DailyMarket(days, {}, market.index)
    assert nearest_expiry(empty, days[0]) is None
    assert trade_night_condor(empty, days[0], 0.15, _no_spread) is None


def test_spreads_only_ever_cost_money() -> None:
    plain = trade_night_condor(_market(), _DAYS[2], 0.15, _no_spread)
    wide = trade_night_condor(_market(), _DAYS[2], 0.15, lambda _: 5.0)
    assert plain is not None and wide is not None and wide.net < plain.net


def test_trailing_rank_sees_no_later_reading() -> None:
    market = _market()
    readings = [r for r in daily_readings(market)]
    assert top_share_by_trailing_rank(readings, 0.5, minimum=100) == set()
    first = top_share_by_trailing_rank(readings[:3], 1.0, minimum=1)
    assert first == {r.session for r in readings[1:3]}


def test_file_names_follow_the_two_formats() -> None:
    assert file_name(date(2023, 1, 2)) == "fo02JAN2023bhav.csv.zip"
    assert file_url(date(2024, 7, 5)).endswith(
        "/content/historical/DERIVATIVES/2024/JUL/fo05JUL2024bhav.csv.zip")
    assert file_url(date(2024, 7, 8)).endswith(
        "/content/fo/BhavCopy_NSE_FO_0_0_0_20240708_F_0000.csv.zip")


def _zip(members: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, text in members.items():
            archive.writestr(name, text)
    return buffer.getvalue()


def test_read_member_accepts_only_one_csv() -> None:
    assert read_member(_zip({"a.csv": "x"})) == "x"
    with pytest.raises(ValueError):
        read_member(_zip({"a.csv": "x", "b.csv": "y"}))
    with pytest.raises(ValueError):
        read_member(_zip({"a.py": "x"}))


_OLD = ("INSTRUMENT,SYMBOL,EXPIRY_DT,STRIKE_PR,OPTION_TYP,OPEN,HIGH,LOW,CLOSE,SETTLE_PR,CONTRACTS,"
        "VAL_INLAKH,OPEN_INT,CHG_IN_OI,TIMESTAMP,\n"
        "OPTIDX,NIFTY,05-Jan-2023,18000,CE,10,12,9,11,11,5,1,1,1,02-JAN-2023,\n"
        "OPTIDX,NIFTY,05-Jan-2023,18050,PE,0,0,0,7,7,0,0,1,0,02-JAN-2023,\n"
        "OPTIDX,BANKNIFTY,05-Jan-2023,40000,CE,1,1,1,1,1,1,1,1,1,02-JAN-2023,\n"
        "FUTIDX,NIFTY,25-Jan-2023,0,XX,1,1,1,1,1,1,1,1,1,02-JAN-2023,\n")
_NEW_FIELDS = ("TradDt,FinInstrmTp,TckrSymb,XpryDt,StrkPric,OptnTp,OpnPric,ClsPric,TtlTradgVol\n")


def test_old_format_reads_nifty_options_and_blanks_untraded_prices() -> None:
    rows = list(nifty_options(_OLD))
    assert [(row.strike, row.kind, row.open, row.close) for row in rows] == [
        (18000, "CE", 10.0, 11.0), (18050, "PE", None, None)]
    assert rows[0].expiry == date(2023, 1, 5)


def test_new_format_reads_the_same_fields() -> None:
    text = _NEW_FIELDS + "2024-09-30,IDO,NIFTY,2024-10-03,25000.00,PE,10.5,9.5,100\n"
    text += "2024-09-30,IDF,NIFTY,2024-10-31,,,1,1,1\n"
    rows = list(nifty_options(text))
    assert len(rows) == 1
    assert (rows[0].expiry, rows[0].strike, rows[0].kind, rows[0].open) == (
        date(2024, 10, 3), 25000, "PE", 10.5)


def test_unknown_format_raises() -> None:
    with pytest.raises(ValueError):
        list(nifty_options("a,b\n1,2\n"))
