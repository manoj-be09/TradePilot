from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from packages.market_data.contracts import HistoricalBar
from packages.regime.outlook import describe_market

IST = ZoneInfo("Asia/Kolkata")
KEY = "NSE_INDEX|Nifty 50"


def bars(count: int = 130) -> list[HistoricalBar]:
    start = datetime(2026, 1, 1, tzinfo=IST)
    result = []
    for index in range(count):
        close = Decimal(20000 + index * 3 + (index % 7) * 4)
        result.append(HistoricalBar(
            timestamp=start + timedelta(days=index), open=close,
            high=close + 15, low=close - 15, close=close, volume=100,
        ))
    return result


def test_descriptive_readings_use_only_completed_sessions():
    history = bars()
    expected = history[-2].timestamp.date()
    result = describe_market(
        history, provider="test", instrument_key=KEY, expected_session=expected,
    )
    assert result.status == "ready"
    assert result.session_date == expected
    assert result.last_close == history[-2].close
    assert result.observations == len(history) - 1
    assert result.recent_direction == "Rising"
    assert result.relative_volatility_score is not None
    assert 0 <= result.relative_volatility_score <= 100


def test_missing_latest_session_and_short_history_abstain():
    history = bars()
    missing = describe_market(
        history[:-1], provider="test", instrument_key=KEY,
        expected_session=history[-1].timestamp.date(),
    )
    assert missing.status == "unavailable"
    assert "missing" in (missing.reason or "")
    short = describe_market(
        history[:50], provider="test", instrument_key=KEY,
        expected_session=history[49].timestamp.date(),
    )
    assert short.status == "unavailable"
    assert short.relative_volatility_score is None


def test_duplicate_or_malformed_daily_history_abstains():
    history = bars()
    duplicated = history[:-1] + [history[-2]]
    result = describe_market(
        duplicated, provider="test", instrument_key=KEY,
        expected_session=history[-2].timestamp.date(),
    )
    assert result.status == "unavailable"
    assert "duplicate" in (result.reason or "")
    malformed = history.copy()
    malformed[-1] = malformed[-1].model_copy(update={"high": Decimal(1)})
    result = describe_market(
        malformed, provider="test", instrument_key=KEY,
        expected_session=history[-1].timestamp.date(),
    )
    assert result.status == "unavailable"
    assert "invalid prices" in (result.reason or "")
