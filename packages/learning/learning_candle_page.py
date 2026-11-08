"""One self-refreshing HTML page for L-Earning Candle, written next to the learner's state."""

from datetime import datetime

from packages.learning.learning_candle import FEATURES, IST, Learner

_LABELS = {
    "body": "Last candle body", "upper_wick": "Upper wick", "lower_wick": "Lower wick",
    "ret_1": "Move, 1 candle", "ret_3": "Move, 3 candles", "ret_6": "Move, 6 candles",
    "streak": "Colour streak", "rsi_14": "RSI 14", "ema_9_21_gap": "EMA 9 vs 21",
    "day_position": "Place in day range", "from_day_open": "Move from day open",
    "time_of_day": "Time of day", "range_expansion": "Range expansion",
    "vix_change": "VIX change", "vix_level": "VIX level",
}
_WINDOW = 50


def _pct(part: float, whole: float) -> str:
    return f"{100 * part / whole:.1f}%" if whole else "–"


def _trend(history: list[list[object]]) -> str:
    """Rolling hit rate over the last _WINDOW guesses, as an SVG line against the 50% mark."""
    hits = [int((float(str(p)) >= 0.5) == actual) for _, p, actual in history]
    if len(hits) <= _WINDOW:
        return '<p class="muted">The trend appears after 50 scored candles.</p>'
    rolling = [sum(hits[i - _WINDOW:i]) / _WINDOW for i in range(_WINDOW, len(hits) + 1)]
    width, height, low, high = 640, 160, 0.3, 0.7
    def y(v: float) -> float:
        return height - (min(high, max(low, v)) - low) / (high - low) * height
    step = width / max(1, len(rolling) - 1)
    points = " ".join(f"{i * step:.1f},{y(v):.1f}" for i, v in enumerate(rolling))
    return (f'<svg viewBox="0 0 {width} {height}" class="trend" role="img" '
            f'aria-label="Rolling {_WINDOW}-candle hit rate, latest {rolling[-1]:.0%}">'
            f'<line x1="0" x2="{width}" y1="{y(0.5)}" y2="{y(0.5)}" class="mid"/>'
            f'<text x="4" y="{y(0.5) - 6}" class="axis">50% coin flip</text>'
            f'<polyline points="{points}" class="line"/></svg>')


def _strip(history: list[list[object]]) -> str:
    tiles = []
    for start, p, actual in history[-60:]:
        guess = float(str(p)) >= 0.5
        when = datetime.fromisoformat(str(start)).astimezone(IST).strftime("%d %b %H:%M")
        right = guess == actual
        tiles.append(
            f'<span class="tile {"g" if actual else "r"} {"" if right else "miss"}" '
            f'title="{when}: guessed {"green" if guess else "red"} ({float(str(p)):.2f}), '
            f'was {"green" if actual else "red"} - {"right" if right else "wrong"}">'
            f'{"" if right else "&times;"}</span>')
    return "".join(tiles) or '<p class="muted">No candles scored yet.</p>'


def _weights(learner: Learner) -> str:
    biggest = max((abs(w) for w in learner.weights), default=0) or 1
    rows = []
    for name, w in sorted(zip(FEATURES, learner.weights, strict=True), key=lambda kv: -abs(kv[1])):
        side = "pos" if w >= 0 else "neg"
        rows.append(f'<div class="w"><span>{_LABELS[name]}</span><span class="bar">'
                    f'<i class="{side}" style="width:{50 * abs(w) / biggest:.1f}%"></i></span>'
                    f'<span class="num">{w:+.2f}</span></div>')
    return "".join(rows)


def render(learner: Learner, next_p: float | None, next_at: datetime | None,
           updated: datetime) -> str:
    if next_p is None or next_at is None:
        hero = ('<div class="call idle">Waiting</div><p class="muted">No candle to call right '
                'now. The next guess comes when the market is open.</p>')
    else:
        green = next_p >= 0.5
        sure = abs(next_p - 0.5) * 2
        hero = (f'<p class="eyebrow">Candle starting {next_at.astimezone(IST):%H:%M}</p>'
                f'<div class="call {"g" if green else "r"}">{"Green" if green else "Red"}</div>'
                f'<div class="meter"><i style="width:{100 * next_p:.0f}%"></i></div>'
                f'<p class="muted">Chance of green {next_p:.0%} &middot; '
                f'{"barely leaning" if sure < 0.1 else "leaning" if sure < 0.3 else "fairly sure"}'
                f'</p>')
    recent = learner.recent
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="30">
<title>L-Earning Candle</title>
<style>
:root {{ --bg:#f6f5f2; --card:#fff; --ink:#1d1d1f; --muted:#6e6e73; --line:#e4e2dd;
  --green:#1a9e5c; --red:#d64545; --accent:#3b5bdb; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#111214; --card:#1a1b1e; --ink:#ececee;
  --muted:#9a9aa1; --line:#2a2b2f; --green:#34c77b; --red:#ef6464; --accent:#7c93ff; }} }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--ink);
  font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif; }}
main {{ max-width:960px; margin:0 auto; padding:32px 20px 48px; }}
header {{ display:flex; justify-content:space-between; align-items:baseline; gap:12px;
  flex-wrap:wrap; margin-bottom:24px; }}
h1 {{ margin:0; font-size:26px; letter-spacing:-0.02em; }}
h1 b {{ color:var(--accent); }}
h2 {{ margin:0 0 14px; font-size:13px; text-transform:uppercase; letter-spacing:.08em;
  color:var(--muted); font-weight:600; }}
.muted {{ color:var(--muted); margin:6px 0 0; }}
.tag {{ font-size:12px; padding:4px 10px; border-radius:99px; border:1px solid var(--line);
  color:var(--muted); }}
.grid {{ display:grid; grid-template-columns:1.1fr 1fr; gap:16px; }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:16px; padding:22px; }}
.wide {{ grid-column:1 / -1; }}
.eyebrow {{ margin:0 0 4px; color:var(--muted); font-size:13px; }}
.call {{ font-size:56px; font-weight:700; letter-spacing:-0.03em; line-height:1.1; }}
.call.g {{ color:var(--green); }} .call.r {{ color:var(--red); }}
.call.idle {{ color:var(--muted); font-size:40px; }}
.meter {{ height:8px; border-radius:99px; background:var(--red); margin:14px 0 4px;
  overflow:hidden; }}
.meter i {{ display:block; height:100%; background:var(--green); }}
.stats {{ display:grid; grid-template-columns:1fr 1fr; gap:14px; }}
.stat b {{ display:block; font-size:28px; letter-spacing:-0.02em; }}
.stat span {{ color:var(--muted); font-size:13px; }}
.strip {{ display:flex; flex-wrap:wrap; gap:5px; }}
.tile {{ width:22px; height:22px; border-radius:6px; display:grid; place-items:center;
  color:#fff; font-size:15px; font-weight:700; }}
.tile.g {{ background:var(--green); }} .tile.r {{ background:var(--red); }}
.tile.miss {{ opacity:.35; color:var(--ink); }}
.legend {{ font-size:13px; color:var(--muted); margin-top:10px; }}
.trend {{ width:100%; height:auto; display:block; }}
.trend .mid {{ stroke:var(--line); stroke-dasharray:4 4; }}
.trend .line {{ fill:none; stroke:var(--accent); stroke-width:2.5; stroke-linejoin:round; }}
.trend .axis {{ fill:var(--muted); font-size:12px; }}
.w {{ display:grid; grid-template-columns:170px 1fr 52px; gap:10px; align-items:center;
  padding:5px 0; font-size:14px; }}
.bar {{ position:relative; height:10px; background:linear-gradient(var(--line),var(--line))
  center/1px 100% no-repeat; }}
.bar i {{ position:absolute; top:0; height:100%; border-radius:3px; }}
.bar i.pos {{ left:50%; background:var(--green); }}
.bar i.neg {{ right:50%; background:var(--red); }}
.num {{ text-align:right; color:var(--muted); font-variant-numeric:tabular-nums; }}
footer {{ margin-top:24px; font-size:12px; color:var(--muted); }}
@media (max-width:720px) {{ .grid {{ grid-template-columns:1fr; }} .call {{ font-size:44px; }}
  .w {{ grid-template-columns:120px 1fr 44px; }} }}
</style></head>
<body><main>
<header><h1>L-<b>Earning</b> Candle</h1>
<span class="tag">NIFTY 50 &middot; 5-minute candles &middot; simulated</span></header>
<div class="grid">
<section class="card"><h2>Next call</h2>{hero}</section>
<section class="card"><h2>Scoreboard</h2><div class="stats">
<div class="stat"><b>{_pct(learner.right, learner.played)}</b><span>right, all time</span></div>
<div class="stat"><b>{_pct(sum(recent), len(recent))}</b>
<span>right, last {len(recent)}</span></div>
<div class="stat"><b>{_pct(learner.repeat_right, learner.played)}</b>
<span>&ldquo;repeat last colour&rdquo; baseline</span></div>
<div class="stat"><b>{learner.played:,}</b><span>candles called</span></div>
</div></section>
<section class="card wide"><h2>Recent calls</h2><div class="strip">{_strip(learner.history)}</div>
<p class="legend">Each tile is a finished candle in its real colour. Faded with &times; means the
call was wrong. Hover for the time and the call.</p></section>
<section class="card wide"><h2>Hit rate, rolling {_WINDOW} candles</h2>
{_trend(learner.history)}</section>
<section class="card wide"><h2>What it has learned</h2>{_weights(learner)}
<p class="legend">Green bars push the call towards green, red bars towards red. Bigger bars
matter more.</p></section>
</div>
<footer>Updated {updated.astimezone(IST):%d %b %Y %H:%M:%S} IST &middot;
A learning experiment on past and live candles. Simulated results only;
not a trading signal or investment advice.</footer>
</main></body></html>
"""
