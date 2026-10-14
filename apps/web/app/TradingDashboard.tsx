"use client";

import { useEffect, useState } from "react";
import { EquityEntryReadiness, type EquityEntryState } from "./EquityEntryReadiness";

type EquityDeployment = {
  id: string;
  symbol: string;
  strategy: string;
  status: string;
  updated_at: string;
  state: EquityEntryState & { position?: {entry_price: string}; entry_threshold_price?: string | null; entry_reference_price?: string | null; current_price?: string | null; current_price_at?: string | null; realized_pnl?: string | null; marked_pnl?: string | null; quote_at?: string | null };
};
type OptionTrade = {
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
type EquitySummary = { strategy: string; gross_profit: string; gross_loss: string; realized_net: string; closed_trades: number; winning_trades: number; closed_unpriced: number; active_strategies: number };
type DailyActivity = { date: string; strategy: string; closed_trades: number; realized_net: string };
type EquityDashboardData = { scope: "all_time"; totals: EquitySummary; strategies: EquitySummary[]; daily: DailyActivity[] };
type EquityLedgerReconciliation = { status: "MATCHED" | "MISMATCH"; recorded_cash: string; expected_cash: string; cash_difference: string; deployment_count: number; reserved_count: number; closed_count: number; issue_count: number; issues: string[]; audit_coverage: "COMPLETE" | "INCOMPLETE"; audit_issue_count: number; audit_issues: string[]; cash_journal: { status: "MATCHED" | "LEGACY_CHECKPOINT" | "NOT_STARTED" | "MISMATCH"; entry_count: number; checkpoint_cash: string | null; journal_cash: string | null; issue_count: number; issues: string[] } };
type DashboardRow = { entry?: string | null; currentPrice?: string | null; currentAt?: string | null; id: string; name: string; strategy: string; status: string; mark: string | null; markAt: string | null; note: string | null; at: string };
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
  const [equity, setEquity] = useState<EquityDeployment[] | null>(null);
  const [entryCatalog, setEntryCatalog] = useState<{implementation_key: string; entry: string}[]>([]);
  useEffect(() => {
    if (workspace !== "equities") return;
    const controller = new AbortController();
    fetch(`${apiUrl}/equity/strategy-catalog`, {headers: {Authorization: `Bearer ${token}`}, signal: controller.signal})
      .then(async response => response.ok ? response.json() : [])
      .then(rows => { if (!controller.signal.aborted) setEntryCatalog(rows); })
      .catch(() => { if (!controller.signal.aborted) setEntryCatalog([]); });
    return () => controller.abort();
  }, [apiUrl, token, workspace]);
  const [equitySummary, setEquitySummary] = useState<EquityDashboardData | null>(null);
  const [options, setOptions] = useState<OptionTrade[] | null>(null);
  const [optionDeployments, setOptionDeployments] = useState<OptionDeployment[] | null>(null);
  const [error, setError] = useState("");
  const [selectedStrategy, setSelectedStrategy] = useState("");
  const [activityMeasure, setActivityMeasure] = useState<"trades" | "pnl">("trades");
  const [equityLedger, setEquityLedger] = useState<EquityLedgerReconciliation | null>(null);
  const [equityLedgerBusy, setEquityLedgerBusy] = useState(false);
  const [equityLedgerError, setEquityLedgerError] = useState("");
  const [equityLedgerCheckedAt, setEquityLedgerCheckedAt] = useState("");

  async function checkEquityLedger() {
    if (equityLedgerBusy) return;
    setEquityLedgerBusy(true); setEquityLedgerError(""); setEquityLedger(null);
    try {
      const response = await fetch(`${apiUrl}/equity/account/ledger-reconciliation`, {
        headers: { Authorization: `Bearer ${token}` }, cache: "no-store",
      });
      if (!response.ok) throw new Error(`Equity ledger unavailable (${response.status})`);
      setEquityLedger(await response.json() as EquityLedgerReconciliation);
      setEquityLedgerCheckedAt(new Date().toISOString());
    } catch (cause) {
      setEquityLedgerError(cause instanceof Error ? cause.message : "Equity ledger unavailable");
    } finally { setEquityLedgerBusy(false); }
  }

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
        if (workspace === "equities") {
          const [result, summary] = await Promise.all([
            get<{ deployments: EquityDeployment[] }>("/equity/deployments"),
            get<EquityDashboardData>("/equity/dashboard"),
          ]);
          if (!stopped) { setEquity(result.deployments); setEquitySummary(summary); }
        } else {
          const [portfolio, deployments] = await Promise.all([
            get<{ trades: OptionTrade[] }>("/paper/trades"),
            get<OptionDeployment[]>("/paper/deployments"),
          ]);
          if (!stopped) { setOptions(portfolio.trades); setOptionDeployments(deployments); }
        }
        if (!stopped) setError("");
      } catch (cause) {
        if (!stopped) setError(cause instanceof Error ? cause.message : "Dashboard data unavailable");
      } finally {
        if (!stopped) timer = setTimeout(refresh, 10_000);
      }
    }
    void refresh();
    return () => { stopped = true; controller.abort(); clearTimeout(timer); };
  }, [apiUrl, token, workspace]);

  const loaded = workspace === "equities" ? equity !== null && equitySummary !== null : options !== null && optionDeployments !== null;
  const strategyLabels = new Map<string, string>();
  if (workspace === "equities") {
    for (const row of equitySummary?.strategies ?? []) strategyLabels.set(strategyId(row.strategy), row.strategy);
  } else {
    for (const row of optionDeployments ?? []) strategyLabels.set(strategyId(row.strategy_key), row.strategy_key.replaceAll("_", " "));
    for (const row of options ?? []) strategyLabels.set(strategyId(row.strategy_name), row.strategy_name);
  }
  const strategyChoices = [...strategyLabels.entries()].sort((left, right) => left[1].localeCompare(right[1]));
  const chosen = strategyLabels.has(selectedStrategy) ? selectedStrategy : "";
  const matches = (value: string) => !chosen || strategyId(value) === chosen;
  const closed = workspace === "equities"
    ? (equity ?? []).filter(row => row.status === "CLOSED" && row.state.realized_pnl != null && matches(row.strategy)).map(row => Number(row.state.realized_pnl))
    : (options ?? []).filter(row => row.status === "CLOSED" && row.realized_pnl != null && matches(row.strategy_name)).map(row => Number(row.realized_pnl));
  const validClosed = closed.filter(Number.isFinite);
  const equitySelected = chosen
    ? equitySummary?.strategies.find(row => strategyId(row.strategy) === chosen)
    : equitySummary?.totals;
  const profit = workspace === "equities" ? Number(equitySelected?.gross_profit ?? 0) : validClosed.filter(value => value > 0).reduce((total, value) => total + value, 0);
  const loss = workspace === "equities" ? Number(equitySelected?.gross_loss ?? 0) : validClosed.filter(value => value < 0).reduce((total, value) => total + value, 0);
  const closedCount = workspace === "equities" ? equitySelected?.closed_trades ?? 0 : validClosed.length;
  const wins = workspace === "equities" ? equitySelected?.winning_trades ?? 0 : validClosed.filter(value => value > 0).length;
  const observedWinRate = closedCount ? `${((wins / closedCount) * 100).toFixed(1)}%` : "Unavailable";
  const averageTrade = closedCount ? money((profit + loss) / closedCount) : "Unavailable";
  const active: DashboardRow[] = workspace === "equities"
    ? (equity ?? []).filter(row => ["WAITING_FOR_ENTRY", "OPEN", "EXIT_REQUESTED"].includes(row.status) && matches(row.strategy)).map(row => ({
      entry: row.status === "WAITING_FOR_ENTRY" ? row.state.entry_threshold_price ?? row.state.entry_reference_price : row.state.position?.entry_price, currentPrice: row.state.current_price, currentAt: row.state.current_price_at,
      id: row.id, name: `${row.symbol} · ${row.strategy}`, strategy: row.strategy, status: row.status, mark: ["OPEN", "EXIT_REQUESTED"].includes(row.status) ? row.state.marked_pnl ?? null : null,
      markAt: row.state.quote_at ?? null, note: row.state.detail ? `${row.status === "WAITING_FOR_ENTRY" ? "" : "Exit monitoring: "}${row.state.detail}` : null, at: row.updated_at,
    }))
    : [
      ...(optionDeployments ?? []).filter(row => row.status === "WAITING_FOR_ENTRY" && matches(row.strategy_key)).map(row => ({
        id: row.id, name: row.strategy_key.replaceAll("_", " "), strategy: row.strategy_key, status: row.status, mark: null, markAt: null, note: row.detail ?? "Waiting for entry signal", at: row.created_at,
      })),
      ...(options ?? []).filter(row => row.status === "OPEN" && matches(row.strategy_name)).map(row => ({
        id: row.id, name: row.strategy_name, strategy: row.strategy_name, status: "OPEN", mark: row.valuation_warning ? null : row.unrealized_pnl,
        markAt: null, note: row.valuation_warning, at: row.opened_at,
      })),
    ];
  const activeCount = workspace === "equities" ? equitySelected?.active_strategies ?? 0 : active.length;
  const days = activityDays();
  const activity = days.map(date => {
    const rows = workspace === "equities"
      ? (equitySummary?.daily ?? []).filter(row => row.date === date && matches(row.strategy)).map(row => ({ count: row.closed_trades, pnl: Number(row.realized_net) }))
      : (options ?? []).filter(row => row.status === "CLOSED" && row.closed_at && istDay(row.closed_at) === date && row.realized_pnl != null && matches(row.strategy_name)).map(row => ({ count: 1, pnl: Number(row.realized_pnl) }));
    return { date, count: rows.reduce((sum, row) => sum + row.count, 0), pnl: rows.reduce((sum, row) => sum + (Number.isFinite(row.pnl) ? row.pnl : 0), 0) };
  });
  const activityTotal = activity.reduce((sum, row) => sum + row.count, 0);
  const activityHigh = Math.max(1, ...activity.map(row => activityMeasure === "trades" ? row.count : Math.abs(row.pnl)));

  return <section className="trading-dashboard" aria-label={`${workspace === "equities" ? "Equity and ETF" : "Futures and options"} paper dashboard`}>
    <div className="trading-dashboard-heading"><div><span className="panel-kicker">PAPER TRADING OVERVIEW</span><h2>{workspace === "equities" ? "Equity & ETF dashboard" : "Future & options dashboard"}</h2><p>Your paper account at a glance.</p></div><span className="paper-mode-badge">LIVE ORDERS DISABLED</span></div>
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
        <div><strong>{row.name}</strong><small>{row.status.replaceAll("_", " ")} · Updated {time(row.at)}</small>{row.note && <small>{row.note}</small>}</div>
        {workspace === "equities" ? <div className="dashboard-price-pair"><div><span>{row.status !== "WAITING_FOR_ENTRY" ? "Filled entry price" : row.strategy === "test_pulse" ? "Quote-average reference" : "Daily close reference"}</span><strong>{money(row.entry ?? null)}</strong></div><div><span>Current price</span><strong>{money(row.currentPrice ?? null)}</strong></div></div> : null}
        {workspace === "equities" && row.status === "WAITING_FOR_ENTRY" ? <EquityEntryReadiness strategy={row.strategy} state={equity?.find(item => item.id === row.id)?.state ?? {}} rule={entryCatalog.find(item => item.implementation_key === row.strategy)?.entry} /> : null}
        {workspace === "derivatives" && row.status === "WAITING_FOR_ENTRY" ? <details className="entry-readiness"><summary>Entry criteria &amp; current state</summary><p>Every saved condition must pass on a later open-market snapshot:</p><ul>{optionDeployments?.find(item => item.id === row.id)?.entry_conditions?.map((condition, index) => <li key={index}>{condition.metric.replaceAll("_", " ")} {({GT: ">", GTE: "≥", LT: "<", LTE: "≤"} as Record<string, string>)[condition.operator] ?? condition.operator} {condition.threshold}</li>)}</ul><p>Selected legs, quote quality, expiry window, available cash and risk limits must also pass. The current worker reason is shown above; individual gate results are not confirmed here.</p></details> : null}
        <div className="trading-dashboard-mark"><span>Open P&amp;L</span><strong>{money(row.mark)}</strong>{row.markAt && <small>Mark quote {time(row.markAt)}</small>}{row.currentAt && <small>Price snapshot {time(row.currentAt)}</small>}</div>
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
    {workspace === "equities" ? <details className="paper-ledger-review">
      <summary>Paper wallet check</summary>
      <p className="trading-dashboard-note">Compare all saved equity allocations and closed paper P&amp;L with the wallet balance. This checks internal arithmetic, not whether a market quote was executable.</p>
      <button className="inline-button" disabled={equityLedgerBusy} onClick={() => void checkEquityLedger()} type="button">{equityLedgerBusy ? "Checking…" : "Check wallet now"}</button>
      {equityLedgerError ? <p className="error-banner" role="alert">{equityLedgerError}</p> : null}
      {equityLedger ? <div className={`paper-ledger-result ${equityLedger.status === "MISMATCH" || equityLedger.audit_coverage === "INCOMPLETE" || equityLedger.cash_journal.status === "MISMATCH" ? "paper-ledger-result--mismatch" : ""}`} role="status">
        <strong>{equityLedger.status === "MISMATCH" ? `${equityLedger.issue_count} wallet issue${equityLedger.issue_count === 1 ? "" : "s"} found` : equityLedger.audit_coverage === "INCOMPLETE" ? "Wallet arithmetic matches; audit gaps found" : "Saved wallet matches"}</strong>
        <p>Recorded cash {money(equityLedger.recorded_cash)} · Expected cash {money(equityLedger.expected_cash)} · Difference {money(equityLedger.cash_difference)}</p>
        <p>{equityLedger.deployment_count} deployments · {equityLedger.reserved_count} reserving cash · {equityLedger.closed_count} closed{equityLedgerCheckedAt ? ` · Checked ${time(equityLedgerCheckedAt)}` : ""}</p>
        <p>Lifecycle audit: {equityLedger.audit_coverage === "COMPLETE" ? "Complete for saved deployments" : `${equityLedger.audit_issue_count} missing or inconsistent event${equityLedger.audit_issue_count === 1 ? "" : "s"}`}. Older imported records may have incomplete history.</p>
        <p>Cash journal: {equityLedger.cash_journal.status === "MATCHED" ? "Matched from initial funding" : equityLedger.cash_journal.status === "LEGACY_CHECKPOINT" ? "Matched since legacy balance checkpoint; older movements unverified" : equityLedger.cash_journal.status === "NOT_STARTED" ? "Not started for this saved wallet" : `${equityLedger.cash_journal.issue_count} ${equityLedger.cash_journal.issue_count === 1 ? "discrepancy" : "discrepancies"}`}. {equityLedger.cash_journal.entry_count} recorded cash events.</p>
        {equityLedger.issues.length > 0 ? <ul>{equityLedger.issues.map((issue, index) => <li key={`${index}-${issue}`}>{issue}</li>)}</ul> : null}
        {equityLedger.audit_issues.length > 0 ? <details><summary>Audit gaps</summary><ul>{equityLedger.audit_issues.map((issue, index) => <li key={`${index}-${issue}`}>{issue}</li>)}</ul></details> : null}
        {equityLedger.cash_journal.issues.length > 0 ? <details><summary>Cash journal issues</summary><ul>{equityLedger.cash_journal.issues.map((issue, index) => <li key={`${index}-${issue}`}>{issue}</li>)}</ul></details> : null}
      </div> : null}
    </details> : null}
    <details className="trading-dashboard-method"><summary>About these results</summary><p className="trading-dashboard-note">Realized figures exclude open positions and unfilled deployments. Strategy totals combine saved versions with the same name. {workspace === "equities" ? `The activity list shows the latest 200 deployments. ${equitySelected?.closed_unpriced ?? 0} closed records without a valid P&L are excluded. Opt-in indicative fills may appear in P&L but cannot improve verified strategy feedback. ` : ""}Open marks may be unavailable when verified quotes cannot be obtained.</p></details>
  </section>;
}
