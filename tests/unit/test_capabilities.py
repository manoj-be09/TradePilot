from pydantic import SecretStr

from apps.api.capabilities import current_capabilities
from apps.api.settings import Settings


def test_capability_map_does_not_promise_market_entitlement_or_live_orders():
    matrix = current_capabilities(Settings(
        upstox_analytics_token=SecretStr("private-test-token"),
        paper_allow_unverified_quotes=False,
    ))
    by_type = {asset.instrument_type: asset for asset in matrix.assets}

    assert matrix.provider_configured is True
    assert matrix.provider_health_checked is False
    assert "entitlement" in matrix.note
    assert "private-test-token" not in matrix.model_dump_json()
    assert {asset.live_order for asset in matrix.assets} == {"disabled"}
    assert {asset.snapshot_quote for asset in matrix.assets} == {"requestable"}
    assert by_type["OPTION"].paper_entry == "safety_blocked"
    assert by_type["EQUITY"].paper_entry == "safety_blocked"


def test_capability_map_reflects_unconfigured_provider_and_educational_override():
    missing = current_capabilities(Settings(upstox_analytics_token=None))
    assert {asset.daily_history for asset in missing.assets} == {"unconfigured"}

    override = current_capabilities(Settings(
        upstox_analytics_token=SecretStr("private-test-token"),
        paper_allow_unverified_quotes=True,
    ))
    option = next(asset for asset in override.assets if asset.instrument_type == "OPTION")
    assert option.paper_entry == "educational_only"
    assert all(a.live_order == "disabled" for a in override.assets)
