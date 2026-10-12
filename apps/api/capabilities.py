"""Honest workflow availability for the currently implemented asset classes."""

from typing import Literal

from pydantic import BaseModel

from apps.api.settings import Settings
from packages.market_data.contracts import MarketDataProvider

CapabilityState = Literal[
    "requestable", "unconfigured", "not_implemented", "safety_blocked",
    "educational_only", "disabled",
]


class AssetCapability(BaseModel):
    instrument_type: Literal["INDEX", "EQUITY", "ETF", "FUTURE", "OPTION"]
    discovery: CapabilityState
    snapshot_quote: CapabilityState
    daily_history: CapabilityState
    paper_entry: CapabilityState
    live_order: CapabilityState


class CapabilityMatrix(BaseModel):
    provider: str = "upstox"
    provider_configured: bool
    provider_health_checked: bool = False
    note: str = (
        "Requestable means a read-only API path and token are configured. It does not prove "
        "connectivity, market coverage, subscription entitlement, or quote freshness."
    )
    assets: list[AssetCapability]


def current_capabilities(
    settings: Settings, provider: MarketDataProvider | None = None
) -> CapabilityMatrix:
    token = settings.upstox_analytics_token
    configured = (
        provider.provider_id != "unconfigured" if provider is not None
        else token is not None and bool(token.get_secret_value().strip())
    )
    read_only: CapabilityState = "requestable" if configured else "unconfigured"
    if not settings.paper_trading_enabled:
        option_paper: CapabilityState = "disabled"
    elif settings.paper_allow_unverified_quotes:
        option_paper = "educational_only"
    else:
        option_paper = "safety_blocked"
    return CapabilityMatrix(
        provider=provider.provider_id if provider is not None else "upstox",
        provider_configured=configured,
        assets=[
            AssetCapability(
                instrument_type=kind,
                discovery=read_only,
                snapshot_quote=read_only,
                daily_history=read_only,
                paper_entry=(
                    option_paper if kind == "OPTION" else
                    ("safety_blocked" if settings.paper_trading_enabled else "disabled")
                    if kind in {"EQUITY", "ETF"} else "not_implemented"
                ),
                live_order="disabled",
            )
            for kind in ("INDEX", "EQUITY", "ETF", "FUTURE", "OPTION")
        ],
    )
