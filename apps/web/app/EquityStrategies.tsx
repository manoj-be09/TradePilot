"use client";

import { useEffect, useRef, useState } from "react";
import { StrategyPerformance, type StrategyPerformanceData } from "./StrategyPerformance";
import { ValidationAttemptHistory } from "./ValidationAttemptHistory";

type Instrument = { id: string; instrument_key: string; trading_symbol: string; exchange: string; instrument_type: string; provider: string };
type FoundInstrument = { instrument_key: string; trading_symbol: string; name: string | null; exchange: string; segment: string; instrument_type: string };
type Position = { quantity: number; entry_price: string; stop: string; target: string; distance: string; trailing_active: boolean; cost: string; stop_scenario_net: string; target_scenario_net: string; stop_exit_fill: string; target_exit_fill: string; entry_fee: string; stop_exit_fee: string; target_exit_fee: string };
type SharedScenario = { position: Position | null; basis: "live_ask" | "last_completed_close"; reference_at: string; reference_price: string; signal_close: string; atr_14_sma: string; allocation: string; planned_risk: string; fee_pct_per_side: string; slippage_pct_per_side: string; exit_policy_version: string; exit_rule: string; stop_rule: string };
type Evidence = { closed_trades: number; replay_closed_trades: number; paper_closed_trades: number; source: "historical_replay"; win_rate: number | null; average_net_pnl: number | null; best_trade: number | null; worst_trade: number | null; closed_trade_drawdown: number; open_trade_excluded: boolean; paper_win_rate: number | null; paper_average_net_pnl: number | null; paper_best_trade: number | null; paper_worst_trade: number | null; paper_net_pnl: number };
type Strategy = { key: string; name: string; rule: string; signal: boolean; entry_ready: boolean; history_ready: boolean; minimum_bars: number; score: number; evidence: Evidence; performance: StrategyPerformanceData; proposed: Position | null; provisional: boolean; market_fit_score: number; market_fit_reasons: string[]; profit_review: { tier: number; label: string; summary: string; score: number; sample_weight: number; estimated_profit_probability_pct: number | null; validated_profit_probability_pct: number | null; probability_confidence: "unavailable" | "low" | "medium" | "high"; paper_feedback_score: number; paper_profit_probability_pct: number | null; paper_feedback_trades: number; adaptive_parameter_note: string } };
type MarketContext = { as_of: string; direction: string; change_20d_pct: number; volatility: string; atr_14_pct: number; volatility_percentile: number; volume: string; volume_ratio: number | null; basis: string };
type Research = { strategies: Strategy[]; shared_scenario?: SharedScenario; top_three: string[]; qualified_now: string[]; has_positive_replay: boolean; market_context: MarketContext; recommendation: string; version: string; symbol: string; live: boolean; live_detail: string; stale: boolean; ranking: string; as_of: string; history_from: string; history_to: string; bars: number };
type CatalogEntry = { key: string; name: string; status: "PAPER_READY" | "RESEARCH_ONLY"; implementation_key: string | null; catalog_version: string; trader_or_source: string; source_name: string; source_url: string | null; source_kind: string; instrument_types: string[]; market_conditions: string; entry: string; exit: string; stop_loss: string; limitations: string };
type ShadowRow = { strategy: string; total_closed_trades: number; classified_trades: number; matching_trades: number; minimum_matching_trades: number; status: "insufficient" | "exploratory"; matching_average_net_pnl: string | null };
type ShadowFit = { status: "ready" | "unavailable"; reason: string | null; symbol: string; benchmark_session: string | null; recent_direction: string | null; relative_volatility_label: string | null; stock_history_from: string | null; stock_history_to: string | null; rows: ShadowRow[]; ranking_changed: boolean; out_of_sample_validated: boolean; method: string };
type ValidationRow = { strategy: string; name: string; status: "insufficient" | "reviewable"; training_trades: number; validation_trades: number; boundary_trades_excluded: number; training_smoothed_win_pct: number | null; validation_win_pct: number | null; validation_average_net_pnl: number | null; validation_brier: number | null };
type ChronologicalValidation = { status: "ready" | "unavailable"; reason: string | null; symbol: string; history_from: string | null; history_to: string | null; training_from: string | null; training_to: string | null; validation_from: string | null; validation_to: string | null; reserved_from: string | null; reserved_to: string | null; reserved_bars: number; rows: ValidationRow[]; ranking_changed: boolean; parameters_changed: boolean; method: string; rule_version: string | null; study_id: string | null; frozen_at: string | null; frozen: boolean; dataset_digest: string | null; allocation: string | null; planned_risk: string | null };
type PolicyTrialRow = { policy_version: string; target_r: string; status: "insufficient" | "reviewable"; training_trades: number; validation_trades: number; boundary_trades_excluded: number; training_win_pct: number | null; validation_win_pct: number | null; training_average_r: number | null; validation_average_r: number | null; validation_average_net_pnl: number | null };
type PolicyComparison = { status: "ready" | "unavailable"; reason: string | null; strategy: string; trial_id: string | null; frozen_at: string | null; comparison_digest: string | null; reserved_from: string | null; rows: PolicyTrialRow[]; method: string; holdout_evaluated: boolean; ranking_changed: boolean; parameters_changed: boolean };
type OutcomeEvidence = { strategy_version: string; entry_quote_at: string | null; exit_quote_at: string; entry_signal_date: string | null; entry_market_context: MarketContext | null; initial_stop: string; final_stop: string; initial_risk: string; net_pnl: string; exit_reason: string };
type Deployment = { id: string; provider: string; instrument_key: string; symbol: string; strategy: string; version: string; status: string; budget: string; risk_budget: string; created_at: string; updated_at: string; state: { detail: string; quote_mode?: "VERIFIED" | "INDICATIVE"; current_price?: string; current_price_at?: string; current_price_basis?: string; entry_reference_price?: string; entry_threshold_price?: string; latest_daily_signal_date?: string; latest_daily_signal_met?: boolean; latest_daily_signal_after_apply?: boolean; planned_entry_price?: string; last_entry_check?: { at: string; detail: string }; position?: Position; realized_pnl?: string; marked_pnl?: string; quote_at?: string; entry_at?: string; exit_at?: string; exit_reason?: string; exit_price?: string; outcome_evidence?: OutcomeEvidence } };
type DecisionEvent = { id: string; event_type: string; created_at: string; details: Record<string, string | number> };
type Account = { cash: string; initial_cash: string; deployments: Deployment[] };

const rupees = (value: string | number | null | undefined) => value == null ? "Not available" : new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 2 }).format(Number(value));
const date = (value: string) => new Date(value).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });
const active = ["WAITING_FOR_ENTRY", "OPEN", "EXIT_REQUESTED"];

function CatalogDetails({ entry }: { entry: CatalogEntry }) {
  return <div className="strategy-catalog-details">
    <p><strong>Trader / source:</strong> {entry.trader_or_source}</p>
    <p><strong>Instruments:</strong> {entry.instrument_types.join(", ")}</p>
    <p><strong>Market setting:</strong> {entry.market_conditions}</p>
    <p><strong>Entry:</strong> {entry.entry}</p>
    <p><strong>Exit:</strong> {entry.exit}</p>
    <p><strong>Stop loss:</strong> {entry.stop_loss}</p>
    <p><strong>Evidence limit:</strong> {entry.limitations}</p>
    <p><strong>Source:</strong> {entry.source_url ? <a href={entry.source_url} rel="noopener noreferrer" target="_blank">{entry.source_name} ↗</a> : entry.source_name}</p>
  </div>;
}

function StrategyDetails({ strategy, entry }: { strategy: Strategy; entry?: CatalogEntry }) {
  return <>
    <p><strong>Why this rank:</strong> {strategy.profit_review.summary}</p>
    <p>{strategy.market_fit_reasons.join(" · ")}</p>
    <p><strong>Next-trade profit probability:</strong> Not validated. <strong>Smoothed replay win estimate:</strong> {strategy.profit_review.estimated_profit_probability_pct == null ? "Unavailable" : `${strategy.profit_review.estimated_profit_probability_pct.toFixed(1)}% across ${strategy.evidence.closed_trades} closed replay trades`}. This comes from the same replay used for ranking; it is not an out-of-sample forecast. Your live-paper results are shown separately under View performance.</p><p><strong>Parameter note:</strong> {strategy.profit_review.adaptive_parameter_note}</p>
    <p><strong>Evidence source:</strong> {strategy.evidence.replay_closed_trades} replay trades · {strategy.evidence.paper_closed_trades} closed live-paper trades. {strategy.profit_review.paper_feedback_trades} current-version paper outcomes have verified snapshots and affect ordering within the replay tier. Their separate smoothed win frequency is {strategy.profit_review.paper_profit_probability_pct == null ? "unavailable" : `${strategy.profit_review.paper_profit_probability_pct.toFixed(1)}%`}.</p>
    {entry ? <CatalogDetails entry={entry} /> : <p><strong>Entry:</strong> {strategy.rule}. Full source details are unavailable.</p>}
    {!strategy.history_ready ? <p>Needs {strategy.minimum_bars} completed daily candles before this rule can be applied.</p> : strategy.proposed ? <p>Uses the shared stop/target model shown above. Size and levels are recalculated at the actual entry signal.</p> : <p>Allocation/risk is too small for one share.</p>}
    <p>Best / worst replay trade: {rupees(strategy.evidence.best_trade)} / {rupees(strategy.evidence.worst_trade)}.<br />Closed-trade drawdown: {rupees(strategy.evidence.closed_trade_drawdown)}.</p>
  </>;
}

function Scenario({ value }: { value: SharedScenario }) {
  const p = value.position;
  return <section className="equity-scenario" aria-label="Shared trade scenario">
    <h4>Shared trade scenario</h4>
    <p>These paper rules have different entry signals but use the same protective stop, target and trailing model. Their hypothetical outcomes at the same price are identical. Adaptations do not reproduce the source author's complete exit system.</p>
    {p ? <>
      <dl><div><dt>Net if target reached</dt><dd>{rupees(p.target_scenario_net)}</dd></div><div><dt>Net at initial stop</dt><dd>{rupees(p.stop_scenario_net)}</dd></div><div><dt>Indicative size</dt><dd>{p.quantity} shares</dd></div><div><dt>Entry cost incl. fees</dt><dd>{rupees(p.cost)}</dd></div></dl>
      <small>Uses {value.basis === "live_ask" ? "the verified live ask" : "the last completed close"} of {rupees(value.reference_price)}. Recalculated when a future entry signal qualifies. These are scenarios, not expected profit or guaranteed loss limits.</small>
      <details><summary>Prices &amp; calculation breakdown</summary>
        <p>Paper exit policy: {value.exit_policy_version}. The same policy is used for the displayed historical replay and paper execution.</p>
        <p>Reference {rupees(value.reference_price)} · {value.basis === "live_ask" ? "Quote response" : "Daily candle"} {date(value.reference_at)}. Planned allocation {rupees(value.allocation)}; planned risk {rupees(value.planned_risk)}.</p>
        <p>14-bar ATR (simple average): {rupees(value.atr_14_sma)}. Initial distance (1R): {rupees(p.distance)} = greater of 2 × ATR and 0.5% of the signal close ({rupees(value.signal_close)}).</p>
        <p>Modeled entry fill {rupees(p.entry_price)} · initial stop trigger {rupees(p.stop)} · target trigger {rupees(p.target)}. Target is entry + 2R; initial stop is entry − 1R.</p>
        <p>Entry: {p.quantity} × {rupees(p.entry_price)} + {rupees(p.entry_fee)} fees = {rupees(p.cost)} invested.</p>
        <p>At stop: {p.quantity} × {rupees(p.stop_exit_fill)} exit fill − {rupees(p.stop_exit_fee)} exit fees − {rupees(p.cost)} entry cost = {rupees(p.stop_scenario_net)} net.</p>
        <p>At target: {p.quantity} × {rupees(p.target_exit_fill)} exit fill − {rupees(p.target_exit_fee)} exit fees − {rupees(p.cost)} entry cost = {rupees(p.target_scenario_net)} net.</p>
        <p>Illustrative fees {Number(value.fee_pct_per_side)}% and slippage {Number(value.slippage_pct_per_side)}% per side. Fill prices and cash flows round to ₹0.01. Whole-share quantity must fit both allocation and the net stop-loss scenario.</p>
        <p>{value.exit_rule} A stop at the entry price still loses fees and exit slippage. Gaps or downtime can exceed the planned stop loss.</p>
      </details>
    </> : <p>Allocation or planned risk cannot fund one share at this price and stop distance.</p>}
  </section>;
}

function DeploymentRules({ row, entry }: { row: Deployment; entry?: CatalogEntry }) {
  return <div className="equity-deployment-rules">
    <p><strong>Entry rule:</strong> {entry?.entry ?? "Rule description unavailable. Check the strategy catalog before applying again."}</p>
    <p><strong>When checked:</strong> A new completed daily candle after Apply must match the rule. Then the worker checks an open NSE session, the selected provider, a valid two-sided quote, a spread ≤0.5%, enough displayed ask size, and an ask within 2% of the signal close.</p>
    <p><strong>Exit rule:</strong> {entry?.exit ?? "Initial stop at entry −1R, target at entry +2R; after +1R, the stop trails 1R behind the highest observed bid."}</p>
    <p><strong>Initial stop:</strong> {entry?.stop_loss ?? "1R is the greater of 2 × 14-bar ATR and 0.5% of signal close. Gaps may exceed planned risk."}</p>
    {row.status === "WAITING_FOR_ENTRY" && row.state.quote_mode !== "INDICATIVE" && <p><strong>Strict quote gate:</strong> Entry requires an exchange-timestamped bid/ask book. The current Upstox feed has not established that book timestamp; this deployment cannot fill while the gate remains unmet.</p>}
    {row.status === "WAITING_FOR_ENTRY" && row.state.last_entry_check && <p><strong>Last open-session check ({date(row.state.last_entry_check.at)}):</strong> {row.state.last_entry_check.detail}</p>}
  </div>;
}

function DecisionHistory({ deploymentId, apiUrl, token }: { deploymentId: string; apiUrl: string; token: string }) {
  const [events, setEvents] = useState<DecisionEvent[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  async function load() {
    setLoading(true); setError("");
    try {
      const response = await fetch(`${apiUrl}/equity/deployments/${deploymentId}/decisions`, {
        headers: { Authorization: `Bearer ${token}` }, cache: "no-store",
      });
      if (!response.ok) throw new Error("Decision history is unavailable");
      setEvents(await response.json() as DecisionEvent[]);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Decision history is unavailable"); }
    finally { setLoading(false); }
  }
  return <details onToggle={event => { if (event.currentTarget.open && events === null && !loading) void load(); }}>
    <summary>Decision history</summary>
    <p className="muted">First occurrence of each distinct entry-check reason per NSE session, entries, moving-stop changes and exits · latest 200</p>
    <button className="inline-button" type="button" disabled={loading} onClick={() => void load()}>{loading ? "Loading…" : "Refresh decisions"}</button>
    {error && <p className="error-banner" role="alert">{error}</p>}
    {events?.length === 0 && <p className="muted">No worker decisions recorded for this deployment yet.</p>}
    {events && events.length > 0 && <ul>{events.map(event => <li key={event.id}>
      <strong>{event.event_type === "equity.deployment.applied" ? "Applied" : event.event_type === "equity.deployment.entry_checked" ? "Entry check" : event.event_type === "equity.deployment.entered" ? "Entered" : event.event_type === "equity.deployment.exited" ? "Exited" : event.event_type === "equity.deployment.stop_raised" ? "Moving stop raised" : event.event_type === "equity.deployment.cancelled" ? "Cancelled" : event.event_type === "equity.deployment.exit_requested" ? "Exit requested" : event.event_type === "equity.deployment.expired" ? "Waiting expired" : event.event_type}</strong> · {date(event.created_at)}
      {event.event_type === "equity.deployment.applied" && <span> · allocation {rupees(event.details.allocation)} · planned risk {rupees(event.details.planned_risk)} · waits after {event.details.signal_after}</span>}
      {event.event_type === "equity.deployment.entry_checked" && <span> · {event.details.reason} · {event.details.quote_mode === "INDICATIVE" ? "indicative" : "strict"} quote mode · NSE session {event.details.session_date}</span>}
      {event.event_type === "equity.deployment.entered" && <span> · {event.details.quantity} shares at {rupees(event.details.entry_price)} · initial stop {rupees(event.details.initial_stop)} · target {rupees(event.details.target)}</span>}
      {event.event_type === "equity.deployment.stop_raised" && <span> · {rupees(event.details.old_stop)} → {rupees(event.details.new_stop)} · quote {date(String(event.details.quote_at))}</span>}
      {event.event_type === "equity.deployment.exited" && <span> · {event.details.exit_reason} · net P&amp;L {rupees(event.details.net_pnl)} · quote {date(String(event.details.quote_at))}</span>}
      {event.event_type === "equity.deployment.expired" && <span> · {event.details.reason} · {rupees(event.details.reserved_cash_released)} released</span>}
      {event.event_type === "equity.deployment.cancelled" && <span> · {rupees(event.details.reserved_cash_released)} released</span>}
    </li>)}</ul>}
  </details>;
}

export function EquityStrategies({ token, apiUrl }: { token: string; apiUrl: string }) {
  const [items, setItems] = useState<Instrument[]>([]);
  const [selected, setSelected] = useState("");
  const [quickQuery, setQuickQuery] = useState("");
  const [quickResults, setQuickResults] = useState<FoundInstrument[]>([]);
  const [quickBusy, setQuickBusy] = useState(false);
  const [quickError, setQuickError] = useState("");
  const [savingKey, setSavingKey] = useState("");
  const [budget, setBudget] = useState("10000");
  const [risk, setRisk] = useState("200");
  const [indicativeQuotes, setIndicativeQuotes] = useState(false);
  const [research, setResearch] = useState<Research | null>(null);
  const [catalog, setCatalog] = useState<CatalogEntry[]>([]);
  const [catalogError, setCatalogError] = useState("");
  const [librarySearch, setLibrarySearch] = useState("");
  const [libraryView, setLibraryView] = useState("all");
  const [shadow, setShadow] = useState<ShadowFit | null>(null);
  const [shadowBusy, setShadowBusy] = useState(false);
  const [shadowError, setShadowError] = useState("");
  const [validation, setValidation] = useState<ChronologicalValidation | null>(null);
  const [validationBusy, setValidationBusy] = useState(false);
  const [validationError, setValidationError] = useState("");
  const [trialStrategy, setTrialStrategy] = useState("trend");
  const [trial, setTrial] = useState<PolicyComparison | null>(null);
  const [trialBusy, setTrialBusy] = useState(false);
  const [trialError, setTrialError] = useState("");
  const [account, setAccount] = useState<Account | null>(null);
  const [error, setError] = useState("");
  const [pollError, setPollError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [applying, setApplying] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [focusApplied, setFocusApplied] = useState(false);
  const appliedPanel = useRef<HTMLElement>(null);
  const libraryPanel = useRef<HTMLDetailsElement>(null);
  const generation = useRef(0);
  const shadowGeneration = useRef(0);
  const validationGeneration = useRef(0);
  const trialGeneration = useRef(0);
  const command = useRef<{ fingerprint: string; key: string } | null>(null);
  const headers = { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };
  const matchesLibrary = (name: string, entry?: CatalogEntry) =>
    [name, entry?.trader_or_source, entry?.market_conditions, entry?.instrument_types.join(" ")]
      .join(" ").toLowerCase().includes(librarySearch.trim().toLowerCase());
  const visiblePaper = research?.strategies.filter(strategy => libraryView !== "research" &&
    matchesLibrary(strategy.name, catalog.find(entry => entry.implementation_key === strategy.key))) ?? [];
  const visibleResearch = catalog.filter(entry => entry.status === "RESEARCH_ONLY" &&
    libraryView !== "paper" && matchesLibrary(entry.name, entry));

  useEffect(() => {
    if (focusApplied && account?.deployments.some(row => active.includes(row.status))) {
      appliedPanel.current?.focus({ preventScroll: true });
      appliedPanel.current?.scrollIntoView({ behavior: "smooth", block: "start" });
      setFocusApplied(false);
    }
  }, [focusApplied, account]);

  useEffect(() => {
    const onWatchlistChange = () => setRefresh(value => value + 1);
    window.addEventListener("market-watchlist-changed", onWatchlistChange);
    return () => window.removeEventListener("market-watchlist-changed", onWatchlistChange);
  }, []);

  useEffect(() => {
    const query = quickQuery.trim();
    setQuickResults([]);
    setQuickError("");
    if (query.length < 3) { setQuickBusy(false); return; }
    setQuickBusy(true);
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      setQuickBusy(true);
      try {
        const params = new URLSearchParams({ query, exchange: "NSE", segment: "EQ" });
        const response = await fetch(`${apiUrl}/market-data/instrument-search?${params}`, {
          headers: { Authorization: `Bearer ${token}` }, signal: controller.signal,
        });
        if (!response.ok) throw new Error("Stock search is unavailable. Check the market-data connection.");
        const matches = await response.json() as FoundInstrument[];
        if (!controller.signal.aborted) setQuickResults(matches.filter(row => row.exchange === "NSE" && ["EQ", "EQUITY", "ETF"].includes(row.instrument_type)).slice(0, 8));
      } catch (cause) {
        if (!controller.signal.aborted) setQuickError(cause instanceof Error ? cause.message : "Stock search failed");
      } finally {
        if (!controller.signal.aborted) setQuickBusy(false);
      }
    }, 350);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [apiUrl, quickQuery, token]);

  async function saveQuick(row: FoundInstrument) {
    if (savingKey) return;
    setSavingKey(row.instrument_key); setQuickError(""); setNotice("");
    try {
      const item = await read<Instrument>("/watchlist", { method: "POST", body: JSON.stringify({
        query: quickQuery.trim(), instrument_key: row.instrument_key, exchange: "NSE", segment: "EQ",
      }) });
      setItems(current => current.some(saved => saved.id === item.id) ? current : [...current, item]);
      setSelected(item.id);
      setQuickQuery(""); setQuickResults([]);
      setNotice(`${item.trading_symbol} saved. Compare strategies below.`);
      window.dispatchEvent(new Event("market-watchlist-changed"));
    } catch (cause) { setQuickError(cause instanceof Error ? cause.message : "Could not save stock"); }
    finally { setSavingKey(""); }
  }

  async function read<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await fetch(`${apiUrl}${path}`, { ...init, headers });
    if (!response.ok) {
      const body = await response.json().catch(() => null);
      throw new Error(typeof body?.detail === "string" ? body.detail : `Request failed (${response.status}); check your settings and sign-in.`);
    }
    return response.json() as Promise<T>;
  }

  useEffect(() => {
    const controller = new AbortController();
    void read<CatalogEntry[]>("/equity/strategy-catalog", { signal: controller.signal })
      .then(rows => { if (!controller.signal.aborted) { setCatalog(rows); setCatalogError(""); } })
      .catch(() => { if (!controller.signal.aborted) setCatalogError("Strategy library is unavailable."); });
    return () => controller.abort();
  }, [apiUrl, token]);

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    async function poll() {
      try {
        const [watchlist, wallet] = await Promise.all([
          read<Instrument[]>("/watchlist", { signal: controller.signal }),
          read<Account>("/equity/deployments", { signal: controller.signal }),
        ]);
        if (!stopped) {
          setItems(watchlist.filter(item => item.exchange === "NSE" && ["EQ", "EQUITY", "ETF"].includes(item.instrument_type)));
          setAccount(wallet); setPollError("");
        }
      } catch (cause) { if (!stopped) setPollError(cause instanceof Error ? cause.message : "Cannot refresh deployments"); }
      finally { if (!stopped) timer = setTimeout(poll, 5_000); }
    }
    void poll();
    return () => { stopped = true; controller.abort(); clearTimeout(timer); };
  }, [token, apiUrl, refresh]);

  useEffect(() => {
    if (selected && !items.some(item => item.id === selected)) setSelected("");
  }, [items, selected]);

  useEffect(() => {
    const current = ++generation.current;
    setResearch(null); setError("");
    shadowGeneration.current += 1;
    setShadow(null); setShadowError(""); setShadowBusy(false);
    validationGeneration.current += 1;
    setValidation(null); setValidationError(""); setValidationBusy(false);
    trialGeneration.current += 1;
    setTrial(null); setTrialError(""); setTrialBusy(false);
    command.current = null;
    if (!selected) { setBusy(false); return; }
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    async function evaluate() {
      setBusy(true);
      try {
        const result = await read<Research>("/equity/strategies", { method: "POST", signal: controller.signal, body: JSON.stringify({ watchlist_id: selected, budget, risk_budget: risk }) });
        if (!stopped && current === generation.current) { setResearch(result); setError(""); }
      } catch (cause) {
        if (!stopped && current === generation.current) { setResearch(null); setError(cause instanceof Error ? cause.message : "Strategy evidence unavailable"); }
      } finally {
        if (!stopped && current === generation.current) { setBusy(false); timer = setTimeout(evaluate, 60_000); }
      }
    }
    // Avoid a provider request per keystroke while editing capital limits.
    timer = setTimeout(evaluate, 500);
    return () => { stopped = true; controller.abort(); clearTimeout(timer); };
  }, [selected, budget, risk, token, apiUrl]);

  async function compareMarketFit() {
    if (!selected || shadowBusy) return;
    const current = ++shadowGeneration.current;
    setShadowBusy(true); setShadowError(""); setShadow(null);
    try {
      const result = await read<ShadowFit>("/equity/strategy-fit-shadow", {
        method: "POST", body: JSON.stringify({ watchlist_id: selected, budget, risk_budget: risk }),
      });
      if (current === shadowGeneration.current) setShadow(result);
    } catch (cause) {
      if (current === shadowGeneration.current) setShadowError(cause instanceof Error ? cause.message : "Market fit comparison unavailable");
    } finally { if (current === shadowGeneration.current) setShadowBusy(false); }
  }

  async function reviewChronology() {
    if (!selected || validationBusy) return;
    const current = ++validationGeneration.current;
    setValidationBusy(true); setValidationError(""); setValidation(null);
    try {
      const result = await read<ChronologicalValidation>("/equity/chronological-validation", {
        method: "POST", body: JSON.stringify({ watchlist_id: selected, budget, risk_budget: risk }),
      });
      if (current === validationGeneration.current) setValidation(result);
    } catch (cause) {
      if (current === validationGeneration.current) setValidationError(cause instanceof Error ? cause.message : "Chronological review unavailable");
    } finally { if (current === validationGeneration.current) setValidationBusy(false); }
  }

  async function compareExitPolicies() {
    if (!selected || trialBusy) return;
    const current = ++trialGeneration.current;
    setTrialBusy(true); setTrialError(""); setTrial(null);
    try {
      const result = await read<PolicyComparison>("/equity/exit-policy-comparison", {
        method: "POST", body: JSON.stringify({ watchlist_id: selected, strategy: trialStrategy }),
      });
      if (current === trialGeneration.current) setTrial(result);
    } catch (cause) {
      if (current === trialGeneration.current) setTrialError(cause instanceof Error ? cause.message : "Exit-rule comparison unavailable");
    } finally { if (current === trialGeneration.current) setTrialBusy(false); }
  }

  async function apply(key: string) {
    if (applying) return;
    setApplying(key); setNotice(""); setError("");
    const request = { watchlist_id: selected, budget, risk_budget: risk, strategy: key, indicative_quotes: indicativeQuotes };
    const fingerprint = JSON.stringify(request);
    if (command.current?.fingerprint !== fingerprint) command.current = { fingerprint, key: crypto.randomUUID() };
    try {
      const deployment = await read<Deployment>("/equity/deployments", { method: "POST", body: JSON.stringify({ ...request, command_key: command.current.key }) });
      command.current = null;
      setAccount(current => current ? { ...current, deployments: [deployment, ...current.deployments.filter(row => row.id !== deployment.id)] } : current);
      if (libraryPanel.current) libraryPanel.current.open = false;
      setFocusApplied(true);
      setNotice(indicativeQuotes
        ? "Indicative paper strategy deployed. Entry waits for a new daily signal and fresh displayed depth. Results stay out of verified ranking evidence."
        : "Strategy deployed and pinned above. Entry waits for a new completed daily signal.");
      setRefresh(value => value + 1);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Deployment failed"); }
    finally { setApplying(""); }
  }

  async function stop(id: string) {
    setApplying(id); setError("");
    try {
      const row = await read<Deployment>(`/equity/deployments/${id}/stop`, { method: "POST" });
      setAccount(current => current ? { ...current, deployments: current.deployments.map(item => item.id === row.id ? row : item) } : current);
      setNotice(row.state.detail); setRefresh(value => value + 1);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Could not stop deployment"); }
    finally { setApplying(""); }
  }

  const selectedInstrument = items.find(item => item.id === selected);
  const activeDeployment = account?.deployments.find(row =>
    row.provider === selectedInstrument?.provider &&
    row.instrument_key === selectedInstrument?.instrument_key &&
    active.includes(row.status)
  );
  const appliedName = research?.strategies.find(row => row.key === activeDeployment?.strategy)?.name ?? activeDeployment?.strategy;
  const activeDeployments = account?.deployments.filter(row => active.includes(row.status)) ?? [];
  const strategyName = (key: string) => catalog.find(entry => entry.implementation_key === key)?.name ?? research?.strategies.find(row => row.key === key)?.name ?? key;
  const entryZone = (row: Deployment) => {
    const reference = Number(row.state.entry_reference_price);
    if (!Number.isFinite(reference) || reference <= 0) return null;
    return { low: reference * 0.98, high: reference * 1.02 };
  };
  const dailySetupNote = (row: Deployment) => {
    const state = row.state;
    if (state.latest_daily_signal_met === false) return `Daily pattern not met on ${state.latest_daily_signal_date ?? "last candle"}`;
    if (state.latest_daily_signal_met === true && !state.latest_daily_signal_after_apply) return "Pattern met before Apply; a new daily candle is required";
    if (state.latest_daily_signal_met === true) return `Daily pattern met on ${state.latest_daily_signal_date ?? "last candle"}; quote checks still apply`;
    return state.entry_threshold_price ? "Last completed candle reference; other rule checks apply" : "New daily signal required";
  };
  const quoteTimeLabel = (basis?: string) => basis === "exchange" ? "Exchange time" :
    basis === "provider_response" ? "Provider response" :
      basis === "provider_tick" ? "Provider tick" :
        basis === "api_response_received" ? "API receipt" : "Time source unverified";

  return <section className="equity-lab" aria-label="Equity paper strategies">
    <div className="equity-lab-heading"><div><div className="panel-kicker">EQUITY STRATEGIES</div><h3>Choose a stock. Apply a strategy.</h3></div><span className="paper-mode-badge">PAPER ONLY · DAILY</span></div>
    <p>Apply starts monitoring. A trade opens only when a new daily entry signal passes the checks.</p>
    {activeDeployments.length > 0 && <section className="equity-pinned" aria-label="Your active strategies" ref={appliedPanel} tabIndex={-1}>
      <div className="equity-lab-heading"><h3>Your active strategies</h3><small>Paper mode · stays visible after reload</small></div>
      <div className="equity-pinned-grid">{activeDeployments.map(row => <article key={row.id}>
        <div className="equity-deployment-heading"><strong>{row.symbol} · {strategyName(row.strategy)}</strong><span>{row.status.replaceAll("_", " ").toLowerCase()}</span></div>
        <p className="equity-deployment-status">{row.state.detail}</p>
        <div className="equity-entry-comparison" aria-label="Entry plan compared with current market price">
          <div><span>{row.state.position ? "Entry price" : "Daily close reference"}</span><strong>{row.state.position ? rupees(row.state.position.entry_price) : row.state.entry_threshold_price ? `Above ${rupees(row.state.entry_threshold_price)}` : entryZone(row) ? `${rupees(entryZone(row)!.low)} – ${rupees(entryZone(row)!.high)}` : "Waiting for signal"}</strong><small>{row.state.position ? "Filled entry" : dailySetupNote(row)}</small></div>
          <div><span>Current price</span><strong>{row.state.current_price ? rupees(row.state.current_price) : "Unavailable"}</strong><small>{row.state.current_price_at ? `${quoteTimeLabel(row.state.current_price_basis)} · ${date(row.state.current_price_at)}` : "Waiting for quote"}</small></div>
        </div>
        {row.state.position ? <div className="equity-position-strip"><span>Stop <b>{rupees(row.state.position.stop)}</b></span><span>Target <b>{rupees(row.state.position.target)}</b></span><span>Marked P&amp;L <b>{rupees(row.state.marked_pnl)}</b></span></div> : null}
        <details className="equity-card-details"><summary>＋ Details</summary><DeploymentRules row={row} entry={catalog.find(entry => entry.implementation_key === row.strategy)} /><small>Allocated {rupees(row.budget)} · planned risk {rupees(row.risk_budget)} · last checked {date(row.updated_at)}</small>{row.status === "WAITING_FOR_ENTRY" && <small>Applied {date(row.created_at)} · deadline {date(new Date(new Date(row.created_at).getTime() + 7 * 86_400_000).toISOString())}</small>}{row.state.quote_mode === "INDICATIVE" && <small className="equity-evidence-label">Indicative fill · book time unverified · excluded from verified feedback</small>}</details>
        <DecisionHistory deploymentId={row.id} apiUrl={apiUrl} token={token} />
        <button className="inline-button" type="button" disabled={!!applying || row.status === "EXIT_REQUESTED"} onClick={() => void stop(row.id)}>{row.status === "WAITING_FOR_ENTRY" ? "Cancel deployment" : row.status === "EXIT_REQUESTED" ? "Exit requested" : "Request exit"}</button>
      </article>)}</div>
    </section>}
    <div className="equity-controls">
      <label>Stock<select value={selected} disabled={!!applying} onChange={event => setSelected(event.target.value)}><option value="">{items.length ? "Choose a saved stock" : "No stocks saved yet"}</option>{items.map(item => <option value={item.id} key={item.id}>{item.trading_symbol} · {item.provider}</option>)}</select></label>
      <label>Allocation (₹)<input type="number" min="100" max="25000" step="100" disabled={!!applying} value={budget} onChange={event => setBudget(event.target.value)} /></label>
      <label>Planned risk (₹)<input type="number" min="10" max="1000" step="10" disabled={!!applying} value={risk} onChange={event => setRisk(event.target.value)} /></label>
      <div className="equity-cash"><small>Unreserved paper cash</small><strong>{account ? rupees(account.cash) : "Loading…"}</strong><small>Separate ₹10,00,000 equity balance</small></div>
    </div>
    <label className="equity-indicative-choice"><input checked={indicativeQuotes} disabled={!!applying} onChange={event => setIndicativeQuotes(event.target.checked)} type="checkbox" />
      <span><strong>Allow indicative paper fills for the next strategy I apply</strong><small>Uses fresh provider-response snapshots when exchange book time is unavailable. Simulated fills and stops may differ from actual execution; these results are recorded but cannot improve verified strategy rankings. The default is strict.</small></span>
    </label>
    <div className="equity-quick-search">
      <label htmlFor="equity-stock-search">{items.length ? "Find another NSE stock" : "Find your first NSE stock"}</label>
      <input autoComplete="off" id="equity-stock-search" maxLength={50} onChange={event => setQuickQuery(event.target.value)} placeholder="Type a symbol, e.g. RELIANCE" type="search" value={quickQuery} />
      {quickQuery.trim().length > 0 && quickQuery.trim().length < 3 && <small>Type at least 3 characters.</small>}
      {quickBusy && <small role="status">Searching connected market data…</small>}
      {quickError && <p className="error-banner" role="alert">{quickError}</p>}
      {!quickBusy && quickQuery.trim().length >= 3 && !quickError && quickResults.length === 0 && <small>No NSE shares or ETFs found for this search.</small>}
      {quickResults.length > 0 && <ul aria-label="Stock search results">{quickResults.map(row => <li key={row.instrument_key}><span><strong>{row.trading_symbol}</strong>{row.name ? ` · ${row.name}` : ""}</span><button className="inline-button" disabled={!!savingKey || !!applying} onClick={() => void saveQuick(row)} type="button">{savingKey === row.instrument_key ? "Saving…" : "Save & select"}</button></li>)}</ul>}
    </div>
    <p className="muted"><a href="#equity-instruments">Open watchlist, quotes and charts</a></p>
    {!selected && <section className="strategy-library strategy-library-preview" aria-label="Available equity strategies">
      <div className="equity-lab-heading"><div><h3>Equity &amp; ETF strategies</h3><small>Paper-ready rules, grouped like the AlgoCrab strategy board</small></div><span className="paper-mode-badge">RANK AFTER STOCK SELECTION</span></div>
      <p className="muted">Choose a saved stock to calculate market fit, historical ranking, and the Apply action. The strategy cards stay visible here so you can browse the rules before choosing an instrument.</p>
      <div className="strategy-color-legend" aria-label="Strategy evidence legend"><span className="strategy-color-legend--green">Green · paper-ready</span><span className="strategy-color-legend--amber">Amber · limited evidence</span><span className="strategy-color-legend--red">Red · research-only or blocked</span></div>
      <div className="equity-preview-grid">{catalog.filter(entry => entry.status === "PAPER_READY").map(entry => <article className="equity-preview-card strategy-scorecard--green" key={entry.key}>
        <div className="equity-preview-card-heading"><span aria-hidden="true">↗</span><strong>{entry.name}</strong><span className="equity-preview-status">PAPER READY</span></div>
        <small>{entry.trader_or_source}</small><p>{entry.market_conditions}</p>
        <details><summary>Entry, exit &amp; risk</summary><CatalogDetails entry={entry} /></details>
      </article>)}</div>
      {catalog.length === 0 && !catalogError && <p className="muted">Loading the strategy library…</p>}
      {catalogError && <p className="error-banner" role="alert">{catalogError}</p>}
    </section>}
    {error && <p className="error-banner" role="alert">{error}</p>}
    {pollError && <p className="error-banner" role="alert">Deployment status could not refresh: {pollError}</p>}
    {notice && <p className="equity-notice" role="status">{notice}</p>}
    {busy && <p className="muted" role="status">Refreshing daily evidence and current quote…</p>}
    {research && <>
      <div className="equity-evidence"><strong>{research.live ? "Fresh market quote" : "Historical ranking"} · {research.symbol}</strong><span>{research.live_detail}</span><small>{research.bars} completed daily bars · {date(research.history_from)} to {date(research.history_to)} · Updated {date(research.as_of)}</small></div>
      {research.shared_scenario ? <Scenario value={research.shared_scenario} /> : <p className="muted">The API needs a refresh to show the shared calculation breakdown.</p>}
      <div className="equity-lab-heading"><h3>Top {research.top_three.length} candidates · {research.symbol}</h3><small>Historical profit, win rate, risk &amp; market fit</small></div>
      <p className={`equity-ranking-note ${research.has_positive_replay ? "" : "equity-ranking-caution"}`}>{research.recommendation}</p>
      <details className="equity-context-details"><summary>Completed-session conditions: {research.market_context.direction} · {research.market_context.volatility} volatility · {research.market_context.volume} volume</summary><div className="equity-market-context" aria-label="Selected stock market context"><span>Direction: {research.market_context.direction} ({research.market_context.change_20d_pct.toFixed(1)}% over 20 sessions)</span><span>Volatility: {research.market_context.volatility} ({research.market_context.volatility_percentile}/100 relative; ATR {research.market_context.atr_14_pct.toFixed(1)}%)</span><span>Volume: {research.market_context.volume}{research.market_context.volume_ratio == null ? " · ratio unavailable" : ` · ${research.market_context.volume_ratio.toFixed(2)}× prior 20-session average`}</span><small>{research.market_context.basis} · {date(research.market_context.as_of)}</small></div></details>
      {research.stale && <p className="error-banner">History is stale. Applying a new deployment is blocked.</p>}
      {activeDeployment && <p className="equity-active-note" role="status">{appliedName} is {activeDeployment.status.replaceAll("_", " ").toLowerCase()} for {research.symbol}. Only one paper strategy can be active for this stock; cancel or close it before applying another.</p>}
      <details className="equity-context-details"><summary>Compare replay results in similar NIFTY conditions</summary><div className="equity-shadow">
        <div><strong>Market-condition comparison</strong><p>See how closed replay trades behaved when NIFTY had conditions like its latest completed session. This does not change the ranking or deploy a strategy.</p></div>
        <button className="inline-button" type="button" disabled={shadowBusy || busy} onClick={() => void compareMarketFit()}>{shadowBusy ? "Comparing…" : shadow ? "Refresh comparison" : "Compare market fit"}</button>
      </div>
      {shadowError && <p className="error-banner" role="alert">{shadowError}</p>}
      {shadow?.status === "unavailable" && <p className="muted" role="status">Market comparison unavailable: {shadow.reason}</p>}
      {shadow?.status === "ready" && <p className="equity-shadow-summary" role="status">NIFTY 50 on {shadow.benchmark_session}: {shadow.recent_direction} · {shadow.relative_volatility_label} volatility. Exploratory historical comparison; no validated prediction.</p>}
      </details>
      <details className="equity-context-details"><summary>Check strategy results across time</summary><div className="equity-shadow">
        <div><strong>Chronological replay check</strong><p>Compare earlier training results with later trades. The newest sessions are reserved for a future final check. This does not change the ranking, deployed rules or paper risk.</p></div>
        <button className="inline-button" type="button" disabled={validationBusy || busy} onClick={() => void reviewChronology()}>{validationBusy ? "Checking…" : validation ? "Refresh check" : "Run check"}</button>
      </div>
      <ValidationAttemptHistory apiUrl={apiUrl} token={token} refresh={validationBusy} />
      {validationError && <p className="error-banner" role="alert">{validationError}</p>}
      {validation?.status === "unavailable" && <p className="muted" role="status">Chronological check unavailable: {validation.reason}</p>}
      {validation?.status === "ready" && <>
        <p className="equity-shadow-summary" role="status">Training {validation.training_from} to {validation.training_to} · later check {validation.validation_from} to {validation.validation_to}. Latest {validation.reserved_bars} sessions ({validation.reserved_from} to {validation.reserved_to}) remain reserved.</p>
        <p className="muted">{validation.frozen ? `Study frozen ${validation.frozen_at ? date(validation.frozen_at) : ""} under ${validation.rule_version}; future checks return this saved evidence.` : "Exploratory sample: no rule has 20 trades in both earlier windows, so this window is not frozen yet."} Allocation {rupees(validation.allocation)} · planned risk {rupees(validation.planned_risk)}.</p>
        <div className="strategy-library-list">{validation.rows.map(row => <div className="equity-shadow-card" key={row.strategy}><strong>{row.name}</strong><small>{row.training_trades} training trades · {row.validation_trades} later trades{row.boundary_trades_excluded ? ` · ${row.boundary_trades_excluded} crossing boundary excluded` : ""}</small><span>{row.status === "reviewable" ? `Earlier smoothed wins ${row.training_smoothed_win_pct?.toFixed(1)}% · later observed wins ${row.validation_win_pct?.toFixed(1)}% · later net average ${rupees(row.validation_average_net_pnl)}` : "Insufficient closed trades to compare rates (20 required in each window)."}</span></div>)}</div>
        <p className="muted">{validation.method} Corporate actions and provider history gaps still require independent verification.</p>
      </>}
      </details>
      <details className="equity-context-details"><summary>Compare paper exit rules · research only</summary><div className="equity-shadow">
        <div><strong>Versioned exit-rule comparison</strong><p>Compare the current +2R target with predeclared +1.5R and +3R targets on a frozen study. No paper rule or ranking changes.</p></div>
        <label>Strategy <select value={trialStrategy} onChange={event => { trialGeneration.current += 1; setTrialStrategy(event.target.value); setTrial(null); setTrialError(""); }}>{research.strategies.map(row => <option key={row.key} value={row.key}>{row.name}</option>)}</select></label>
        <button className="inline-button" type="button" disabled={trialBusy || busy} onClick={() => void compareExitPolicies()}>{trialBusy ? "Comparing…" : trial ? "Show saved comparison" : "Compare exit rules"}</button>
      </div>
      {trialError && <p className="error-banner" role="alert">{trialError}</p>}
      {trial?.status === "unavailable" && <p className="muted" role="status">Comparison unavailable: {trial.reason} Run the chronological check above first.</p>}
      {trial?.status === "ready" && <><p className="equity-shadow-summary" role="status">Saved research comparison for {strategyName(trial.strategy)}. Sessions from {trial.reserved_from} onward were not evaluated; no rule or ranking changed.</p>
        <div className="strategy-library-list">{trial.rows.map(row => <div className="equity-shadow-card" key={row.policy_version}><strong>{row.policy_version === "equity-exit-v1" ? "Current rule" : `Research target +${row.target_r}R`}</strong><small>{row.training_trades} training trades · {row.validation_trades} later trades{row.boundary_trades_excluded ? ` · ${row.boundary_trades_excluded} crossing boundary excluded` : ""}</small><span>{row.status === "reviewable" ? `Earlier wins ${row.training_win_pct?.toFixed(1)}% · later wins ${row.validation_win_pct?.toFixed(1)}% · later average ${rupees(row.validation_average_net_pnl)} (${row.validation_average_r?.toFixed(2)}R)` : "Insufficient trades to compare rates (20 required in each window)."}</span></div>)}</div>
        <p className="muted">{trial.method}</p>
      </>}
      </details>
      <div className="strategy-color-legend" aria-label="Strategy evidence legend"><span className="strategy-color-legend--green">Green · stronger evidence</span><span className="strategy-color-legend--amber">Amber · limited evidence</span><span className="strategy-color-legend--red">Red · blocked or unavailable</span></div>
      <div className="equity-card-grid">{research.strategies.filter(strategy => research.top_three.includes(strategy.key)).map((strategy) => <article className={`equity-card strategy-scorecard--${strategy.profit_review.tier >= 2 ? "green" : strategy.profit_review.tier === 1 ? "amber" : "red"} ${research.top_three[0] === strategy.key ? "equity-card-first" : ""}`} key={strategy.key}>
        <div className="equity-card-rank">#{research.top_three.indexOf(strategy.key) + 1} Ranked candidate<span>{strategy.provisional ? "Limited evidence" : "Historical sample"}</span></div>
        <h4>{strategy.name}</h4>
        <small className="muted">{catalog.find(entry => entry.implementation_key === strategy.key)?.trader_or_source}</small>
        <small className={`equity-evidence-label ${strategy.profit_review.tier >= 2 ? "equity-evidence-positive" : ""}`}>{strategy.profit_review.label}</small>
        {activeDeployment?.strategy === strategy.key && <span className="equity-applied-badge">Applied to paper trading · {activeDeployment.status.replaceAll("_", " ").toLowerCase()}</span>}
        <div className="equity-signal">{!strategy.history_ready ? `Needs ${strategy.minimum_bars} daily candles` : strategy.entry_ready ? "Setup qualifies · apply waits for a new signal" : strategy.signal ? "Setup found · execution checks pending" : "Waiting for entry signal"}</div>
        <dl><div><dt>Profit probability</dt><dd>Not validated</dd><small>Replay win estimate {strategy.profit_review.estimated_profit_probability_pct == null ? "unavailable" : `${strategy.profit_review.estimated_profit_probability_pct.toFixed(1)}%`} · {strategy.evidence.closed_trades} trades</small></div><div><dt>Replay average / trade</dt><dd>{rupees(strategy.evidence.average_net_pnl)}</dd></div><div><dt>Market fit</dt><dd>{strategy.market_fit_score}/4</dd><small>{strategy.market_fit_reasons.join(" · ")}</small></div><div><dt>Worst replay trade</dt><dd>{rupees(strategy.evidence.worst_trade)}</dd></div></dl>
        <small className="muted">Replay sets the evidence tier; {strategy.profit_review.paper_feedback_trades} verified paper outcomes make a bounded score adjustment. Uses the shared stop/target rules above.</small>
        {shadow?.status === "ready" && (() => {
          const fit = shadow.rows.find(row => row.strategy === strategy.key);
          return fit ? <div className="equity-shadow-card"><strong>Similar NIFTY conditions</strong><small>{fit.matching_trades} matching closed trades · {fit.classified_trades} classified · {fit.total_closed_trades} total</small><span>{fit.status === "exploratory" ? `Historical average per matching trade: ${rupees(fit.matching_average_net_pnl)}` : `Need ${fit.minimum_matching_trades} matching trades to show performance; evidence is insufficient.`}</span></div> : null;
        })()}
        <StrategyPerformance name={strategy.name} symbol={research.symbol} data={strategy.performance} historyFrom={research.history_from} historyTo={research.history_to} asOf={research.as_of} stale={research.stale} />
        <details className="equity-card-details"><summary>Strategy details, source &amp; statistics</summary><StrategyDetails strategy={strategy} entry={catalog.find(entry => entry.implementation_key === strategy.key)} /></details>
        <button className="primary-button" type="button" disabled={!!applying || !account || busy || research.stale || !!pollError || !strategy.proposed || !!activeDeployment} onClick={() => void apply(strategy.key)}>{applying === strategy.key ? "Applying…" : activeDeployment?.strategy === strategy.key ? "Applied to this stock" : activeDeployment ? "Another strategy is active for this stock" : "Apply for paper trading"}</button>
      </article>)}</div>
      <details className="strategy-library" aria-label="All strategies" ref={libraryPanel}><summary>Browse all strategies <span>{research.strategies.length} paper rules · {catalog.filter(entry => entry.status === "RESEARCH_ONLY").length} research methods</span></summary>
        <p>Compare every paper rule for {research.symbol}. Figures describe historical replay only. View performance to see your live-paper results separately. Paper adaptations use the shared stop/target scenario above; best and worst trades are not future limits. Any paper rule can be selected here, including one outside the top three.</p>
        <div className="strategy-library-filters"><label>Find a strategy or trader<input type="search" value={librarySearch} onChange={event => setLibrarySearch(event.target.value)} placeholder="Name, trader or market condition" /></label><label>Show<select value={libraryView} onChange={event => setLibraryView(event.target.value)}><option value="all">All strategies</option><option value="paper">Paper trading available</option><option value="research">Research only</option></select></label></div>
        <p className="muted" role="status">{visiblePaper.length} paper rules · {visibleResearch.length} research methods match</p>
        {visiblePaper.length > 0 && <div className="strategy-library-heading"><h4>Paper strategies</h4><small>Ordered by profit evidence &amp; risk · choose any rule</small></div>}
        <div className="strategy-library-list">{visiblePaper.map(strategy => <article className="strategy-library-row" key={strategy.key}>
          <div className="strategy-library-title"><strong>{strategy.name}</strong><small>{catalog.find(entry => entry.implementation_key === strategy.key)?.trader_or_source}</small><small>{research.top_three.includes(strategy.key) ? `#${research.top_three.indexOf(strategy.key) + 1} candidate` : "Outside top three"} · {strategy.profit_review.label}</small></div>
          <div className="strategy-library-metrics"><span><small>Profit probability</small><strong>Not validated</strong><small>Replay win estimate {strategy.profit_review.estimated_profit_probability_pct == null ? "unavailable" : `${strategy.profit_review.estimated_profit_probability_pct.toFixed(1)}%`} · {strategy.evidence.replay_closed_trades} replay · {strategy.evidence.paper_closed_trades} paper</small></span><span><small>Replay average / trade</small><strong>{rupees(strategy.evidence.average_net_pnl)}</strong></span><span><small>Market fit</small><strong>{strategy.market_fit_score}/4</strong></span><span><small>Worst replay trade</small><strong>{rupees(strategy.evidence.worst_trade)}</strong></span></div>
          <small className="strategy-library-signal">{!strategy.history_ready ? `Needs ${strategy.minimum_bars} daily candles` : strategy.entry_ready ? "Setup qualifies · new signal required after Apply" : strategy.signal ? "Setup found · execution checks pending" : "Waiting for entry signal"}</small>
          <StrategyPerformance name={strategy.name} symbol={research.symbol} data={strategy.performance} historyFrom={research.history_from} historyTo={research.history_to} asOf={research.as_of} stale={research.stale} />
          <details className="strategy-library-details"><summary>Entry, exit, stop &amp; evidence</summary><StrategyDetails strategy={strategy} entry={catalog.find(entry => entry.implementation_key === strategy.key)} /></details>
          <button className="inline-button" type="button" disabled={!!applying || !account || busy || research.stale || !!pollError || !strategy.proposed || !!activeDeployment} onClick={() => void apply(strategy.key)}>{applying === strategy.key ? "Applying…" : activeDeployment?.strategy === strategy.key ? "Applied to this stock" : activeDeployment ? "Another strategy is active" : "Apply for paper trading"}</button>
        </article>)}</div>
        {catalogError && <p className="muted" role="status">{catalogError}</p>}
        {visibleResearch.length > 0 && <div className="strategy-library-research"><h4>Published trader methods · research only</h4><p>These methods have no local replay win rate or paper Apply action. They need complete executable rules and independent testing first.</p>{visibleResearch.map(entry => <details key={entry.key}><summary>{entry.name} <small>{entry.trader_or_source}</small></summary><CatalogDetails entry={entry} /></details>)}</div>}
      </details>
      <details className="equity-method"><summary>How ranking, estimates and stops work</summary><p>{research.ranking}</p><p>These are fixed v1 rules, not optimized or validated predictions. The displayed replay win estimate is sample-size smoothed and cannot be used as a calibrated chance that the next trade wins. No claimed maximum profit. The planned risk is not a maximum loss: gaps, stale prices and downtime can exceed it.</p><p>Daily replay uses next-bar entries, stop-first handling for ambiguous bars, and next-bar activation of trailing adjustments. Historical OHLC cannot reproduce the live bid/ask path. Each side includes illustrative 0.10% fees and 0.05% slippage; these are not your broker’s exact charges. Open replay trades are excluded from closed-trade statistics.</p><p>Ranking uses one year of provider history without corporate-action normalization or out-of-sample calibration. The separate on-demand chronological check can expose later-window results, but never changes rank or parameters. Live quote checks require a verified exchange trade within 60 seconds and a spread no greater than 0.5%. Entries more than 2% from the signal close are blocked.</p></details>
      {shadow?.status === "ready" && <details className="equity-method"><summary>About the market comparison</summary><p>{shadow.method}</p><p>It uses up to 900 calendar days of stock and NIFTY history. A trade matches when NIFTY direction and relative volatility at the signal close equal the latest completed NIFTY session. Missing benchmark sessions are excluded. This is descriptive, subject to provider and corporate-action data quality, and has no out-of-sample validation.</p></details>}
    </>}
    <details className="equity-history" open={activeDeployments.length > 0}><summary>Logs &amp; order history ({account?.deployments.length ?? 0})</summary>
    <p className="muted">Refreshes every 5 seconds · latest 200</p>
    <p className="muted">Waiting deployments expire after seven days. Cancel releases unused allocation. For an open position, Request exit waits for a fresh quote in an open NSE session. Positions can remain open overnight.</p>
    {!account?.deployments.length && <p className="muted">No equity deployments yet.</p>}
    <div className="equity-deployments">{account?.deployments.map(row => <article key={row.id}>
      <div className="equity-deployment-heading"><strong>{row.symbol} · {row.strategy}</strong><span>{row.status.replaceAll("_", " ")}</span></div>
      <p>{row.state.detail}</p>
      <details><summary>Entry, exit &amp; last market-hours check</summary><DeploymentRules row={row} entry={catalog.find(entry => entry.implementation_key === row.strategy)} /></details>
      <small>Allocated {rupees(row.budget)} · planned risk {rupees(row.risk_budget)} · {row.version} · Last check {date(row.updated_at)}</small>
      {row.state.position && <><p>{row.state.position.quantity} shares · Entry {rupees(row.state.position.entry_price)} · Stop {rupees(row.state.position.stop)} · Target {rupees(row.state.position.target)} · Trail {row.state.position.trailing_active ? "active" : "awaiting +1R"}</p><strong>{row.status === "CLOSED" ? `Realized net P&L ${rupees(row.state.realized_pnl)} · ${row.state.exit_reason}` : `Last marked net P&L ${rupees(row.state.marked_pnl)}`}</strong>{row.state.entry_at && <small>Entered {date(row.state.entry_at)}{row.state.exit_at ? ` · Exited ${date(row.state.exit_at)} at ${rupees(row.state.exit_price)}` : ""}</small>}{row.state.quote_at && <small>Last mark quote: {date(row.state.quote_at)}</small>}</>}
      {row.state.outcome_evidence && <details><summary>Trade evidence</summary><p>Entry signal: {row.state.outcome_evidence.entry_signal_date ?? "Not recorded"} · Entry quote: {row.state.outcome_evidence.entry_quote_at ? date(row.state.outcome_evidence.entry_quote_at) : "Not recorded"} · Exit quote: {date(row.state.outcome_evidence.exit_quote_at)}</p><p>Original stop {rupees(row.state.outcome_evidence.initial_stop)} · Final moving stop {rupees(row.state.outcome_evidence.final_stop)} · Original modeled risk {rupees(row.state.outcome_evidence.initial_risk)} · Net P&amp;L {rupees(row.state.outcome_evidence.net_pnl)}</p><p>At entry: {row.state.outcome_evidence.entry_market_context ? `${row.state.outcome_evidence.entry_market_context.direction} direction · ${row.state.outcome_evidence.entry_market_context.volatility} volatility · ${row.state.outcome_evidence.entry_market_context.volume} volume` : "Market context not recorded"}. Strategy version {row.state.outcome_evidence.strategy_version}.</p></details>}
      <DecisionHistory deploymentId={row.id} apiUrl={apiUrl} token={token} />
      {active.includes(row.status) && <button className="inline-button" disabled={!!applying || row.status === "EXIT_REQUESTED"} onClick={() => void stop(row.id)} type="button">{row.status === "WAITING_FOR_ENTRY" ? "Cancel deployment" : row.status === "EXIT_REQUESTED" ? "Exit requested" : "Request exit"}</button>}
    </article>)}</div>
    </details>
  </section>;
}
