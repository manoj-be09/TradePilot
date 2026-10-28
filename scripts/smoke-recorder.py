"""Bounded live smoke test of the Feed V3 recorder into a throwaway SQLite database.

Usage (inside the recorder image): python scripts/smoke-recorder.py --seconds 60
Read-only: authorizes the feed, subscribes, records, then prints a coverage summary.
"""

import argparse
import asyncio
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from packages.database import models  # noqa: F401 - register shared metadata
from packages.database.base import Base
from packages.database.recorder_models import MarketSnapshot1m, RecordedInstrument, RecorderEvent
from packages.market_data.contracts import ExchangeSession
from packages.market_data.provider import get_market_data_provider
from packages.market_data.upstox import UpstoxMarketDataProvider
from packages.recorder.service import INDIA, RecorderConfig, SessionRecorder
from packages.recorder.storage import RecorderStorage


async def main(seconds: int) -> None:
    provider = get_market_data_provider()
    if not isinstance(provider, UpstoxMarketDataProvider):
        raise SystemExit("Configured Upstox provider required")
    work = Path(tempfile.mkdtemp(prefix="recorder-smoke-"))
    engine = create_engine(f"sqlite:///{work / 'smoke.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    window = ExchangeSession("NFO", now - timedelta(minutes=1), now + timedelta(seconds=seconds))
    recorder = SessionRecorder(
        provider, RecorderStorage(factory, now.astimezone(INDIA).date()),
        RecorderConfig(data_dir=work, tail_minutes=0), window,
    )
    await recorder.run()
    with factory() as session:
        kinds = [row.kind for row in session.scalars(select(RecordedInstrument))]
        print("instruments:", {kind: kinds.count(kind) for kind in sorted(set(kinds))})
        print("snapshot rows:", session.scalar(select(func.count()).select_from(MarketSnapshot1m)))
        print("distinct instruments with data:", session.scalar(
            select(func.count(func.distinct(MarketSnapshot1m.instrument_key)))))
        print("events:", [(e.kind, e.detail) for e in session.scalars(
            select(RecorderEvent).order_by(RecorderEvent.occurred_at))])
        sample = session.scalars(select(MarketSnapshot1m).where(
            MarketSnapshot1m.iv.is_not(None)).limit(1)).first()
        if sample is not None:
            print("sample option:", sample.instrument_key, sample.ltp_close, sample.bid,
                  sample.ask, sample.open_interest, sample.iv, sample.delta)
    raw = sorted(work.rglob("*.frames.gz"))
    print("raw files:", [(p.name, p.stat().st_size) for p in raw], "frames:", recorder_frames(work))


def recorder_frames(work: Path) -> int:
    from packages.recorder.raw_store import iter_frames

    return sum(1 for path in work.rglob("*.frames.gz") for _ in iter_frames(path))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=int, default=60)
    asyncio.run(main(parser.parse_args().seconds))
