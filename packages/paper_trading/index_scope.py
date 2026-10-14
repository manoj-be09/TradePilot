"""Recover the pinned option venue without substituting another index's market."""

from typing import Any

from packages.market_data.index_catalog import OptionIndex
from packages.paper_trading.session import PaperSessionError


def saved_option_index(
    underlying: str, snapshot: dict[str, Any], leg_keys: list[str]
) -> OptionIndex:
    if underlying == "NIFTY":
        index = OptionIndex(symbol="NIFTY", instrument_key="NSE_INDEX|Nifty 50",
                            name="Nifty 50", exchange="NSE", derivatives_exchange="NFO")
    else:
        try:
            index = OptionIndex.model_validate(snapshot.get("option_index"))
        except ValueError as error:
            raise PaperSessionError(
                "Index-specific paper close needs pinned index identity"
            ) from error
    venue = {"NSE": "NFO", "BSE": "BFO"}.get(index.exchange)
    if (
        index.symbol != underlying or index.derivatives_exchange != venue
        or not index.instrument_key.startswith(index.exchange + "_INDEX|")
        or not leg_keys
        or (snapshot.get("market_data_provider", "upstox") == "upstox"
            and any(not key.startswith(index.exchange + "_FO|") for key in leg_keys))
    ):
        raise PaperSessionError("Pinned index and option venue identities do not match")
    return index
