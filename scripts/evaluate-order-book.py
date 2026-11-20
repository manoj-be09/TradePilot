"""Usage: python scripts/evaluate-order-book.py data/orderbook

Does order-book pressure just before a 5m candle opens call its colour?
Reads the recorder's JSONL files. Candle colour comes from the NIFTY index prices recorded inside
each 5-minute bucket (first vs last second seen); buckets with a gap over 15 s are skipped.
Each signal is read from the last second before the bucket opens. With one signal and no fitting,
there is nothing to overfit: a signal's sign is fixed (more buyers -> green) before looking."""
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

rows = []
for f in sorted(Path(sys.argv[1]).glob("*.jsonl")):
    rows += [json.loads(line) for line in f.read_text().splitlines() if line.strip()]
rows.sort(key=lambda r: r[0])
buckets = defaultdict(list)
for r in rows:
    t = datetime.fromisoformat(r[0])
    buckets[t.replace(minute=t.minute - t.minute % 5, second=0)].append((t, r))


def imb(a, b):
    return (a - b) / (a + b) if a + b else 0.0


def signals(r, r60):
    """Positive = buyers stronger. r60 is the row about a minute earlier (or None)."""
    s = {"total qty (all waiting orders)": imb(r[5], r[6]),
         "top-5 depth qty": imb(r[7], r[8]),
         "top-5 order count": imb(r[9], r[10]),
         "futures last price vs mid": r[2] - (r[11] + r[12]) / 2}
    if r60:
        s["change in total qty, last minute"] = imb(r[5], r[6]) - imb(r60[5], r60[6])
        s["futures move, last minute"] = r[2] - r60[2]
    return s


results = defaultdict(lambda: [0, 0])
strong = defaultdict(lambda: [0, 0])
keys = sorted(buckets)
n = 0
for k in keys:
    secs = buckets[k]
    gaps = [(b[0] - a[0]).total_seconds() for a, b in zip(secs, secs[1:], strict=False)]
    if len(secs) < 120 or max(gaps, default=99) > 15 or (secs[0][0] - k).total_seconds() > 15:
        continue
    o, c = secs[0][1][1], secs[-1][1][1]
    if c == o:
        continue
    before = [x for x in rows if x[0] < k.isoformat()][-61:]
    if not before or (k - datetime.fromisoformat(before[-1][0])).total_seconds() > 10:
        continue
    r = before[-1]
    ago = (datetime.fromisoformat(r[0]) - timedelta(seconds=60)).isoformat()
    r60 = next((x for x in reversed(before) if x[0] <= ago), None)  # the reading a minute earlier
    if r[0][:10] != k.isoformat()[:10]:
        continue
    green = c > o
    n += 1
    for name, v in signals(r, r60).items():
        if v == 0:
            continue
        results[name][0] += (v > 0) == green
        results[name][1] += 1
        if abs(v) >= 0.2 or (name.startswith("futures") and abs(v) >= 3):
            strong[name][0] += (v > 0) == green
            strong[name][1] += 1
print(f"{n} complete candles from {len(rows)} readings")
for name in results:
    (h, t), (sh, st) = results[name], strong[name]
    extra = f"   | when strong: {100*sh/st:5.1f}% of {st}" if st else ""
    print(f"{name:36s} {100*h/t:5.1f}% of {t:3d}{extra}")
