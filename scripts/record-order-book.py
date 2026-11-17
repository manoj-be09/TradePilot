"""Record NIFTY futures order-book pressure once a second during market hours,
so 'buyers vs sellers' can be tested against the next candle's colour. Read-only quote GETs.
One JSON line per second: [time, nifty, fut_ltp, fut_volume, oi, total_buy, total_sell,
depth5_buy_qty, depth5_sell_qty, depth5_buy_orders, depth5_sell_orders, best_bid, best_ask].
Missing or failed quotes are skipped, never filled in. Stops after 15:30 IST."""
import asyncio
import json
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from packages.market_data.contracts import MarketDataProviderError, MarketDataRateLimited
from packages.market_data.provider import get_market_data_provider
from packages.recorder.universe import NIFTY_INDEX_KEY, nifty_futures

IST = timezone(timedelta(hours=5, minutes=30))
OUT = Path(sys.argv[1])


async def main() -> None:
    if date.today().weekday() >= 5:
        return  # weekend: nothing trades
    p = get_market_data_provider()
    while datetime.now(IST).time() < time(9, 14):
        await asyncio.sleep(20)
    found = await p.search_instruments("NIFTY FUT", exchange="NSE", segment="FO")
    fut = nifty_futures(found, date.today())[0]
    keys = [NIFTY_INDEX_KEY, fut.instrument_key]
    out = OUT / f"{date.today()}.jsonl"
    OUT.mkdir(parents=True, exist_ok=True)
    print("recording", fut.trading_symbol, "->", out, flush=True)
    n = 0
    while (now := datetime.now(IST)).time() < time(15, 30, 5):
        try:
            items = await p.get_quote_items(keys)
            nf, f = items[NIFTY_INDEX_KEY], items[fut.instrument_key]
            buy, sell = f["depth"]["buy"], f["depth"]["sell"]
            row = [now.isoformat(timespec="seconds"), nf["last_price"], f["last_price"],
                   f["volume"], f["oi"],
                   f["total_buy_quantity"], f["total_sell_quantity"],
                   sum(x["quantity"] for x in buy), sum(x["quantity"] for x in sell),
                   sum(x["orders"] for x in buy), sum(x["orders"] for x in sell),
                   buy[0]["price"], sell[0]["price"]]
            with out.open("a") as fh:
                fh.write(json.dumps(row) + "\n")
            n += 1
            if n % 600 == 0:
                print(now.time(), n, "rows", flush=True)
        except MarketDataRateLimited:
            await asyncio.sleep(5)  # back off rather than keep hitting the limit every second
            continue
        except (MarketDataProviderError, KeyError, IndexError, TypeError) as e:
            print(now.time(), "skip", type(e).__name__, str(e)[:80], flush=True)
        await asyncio.sleep(max(0.0, 1 - (datetime.now(IST) - now).total_seconds()))
    print("done", n, "rows", flush=True)


asyncio.run(main())
