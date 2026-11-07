"""Compare the broker's open NIFTY F&O positions with what TradePilot expects (LIVE_TRADING.md L3).

Usage (the token is the owner's order-scoped Upstox token, read from the environment of this
one command and never stored):
  UPSTOX_LIVE_READ_TOKEN=... PYTHONPATH=. python scripts/live-reconcile.py

Read-only GET calls. Expected positions come from the live order ledger, so any F&O
position opened outside TradePilot counts as a difference: use an account that holds
nothing else. Exit code 1 on any
difference, 2 if the broker could not be read. Contract notes are compared by hand for now.
"""

import asyncio
import json
import os
import sys

from apps.api.settings import get_settings
from packages.broker_upstox.ledger import expected_positions
from packages.broker_upstox.portfolio import PortfolioReader, reconcile_positions
from packages.database.session import build_session_factory
from packages.market_data.contracts import MarketDataProviderError


async def run() -> int:
    token = os.environ.get("UPSTOX_LIVE_READ_TOKEN", "").strip()
    if not token:
        print("UPSTOX_LIVE_READ_TOKEN is not set", file=sys.stderr)
        return 2
    reader = PortfolioReader(token)
    try:
        positions = await reader.fo_positions()
        funds = await reader.available_margin()
    except MarketDataProviderError as error:
        print(f"Could not read the broker: {error}", file=sys.stderr)
        return 2
    with build_session_factory(get_settings().database_url)() as session:
        differences = reconcile_positions(positions, expected_positions(session))
    print(json.dumps({
        "status": "MATCHED" if not differences else "MISMATCH",
        "available_margin": str(funds),
        "differences": [d.__dict__ for d in differences],
    }, indent=2))
    return 1 if differences else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
