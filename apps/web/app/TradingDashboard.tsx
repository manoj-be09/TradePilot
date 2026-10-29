"use client";

import { useEffect, useState } from "react";
import { ObservedStrategyPerformance } from "./ObservedStrategyPerformance";
import type { PerformanceTrade } from "./observedPerformance";

type OptionTrade = PerformanceTrade & {
  id: string;
  strategy_name: string;
  status: "OPEN" | "CLOSED";
  realized_pnl: string | null;
  unrealized_pnl: string | null;
  valuation_warning: string | null;
  opened_at: string;
  closed_at: string | null;
};
type OptionDeployment = { id: string; strategy_key: string; status: string; trade_id: string | null; created_at: string; detail?: string; entry_conditions?: {metric: string; operator: string; threshold: string}[] };
type DashboardRow = { currentAt?: string | null; id: string; name: string; strategy: string; status: string; mark: string | null; markAt: string | null; note: string | null; at: string };
type WatchItem = { id: string; provider: string; instrument_key: string; trading_symbol: string; exchange: string; instrument_type: string };
type WatchQuote = { instrument_token: string; last_price: string; timestamp: string; timestamp_basis: string; last_trade_time: string | null };

const money = (value: number | string | null) => value == null || !Number.isFinite(Number(value))
  ? "Unavailable"
  : new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 2 }).format(Number(value));
const time = (value: string) => new Date(value).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });
const strategyId = (value: string) => value.trim().replaceAll("_", " ").replace(/\s+/g, " ").toLocaleLowerCase("en-IN");
const istDay = (value: string) => {
  const parts = new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Kolkata", year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(new Date(value));
  const part = (type: string) => parts.find(item => item.type === type)?.value ?? "";
  return `${part("year")}-${part("month")}-${part("day")}`;
};
const activityDays = () => {
  const latest = new Date(`${istDay(new Date().toISOString())}T12:00:00Z`);
  return Array.from({ length: 14 }, (_, index) => new Date(latest.getTime() - (13 - index) * 86_400_000).toISOString().slice(0, 10));
};

function DashboardWatchlist({ apiUrl, token, onExploreSymbols }: { apiUrl: string; token: string; onExploreSymbols: () => void }) {
  const [items, setItems] = useState<WatchItem[] | null>(null);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState("");
  const [quote, setQuote] = useState<WatchQuote | null>(null);
  const [quoteError, setQuoteError] = useState("");
  const [quoteBusy, setQuoteBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    const changed = () => setRefresh(value => value + 1);
    window.addEventListener("market-watchlist-changed", changed);
    fetch(`${apiUrl}/watchlist`, { headers: { Authorization: `Bearer ${token}` }, cache: "no-store", signal: controller.signal })
      .then(async response => {
        if (!response.ok) throw new Error("Watchlist unavailable");
        return response.json() as Promise<WatchItem[]>;
      })
      .then(rows => { if (!controller.signal.aborted) { setItems(rows); setError(""); } })
      .catch(() => { if (!controller.signal.aborted) setError("Could not load your saved watchlist."); });
    return () => { controller.abort(); window.removeEventListener("market-watchlist-changed", changed); };
  }, [apiUrl, token, refresh]);

  async function inspect(item: WatchItem) {
    if (quoteBusy) return;
    setSelected(item.id); setQuote(null); setQuoteError(""); setQuoteBusy(true);
    try {
      const params = new URLSearchParams({ instrument_key: item.instrument_key, provider_id: item.provider });
      const response = await fetch(`${apiUrl}/market-data/quote?${params}`, { headers: { Authorization: `Bearer ${token}` }, cache: "no-store" });
      if (!response.ok) throw new Error("Quote unavailable from the saved source");
      const result = await response.json() as WatchQuote;
      if (result.instrument_token !== item.instrument_key) throw new Error("Quote instrument mismatch");
      setQuote(result);
    } catch (cause) {
      setQuoteError(cause instanceof Error ? cause.message : "Quote unavailable");
    } finally { setQuoteBusy(false); }
  }

  return <aside className="trading-dashboard-watchlist" aria-label="Saved marketwatch">
    <div className="trading-dashboard-watchlist-heading"><div><span className="panel-kicker">MARKETWATCH</span><h3>Saved symbols</h3></div><div className="trading-dashboard-watchlist-actions"><button className="inline-button" onClick={onExploreSymbols} type="button">Add symbol</button><button aria-label="Refresh saved symbols" className="inline-button" onClick={() => setRefresh(value => value + 1)} type="button">Refresh</button></div></div>
    {error && <p className="error-banner" role="alert">{error}</p>}
    {!items ? <p className="muted">Loading saved symbols…</p>
      : items.length === 0 ? <p className="trading-dashboard-watchlist-empty">No saved symbols yet. Use Add symbol to find an instrument.</p>
        : <ul>{items.slice(0, 20).map(item => <li key={item.id}>
          <button aria-expanded={selected === item.id} onClick={() => void inspect(item)} type="button"><strong>{item.trading_symbol}</strong><small>{item.exchange} · {item.instrument_type}</small><span>{selected === item.id && quoteBusy ? "Loading…" : "View quote"}</span></button>
          {selected === item.id && (quote || quoteError) ? <div className="trading-dashboard-watch-quote">
            {quote ? <><strong>{money(quote.last_price)}</strong><small>{quote.timestamp_basis === "exchange" ? "Exchange time" : quote.timestamp_basis === "provider_response" ? "Provider response time" : quote.timestamp_basis === "provider_tick" ? "Provider tick time" : "Timestamp source unverified"} · {time(quote.timestamp)}</small><small>{quote.last_trade_time ? `Last trade ${time(quote.last_trade_time)}` : "Last trade time unavailable"}</small></> : <small role="alert">{quoteError}</small>}
          </div> : null}
        </li>)}</ul>}
    {items && items.length > 20 && <p className="trading-dashboard-watchlist-empty">Showing 20 of {items.length} saved symbols. Open the instrument explorer for the full list.</p>}
    <p className="trading-dashboard-watchlist-foot">Quotes are snapshots on request. A provider response time does not verify bid/ask book freshness.</p>
  </aside>;
}

export function TradingDashboard({ token, apiUrl, workspace, onOpenStrategies, onExploreSymbols }: {
  token: string;
  apiUrl: string;
  workspace: "equities" | "derivatives";
  onOpenStrategies: () => void;
  onExploreSymbols: () => void;
}) {
  const [options, setOptions] = useState<OptionTrade[] | null>(null);
  const [optionDeployments, setOptionDeployments] = useState<OptionDeployment[] | null>(null);
  const [error, setError] = useState("");
  const [selectedStrategy, setSelectedStrategy] = useState("");
  const [activityMeasure, setActivityMeasure] = useState<"trades" | "pnl">("trades");

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    async function get<T>(path: string): Promise<T> {
      const response = await fetch(`${apiUrl}${path}`, { headers: { Authorization: `Bearer ${token}` }, cache: "no-store", signal: controller.signal });
      if (!response.ok) throw new Error(`Dashboard data unavailable (${response.status})`);
      return response.json() as Promise<T>;
    }
    async function refresh() {
      try {
        const [portfolio, deployments] = await Promise.all([
          get<{ trades: OptionTrade[] }>("/paper/trades"),
          get<OptionDeployment[]>("/paper/deployments"),
        ]);
        if (!stopped) { setOptions(portfolio.trades); setOptionDeployments(deployments); }
        if (!stopped) setError("");
      } catch (cause) {
        if (!stopped) setError(cause instanceof Error ? cause.message : "Dashboard data unavailable");
      } finally {
        if (!stopped) timer = setTimeout(refresh, 10_000);
      }
    }
    if (workspace === "equities") return;
    void refresh();
    return () => { stopped = true; controller.abort(); clearTimeout(timer); };
  }, [apiUrl, token, workspace]);

  const loaded = options !== null && optionDeployments !== null;
  const strategyLabels = new Map<string, string>();
  for (const row of optionDeployments ?? []) strategyLabels.set(strategyId(row.strategy_key), row.strategy_key.replaceAll("_", " "));
  for (const row of options ?? []) strategyLabels.set(strategyId(row.strategy_name), row.strategy_name);
  const strategyChoices = [...strategyLabels.entries()].sort((left, right) => left[1].localeCompare(right[1]));
  const chosen = strategyLabels.has(selectedStrategy) ? selectedStrategy : "";
  const matches = (value: string) => !chosen || strategyId(value) === chosen;
  const closed = (options ?? []).filter(row => row.status === "CLOSED" && row.realized_pnl != null && matches(row.strategy_name)).map(row => Number(row.realized_pnl));
  const validClosed = closed.filter(Number.isFinite);
  const profit = validClosed.filter(value => value > 0).reduce((total, value) => total + value, 0);
  const loss = validClosed.filter(value => value < 0).reduce((total, value) => total + value, 0);
  const closedCount = validClosed.length;
  const wins = validClosed.filter(value => value > 0).length;
  const observedWinRate = closedCount ? `${((wins / closedCount) * 100).toFixed(1)}%` : "Unavailable";
  const averageTrade = closedCount ? money((profit + loss) / closedCount) : "Unavailable";
  const active: DashboardRow[] = [
      ...(optionDeployments ?? []).filter(row => row.status === "WAITING_FOR_ENTRY" && matches(row.strategy_key)).map(row => ({
        id: row.id, name: row.strategy_key.replaceAll("_", " "), strategy: row.strategy_key, status: row.status, mark: null, markAt: null, note: row.detail ?? "Waiting for entry signal", at: row.created_at,
      })),
      ...(options ?? []).filter(row => row.status === "OPEN" && matches(row.strategy_name)).map(row => ({
        id: row.id, name: row.strategy_name, strategy: row.strategy_name, status: "OPEN", mark: row.valuation_warning ? null : row.unrealized_pnl,
        markAt: null, note: row.valuation_warning, at: row.opened_at,
      })),
    ];
  const activeCount = active.length;
  const days = activityDays();
  const activity = days.map(date => {
    const rows = (options ?? []).filter(row => row.status === "CLOSED" && row.closed_at && istDay(row.closed_at) === date && row.realized_pnl != null && matches(row.strategy_name)).map(row => ({ count: 1, pnl: Number(row.realized_pnl) }));
    return { date, count: rows.reduce((sum, row) => sum + row.count, 0), pnl: rows.reduce((sum, row) => sum + (Number.isFinite(row.pnl) ? row.pnl : 0), 0) };
  });
  const activityTotal = activity.reduce((sum, row) => sum + row.count, 0);
  const activityHigh = Math.max(1, ...activity.map(row => activityMeasure === "trades" ? row.count : Math.abs(row.pnl)));

  if (workspace === "equities") return <section className="trading-dashboard" aria-label="Equity and ETF research">
    <div className="trading-dashboard-heading"><div><span className="panel-kicker">EQUITY RESEARCH</span><h2>Equity &amp; ETF</h2><p>Equity paper trading has been retired. TradePilot paper-trades NIFTY futures and options only.</p></div><span className="paper-mode-badge">RESEARCH ONLY</span></div>
    <div className="trading-dashboard-content"><div className="trading-dashboard-main">
      <p className="trading-dashboard-empty">Equity strategy ranking and chronological checks are still available under Strategies. Earlier equity paper records are kept in the database.</p>
      <button className="refresh-button" onClick={onOpenStrategies} type="button">Open equity research</button>
    </div><DashboardWatchlist apiUrl={apiUrl} token={token} onExploreSymbols={onExploreSymbols} /></div>
  </section>;

  return <section className="trading-dashboard" aria-label="Futures and options paper dashboard">
    <div className="trading-dashboard-heading"><div><span className="panel-kicker">PAPER TRADING OVERVIEW</span><h2>Future &amp; options dashboard</h2><p>Your paper account at a glance.</p></div><span className="paper-mode-badge">LIVE ORDERS DISABLED</span></div>
    <label className="trading-dashboard-filter">Show results for
      <select aria-label="Filter dashboard by strategy" onChange={event => setSelectedStrategy(event.target.value)} value={chosen}>
        <option value="">All strategies · aggregate</option>
        {strategyChoices.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
      </select>
    </label>
    {error && <p className="error-banner" role="alert">{error} · Showing the last loaded values, if available.</p>}
    <div className="trading-dashboard-content"><div className="trading-dashboard-main">
    <div className="trading-dashboard-metrics">
      <article className="trading-dashboard-metric net"><span>Realized net P&amp;L</span><strong>{loaded ? money(profit + loss) : error ? "Unavailable" : "Loading…"}</strong><small>Closed paper trades · all time</small></article>
      <article className="trading-dashboard-metric gain"><span>Gross profit</span><strong>{loaded ? money(profit) : error ? "Unavailable" : "Loading…"}</strong><small>Sum of profitable closed trades</small></article>
      <article className="trading-dashboard-metric loss"><span>Gross loss</span><strong>{loaded ? money(Math.abs(loss)) : error ? "Unavailable" : "Loading…"}</strong><small>Sum of losing closed trades</small></article>
      <article className="trading-dashboard-metric"><span>Active strategies</span><strong>{loaded ? activeCount : error ? "Unavailable" : "Loading…"}</strong><small>Waiting or open paper strategies</small></article>
    </div>
    <div className="trading-dashboard-activity">
      <div className="trading-dashboard-activity-heading"><div><h3>Active strategies</h3><span className="muted">{activeCount} monitoring</span></div><button className="refresh-button" onClick={onOpenStrategies} type="button">Manage strategies</button></div>
      <details className="workspace-disclosure"><summary>Performance details</summary><p>{loaded ? `${closedCount} closed trades · Observed win rate ${observedWinRate} · Average realized P&L/trade ${averageTrade}. ` : ""}Entry can wait for a qualifying signal. Open P&amp;L is a quote-based mark, not realized profit.</p></details>
      {!loaded ? <p className="muted">{error ? "Active strategies unavailable." : "Loading paper strategies…"}</p> : active.length === 0 ? <p className="trading-dashboard-empty">No active paper strategies. Add one from Strategies to start monitoring.</p> : <div className="trading-dashboard-list">{active.map(row => <article key={row.id}>
        <div className="dashboard-strategy-heading"><span className={`dashboard-status dashboard-status--${row.status.toLowerCase()}`}>{row.status === "WAITING_FOR_ENTRY" ? "Waiting for entry" : row.status === "EXIT_REQUESTED" ? "Exit requested" : "Position open"}</span><strong>{row.name}</strong>{row.note && <small>{row.note}</small>}</div>
        {row.status === "WAITING_FOR_ENTRY" ? <details className="entry-readiness"><summary>Entry criteria &amp; current state</summary><p>Every saved condition must pass on a later open-market snapshot:</p><ul>{optionDeployments?.find(item => item.id === row.id)?.entry_conditions?.map((condition, index) => <li key={index}>{condition.metric.replaceAll("_", " ")} {({GT: ">", GTE: "≥", LT: "<", LTE: "≤"} as Record<string, string>)[condition.operator] ?? condition.operator} {condition.threshold}</li>)}</ul><p>Selected legs, quote quality, expiry window, available cash and risk limits must also pass. The current worker reason is shown above; individual gate results are not confirmed here.</p></details> : null}
        <div className="trading-dashboard-mark">{row.status !== "WAITING_FOR_ENTRY" && <><span>Open P&amp;L</span><strong>{money(row.mark)}</strong>{row.markAt && <small>Mark quote {time(row.markAt)}</small>}</>}{row.currentAt && <small>Price snapshot {time(row.currentAt)}</small>}<small>{row.status === "OPEN" ? "Opened" : "Updated"} {time(row.at)}</small></div>
      </article>)}</div>}
    </div>
    </div><section className="trading-dashboard-chart" aria-label="Recent paper trade activity">
      <div className="trading-dashboard-chart-heading"><div><h3>Paper trade activity</h3><p>Past 14 calendar days · India time · {chosen ? strategyLabels.get(chosen) : "All strategies"}</p></div>
        <div className="trading-dashboard-chart-switch" aria-label="Chart measure">
          <button aria-pressed={activityMeasure === "trades"} onClick={() => setActivityMeasure("trades")} type="button">Trades closed</button>
          <button aria-pressed={activityMeasure === "pnl"} onClick={() => setActivityMeasure("pnl")} type="button">Daily net P&amp;L</button>
        </div>
      </div>
      {!loaded ? <p className="trading-dashboard-empty">Loading saved paper trades…</p>
        : activityTotal === 0 ? <p className="trading-dashboard-empty">No closed paper trades in this period. Applied strategies can remain waiting for an entry signal.</p>
          : <div className="trading-dashboard-chart-scroll"><svg aria-label={`${activityMeasure === "trades" ? "Closed trades" : "Net profit and loss"} for each of the past 14 days`} role="img" viewBox="0 0 700 204">
            <line className="chart-baseline" x1="42" x2="684" y1={activityMeasure === "trades" ? "165" : "90"} y2={activityMeasure === "trades" ? "165" : "90"} />
            {activity.map((row, index) => {
              const center = 59 + index * 45;
              const value = activityMeasure === "trades" ? row.count : row.pnl;
              const height = Math.abs(value) / activityHigh * (activityMeasure === "trades" ? 125 : 65);
              const baseline = activityMeasure === "trades" ? 165 : 90;
              const y = value < 0 ? baseline : baseline - height;
              const label = new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short", timeZone: "UTC" }).format(new Date(`${row.date}T12:00:00Z`));
              return <g key={row.date}><rect className={value < 0 ? "chart-bar chart-bar--loss" : "chart-bar"} height={Math.max(value === 0 ? 0 : 2, height)} width="23" x={center - 11} y={y}><title>{`${label}: ${row.count} closed trades; net ${money(row.pnl)}`}</title></rect>
                {index % 2 === 0 || index === 13 ? <text textAnchor="middle" x={center} y="191">{label}</text> : null}</g>;
            })}
          </svg></div>}
      <p className="trading-dashboard-chart-note">{activityTotal} closed trades in this period. The cards above cover all recorded history; this chart shows only recent realized results.</p>
    </section>
    <DashboardWatchlist apiUrl={apiUrl} token={token} onExploreSymbols={onExploreSymbols} /></div>
    {options !== null ? <ObservedStrategyPerformance trades={options} stale={Boolean(error)} apiUrl={apiUrl} token={token} /> : null}
    <details className="trading-dashboard-method"><summary>About these results</summary><p className="trading-dashboard-note">Realized figures exclude open positions and unfilled deployments. Strategy totals combine saved versions with the same name.Open marks may be unavailable when verified quotes cannot be obtained.</p></details>
  </section>;
}
