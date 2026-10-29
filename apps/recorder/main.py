"""Run with python -m apps.recorder.main; records NIFTY F&O Feed V3 data privately.

Read-only market data only: no order endpoints. Raw frames go to --data-dir and
must never be redistributed (Upstox private-use guidance, DATA_SOURCE_DECISION.md).
"""

import argparse
import asyncio
import logging
from datetime import date
from pathlib import Path

from packages.database.session import build_session_factory
from packages.market_data.provider import get_market_data_provider
from packages.market_data.upstox import UpstoxMarketDataProvider
from packages.recorder.service import RecorderConfig, rebuild_day, run_forever
from packages.recorder.storage import RecorderStorage
from packages.recorder.universe import UniverseConfig

logger = logging.getLogger("recorder")


def main() -> None:
    parser = argparse.ArgumentParser(description="Private NIFTY F&O Feed V3 recorder")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--weekly-expiries", type=int, default=3)
    parser.add_argument("--strikes-each-side", type=int, default=20)
    parser.add_argument(
        "--rebuild", type=date.fromisoformat, metavar="YYYY-MM-DD",
        help="Re-derive database rows for one recorded day from raw files, then exit",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # Request URLs are noise here and must never reach shared logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if args.rebuild is not None:
        day_dir = args.data_dir / "feed_v3" / args.rebuild.isoformat()
        frames, rows = rebuild_day(day_dir, RecorderStorage(build_session_factory(), args.rebuild))
        logger.info("Rebuilt %s: %s frames read, %s minute rows written (duplicates ignored)",
                    args.rebuild, frames, rows)
        return

    provider = get_market_data_provider()
    if not isinstance(provider, UpstoxMarketDataProvider):
        raise SystemExit("The recorder requires the configured read-only Upstox provider")
    factory = build_session_factory()
    config = RecorderConfig(
        data_dir=args.data_dir,
        universe=UniverseConfig(
            weekly_expiries=args.weekly_expiries, strikes_each_side=args.strikes_each_side,
        ),
    )

    def storage_for(day: date) -> RecorderStorage:
        return RecorderStorage(factory, day)

    try:
        asyncio.run(run_forever(provider, storage_for, config))
    except KeyboardInterrupt:
        logger.info("Recorder stopped")


if __name__ == "__main__":
    main()
