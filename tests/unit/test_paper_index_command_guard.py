import pytest
from pydantic import ValidationError

from apps.api.paper_schemas import PaperTradeCreateRequest


def test_legacy_nifty_request_remains_valid():
    request = PaperTradeCreateRequest.model_validate({
        "strategy_key": "long_call", "expiry": "2026-10-19",
        "reference_strike": "22600", "lots": 1,
    })
    assert request.lots == 1


@pytest.mark.parametrize("selector", ["underlying_key", "index"])
def test_unsupported_index_cannot_be_silently_ignored(selector):
    with pytest.raises(ValidationError) as error:
        PaperTradeCreateRequest.model_validate({
            "strategy_key": "long_call", "expiry": "2026-10-19",
            "reference_strike": "22600", "lots": 1, selector: "SENSEX",
        })
    assert "Index-specific paper execution is not enabled yet" in str(error.value)


def test_saved_provider_and_exit_policy_metadata_remain_compatible():
    request = PaperTradeCreateRequest.model_validate({
        "strategy_key": "long_call", "expiry": "2026-10-19",
        "reference_strike": "22600", "lots": 1,
        "market_data_provider": "upstox", "exit_policy": {"version": "legacy"},
    })
    assert request.strategy_key == "long_call"


def test_explicit_nifty_retains_legacy_serialized_command():
    payload = {"strategy_key": "long_call", "expiry": "2026-10-19",
               "reference_strike": "22600", "lots": 1}
    legacy = PaperTradeCreateRequest.model_validate(payload).model_dump(mode="json")
    explicit = PaperTradeCreateRequest.model_validate({
        **payload, "underlying": "NIFTY",
    }).model_dump(mode="json")
    assert explicit == legacy
    assert "underlying" not in legacy
    selected = PaperTradeCreateRequest.model_validate({
        **payload, "underlying": "SENSEX",
    }).model_dump(mode="json")
    assert selected["underlying"] == "SENSEX"
