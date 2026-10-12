"use client";

import { useEffect, useMemo, useState } from "react";

import { rsi14 } from "./chartIndicators";

type Bar = {
  timestamp: string;
  open: string;
  high: string;
  low: string;
  close: string;
  volume: number;
};

type LiveQuote = {
  last_price: string;
  timestamp: string;
  timestamp_basis: string;
};

function price(value: string | number): string {
  return Number(value).toLocaleString("en-IN", { maximumFractionDigits: 2 });
}

function day(value: string): string {
  return new Date(value).toLocaleDateString("en-IN", {
    day: "numeric", month: "short", timeZone: "Asia/Kolkata",
  });
}

function barTime(value: string, interval: string): string {
  if (interval === "1d") return day(value);
  return new Date(value).toLocaleString("en-IN", {
    day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit",
    hour12: false, timeZone: "Asia/Kolkata",
  });
}

export function HistoricalPriceChart({
  instrumentKey, providerId, symbol, token, apiUrl, onClose,
}: {
  instrumentKey: string;
  providerId: string;
  symbol: string;
  token: string;
  apiUrl: string;
  onClose?: () => void;
}) {
  const [bars, setBars] = useState<Bar[]>([]);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const [dataWarning, setDataWarning] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [rangeDays, setRangeDays] = useState(45);
  const [interval, setInterval] = useState<"15m" | "1h" | "1d">("1d");
  const [chartStyle, setChartStyle] = useState<"candles" | "line">("candles");
  const [indicator, setIndicator] = useState<"none" | "sma20" | "ema20" | "both" | "rsi14">("none");
  const [selectedTimestamp, setSelectedTimestamp] = useState<string | null>(null);
  const [live, setLive] = useState(true);
  const [liveQuote, setLiveQuote] = useState<LiveQuote | null>(null);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);

  useEffect(() => {
    if (!live) return;
    const timer = window.setInterval(() => setRefresh((value) => value + 1), 15_000);
    return () => window.clearInterval(timer);
  }, [live]);

  useEffect(() => {
    const controller = new AbortController();
    const end = new Date();
    const start = new Date(end);
    start.setUTCDate(start.getUTCDate() - rangeDays);
    const params = new URLSearchParams({
      instrument_key: instrumentKey, provider_id: providerId, start: start.toISOString(),
      end: end.toISOString(), interval,
    });
    setBusy(true);
    setError("");
    setDataWarning("");
    setBars([]);
    setSelectedTimestamp(null);
    const headers = { Authorization: `Bearer ${token}` };
    const intradayParams = new URLSearchParams({
      instrument_key: instrumentKey, provider_id: providerId, interval,
    });
    const quoteParams = new URLSearchParams({
      instrument_key: instrumentKey, provider_id: providerId,
    });
    Promise.allSettled([
      fetch(`${apiUrl}/market-data/historical?${params}`, {
        headers, cache: "no-store", signal: controller.signal,
      }).then(async (response) => {
        if (!response.ok) throw new Error("Historical candles unavailable");
        return await response.json() as Bar[];
      }),
      fetch(`${apiUrl}/market-data/intraday?${intradayParams}`, {
        headers, cache: "no-store", signal: controller.signal,
      }).then(async (response) => {
        if (!response.ok) throw new Error("Current-session candles unavailable");
        return await response.json() as Bar[];
      }),
      fetch(`${apiUrl}/market-data/quote?${quoteParams}`, {
        headers, cache: "no-store", signal: controller.signal,
      }).then(async (response) => {
        if (!response.ok) throw new Error("Latest quote unavailable");
        return await response.json() as LiveQuote;
      }),
    ])
      .then(([historicalResult, intradayResult, quoteResult]) => {
        if (!controller.signal.aborted) {
          if (historicalResult.status === "rejected" && intradayResult.status === "rejected") {
            setError("Historical and current-session candles are unavailable. Try Refresh.");
            return;
          }
          const history = historicalResult.status === "fulfilled" ? historicalResult.value : [];
          const intraday = intradayResult.status === "fulfilled" ? intradayResult.value : [];
          const merged = new Map<string, Bar>();
          for (const bar of [...history, ...intraday]) merged.set(bar.timestamp, bar);
          setBars([...merged.values()].filter((bar) => {
            const values = [bar.open, bar.high, bar.low, bar.close].map(Number);
            return Number.isFinite(Date.parse(bar.timestamp)) &&
              values.every((value) => Number.isFinite(value) && value >= 0) &&
              values[1] >= Math.max(values[0], values[3]) &&
              values[2] <= Math.min(values[0], values[3]) &&
              Number.isFinite(bar.volume) && bar.volume >= 0;
          }).sort(
            (left, right) => Date.parse(left.timestamp) - Date.parse(right.timestamp)
          ));
          setLiveQuote(quoteResult.status === "fulfilled" ? quoteResult.value : null);
          setDataWarning([
            historicalResult.status === "rejected" ? "Historical candles could not be updated." : "",
            intradayResult.status === "rejected" ? "Current-session candles could not be updated." : "",
            quoteResult.status === "rejected" ? "Latest quote could not be updated." : "",
          ].filter(Boolean).join(" "));
          setLastRefresh(new Date());
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) setError("Historical candles are unavailable for this instrument or period.");
      })
      .finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [apiUrl, instrumentKey, interval, providerId, rangeDays, refresh, token]);

  const chart = useMemo(() => {
    if (!bars.length) return null;
    const low = Math.min(...bars.map((bar) => Number(bar.low)));
    const high = Math.max(...bars.map((bar) => Number(bar.high)));
    const span = high - low || 1;
    const x = (index: number) => 52 + ((index + 0.5) / bars.length) * 714;
    const y = (value: number) => 200 - ((value - low) / span) * 168;
    const width = Math.max(1.5, Math.min(12, (714 / bars.length) * 0.65));
    const maxVolume = Math.max(1, ...bars.map((bar) => bar.volume));
    const points = bars.map((bar, index) => `${x(index).toFixed(1)},${y(Number(bar.close)).toFixed(1)}`).join(" ");
    return { low, high, x, y, width, maxVolume, points };
  }, [bars]);
  const selectedBar = bars.find((bar) => bar.timestamp === selectedTimestamp) || bars[bars.length - 1];
  const averages = useMemo(() => {
    const closes = bars.map(bar => Number(bar.close));
    const sma: Array<number | null> = Array(closes.length).fill(null);
    const ema: Array<number | null> = Array(closes.length).fill(null);
    if (closes.length < 20) return { sma, ema };
    let total = closes.slice(0, 20).reduce((sum, value) => sum + value, 0);
    sma[19] = total / 20;
    ema[19] = sma[19];
    for (let index = 20; index < closes.length; index++) {
      total += closes[index] - closes[index - 20];
      sma[index] = total / 20;
      ema[index] = closes[index] * (2 / 21) + ema[index - 1]! * (19 / 21);
    }
    return { sma, ema };
  }, [bars]);
  const indicatorPoints = (values: Array<number | null>) => values.flatMap((value, index) => value == null ? [] : [`${chart!.x(index).toFixed(1)},${chart!.y(value).toFixed(1)}`]).join(" ");
  const rsiValues = useMemo(() => rsi14(bars.map(bar => Number(bar.close))), [bars]);
  const rsiPoints = rsiValues.flatMap((value, index) => value == null ? [] : [
    `${chart!.x(index).toFixed(1)},${(90 - value * 0.8).toFixed(1)}`,
  ]).join(" ");
  const quoteTime = liveQuote ? Date.parse(liveQuote.timestamp) : NaN;
  const quoteBasis = liveQuote?.timestamp_basis === "exchange" ? "exchange timestamp" :
    liveQuote?.timestamp_basis === "provider_response" ? "provider response time, not an exchange quote update" : "timestamp unverified";
  const quoteAge = Number.isFinite(quoteTime) ? Math.max(0, Date.now() - quoteTime) : Infinity;

  return <section className="historical-price" aria-label={`${symbol} historical price view`}>
    <div className="historical-price-heading">
      <div><h3>{symbol} · Market chart</h3><p>{providerId} historical and current-session OHLCV</p></div>
      <div><button className="refresh-button" disabled={busy} onClick={() => setRefresh((value) => value + 1)} type="button">Refresh</button>{onClose && <button className="inline-button" onClick={onClose} type="button">Close</button>}</div>
    </div>
    <div className="historical-price-controls" aria-label="Chart controls">
      <label>Interval <select onChange={(event) => {
        const next = event.target.value as "15m" | "1h" | "1d";
        setInterval(next);
        setRangeDays(next === "15m" ? 5 : next === "1h" ? 15 : 45);
      }} value={interval}>
        <option value="15m">15 minutes</option><option value="1h">1 hour</option><option value="1d">1 day</option>
      </select></label>
      <label>Range <select onChange={(event) => setRangeDays(Number(event.target.value))} value={rangeDays}>
        {(interval === "15m" ? [5, 10, 20] : interval === "1h" ? [15, 30, 60] : [45, 120, 240]).map((days) => <option key={days} value={days}>{days} days</option>)}
      </select></label>
      <label>Style <select onChange={(event) => setChartStyle(event.target.value as "candles" | "line")} value={chartStyle}>
        <option value="candles">Candles</option><option value="line">Close line</option>
      </select></label>
      <label>Indicator <select onChange={(event) => setIndicator(event.target.value as "none" | "sma20" | "ema20" | "both" | "rsi14")} value={indicator}>
        <option value="none">None</option><option value="sma20">SMA 20</option><option value="ema20">EMA 20</option><option value="both">SMA 20 + EMA 20</option><option value="rsi14">RSI 14</option>
      </select></label>
      <label className="historical-live-toggle"><input checked={live} onChange={(event) => setLive(event.target.checked)} type="checkbox" /> Live refresh</label>
      <span>{live ? "Updates every 15 seconds" : "Automatic updates paused"}</span>
    </div>
    {busy ? <p className="muted" role="status">Loading historical candles…</p> : error ? <p className="error-banner" role="alert">{error}</p> : !bars.length ? <p className="muted">No valid historical candles were returned for this period.</p> : <>
      {dataWarning && <p className="muted" role="status">{dataWarning} Showing available candles.</p>}
      <p className="historical-price-summary">{bars.length} {interval} bars · Price range {price(chart!.low)}–{price(chart!.high)}{liveQuote ? ` · Latest quote ${price(liveQuote.last_price)} (${quoteBasis}${Number.isFinite(quoteTime) ? `, ${barTime(liveQuote.timestamp, "1h")} IST` : ""}${quoteAge > 60_000 ? ", older than 1 minute" : ""})` : " · Quote unavailable"}{lastRefresh ? ` · Retrieved ${lastRefresh.toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata" })} IST` : ""}</p>
      <svg aria-label={`${symbol} ${interval} ${chartStyle === "candles" ? "candlestick" : "close-line"} and volume chart from ${barTime(bars[0].timestamp, interval)} to ${barTime(bars[bars.length - 1].timestamp, interval)}`} className="historical-price-chart" role="img" viewBox="0 0 800 300">
        {[0, 0.5, 1].map((fraction) => <g key={fraction}>
          <line x1="52" x2="766" y1={200 - fraction * 168} y2={200 - fraction * 168} stroke="#e4eae4" />
          <text textAnchor="end" x="46" y={204 - fraction * 168}>{price(chart!.low + fraction * (chart!.high - chart!.low))}</text>
        </g>)}
        <line x1="52" x2="766" y1="208" y2="208" stroke="#b9c8ba" />
        {chartStyle === "line" ? <polyline fill="none" points={chart!.points} stroke="#277a56" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" /> : null}
        {(["sma20", "both"].includes(indicator) && bars.length >= 20) ? <polyline fill="none" points={indicatorPoints(averages.sma)} stroke="#b27717" strokeWidth="2" /> : null}
        {(["ema20", "both"].includes(indicator) && bars.length >= 20) ? <polyline fill="none" points={indicatorPoints(averages.ema)} stroke="#7252b5" strokeWidth="2" /> : null}
        {bars.map((bar, index) => {
          const x = chart!.x(index);
          const open = Number(bar.open);
          const close = Number(bar.close);
          const color = close >= open ? "#277a56" : "#b64a46";
          const bodyTop = Math.min(chart!.y(open), chart!.y(close));
          const bodyHeight = Math.max(1, Math.abs(chart!.y(open) - chart!.y(close)));
          const volumeHeight = (bar.volume / chart!.maxVolume) * 45;
          return <g aria-label={`Select ${barTime(bar.timestamp, interval)} candle`} key={bar.timestamp} onClick={() => setSelectedTimestamp(bar.timestamp)} onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === " ") { event.preventDefault(); setSelectedTimestamp(bar.timestamp); }
          }} role="button" style={{ cursor: "pointer" }} tabIndex={0}>
            <title>{`${barTime(bar.timestamp, interval)} · O ${price(open)} H ${price(bar.high)} L ${price(bar.low)} C ${price(close)} · Volume ${bar.volume.toLocaleString("en-IN")}`}</title>
            {chartStyle === "candles" ? <>
              <line x1={x} x2={x} y1={chart!.y(Number(bar.high))} y2={chart!.y(Number(bar.low))} stroke={color} strokeWidth="1.2" />
              <rect fill={color} height={bodyHeight} width={chart!.width} x={x - chart!.width / 2} y={bodyTop} />
            </> : null}
            <rect fill={color} height={volumeHeight} opacity="0.55" width={chart!.width} x={x - chart!.width / 2} y={263 - volumeHeight} />
            <rect fill="transparent" height="232" width={Math.max(chart!.width, 714 / bars.length)} x={x - Math.max(chart!.width, 714 / bars.length) / 2} y="31" />
          </g>;
        })}
        {selectedBar ? <line x1={chart!.x(bars.indexOf(selectedBar))} x2={chart!.x(bars.indexOf(selectedBar))} y1="30" y2="263" stroke="#4c614f" strokeDasharray="3 3" opacity="0.7" pointerEvents="none" /> : null}
        <text x="52" y="286">{day(bars[0].timestamp)}</text><text textAnchor="end" x="766" y="286">{day(bars[bars.length - 1].timestamp)}</text>
      </svg>
      {indicator === "rsi14" && bars.length >= 15 ? <div className="historical-rsi-panel">
        <strong>RSI 14 · observed close momentum</strong>
        <svg aria-label={`${symbol} 14-bar RSI, 0 to 100, with 30 and 70 reference lines`} className="historical-price-chart" role="img" viewBox="0 0 800 105">
          {[30, 50, 70].map(level => <g key={level}><line x1="52" x2="766" y1={90 - level * 0.8} y2={90 - level * 0.8} stroke="#dce5dd" strokeDasharray={level === 50 ? "none" : "4 4"} /><text textAnchor="end" x="46" y={94 - level * 0.8}>{level}</text></g>)}
          <polyline fill="none" points={rsiPoints} stroke="#7252b5" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" />
        </svg>
        <small>30 and 70 are reference levels, not automatic buy or sell signals.</small>
      </div> : null}
      {indicator !== "none" && bars.length < (indicator === "rsi14" ? 15 : 20) ? <p className="muted">The selected indicator needs at least {indicator === "rsi14" ? 15 : 20} candles in this view.</p> : null}
      {selectedBar ? <div className="historical-price-readout" aria-live="polite"><strong>{barTime(selectedBar.timestamp, interval)} IST</strong><span>O {price(selectedBar.open)}</span><span>H {price(selectedBar.high)}</span><span>L {price(selectedBar.low)}</span><span>C {price(selectedBar.close)}</span><span>Vol {selectedBar.volume.toLocaleString("en-IN")}</span>{["sma20", "both"].includes(indicator) && averages.sma[bars.indexOf(selectedBar)] != null ? <span>SMA 20 {price(averages.sma[bars.indexOf(selectedBar)]!)}</span> : null}{["ema20", "both"].includes(indicator) && averages.ema[bars.indexOf(selectedBar)] != null ? <span>EMA 20 {price(averages.ema[bars.indexOf(selectedBar)]!)}</span> : null}{indicator === "rsi14" && rsiValues[bars.indexOf(selectedBar)] != null ? <span>RSI 14 {rsiValues[bars.indexOf(selectedBar)]!.toFixed(1)}</span> : null}</div> : null}
      <div className="catalog-table-scroll"><table className="catalog-table historical-price-table">
        <thead><tr><th>Date</th><th>Open</th><th>High</th><th>Low</th><th>Close</th><th>Volume</th></tr></thead>
        <tbody>{bars.slice(-8).reverse().map((bar) => <tr key={bar.timestamp}>
          <td>{barTime(bar.timestamp, interval)}</td><td>{price(bar.open)}</td><td>{price(bar.high)}</td><td>{price(bar.low)}</td><td>{price(bar.close)}</td><td>{bar.volume.toLocaleString("en-IN")}</td>
        </tr>)}</tbody>
      </table></div>
      <p className="muted">Current-session candles and quotes refresh while Live refresh is enabled. They are provider observations, not executable fills; the latest incomplete candle can change.</p>
    </>}
  </section>;
}
