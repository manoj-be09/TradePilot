"use client";

import { useCallback, useEffect, useState } from "react";

type StrategyDemotion = {
  strategy_key: string;
  paused: boolean;
  reason: string | null;
  summary: string | null;
  demoted_at: string | null;
  stage: "ELIGIBLE" | "DEMOTED" | "PROBATION";
  block_reason: string | null;
  probation_starts_at: string | null;
  probation_trades: number;
  probation_trades_needed: number;
  promotion_needs: string[];
  check: { max_drawdown_per_lot: string; drawdown_limit_per_lot: string; limit_source: "BACKTEST" | "FALLBACK" };
};

type EligibilityEvent = {
  strategy_key: string;
  event_type: "DEMOTED" | "PROBATION_STARTED" | "PROBATION_FAILED" | "PROMOTED" | "REINSTATED";
  reason: string;
  summary: string;
  occurred_at: string;
};

const label = (key: string) => key.replaceAll("_", " ");
const moment = (value: string) => new Date(value).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", dateStyle: "medium", timeStyle: "short" });
const STAGE_LABEL: Record<StrategyDemotion["stage"], string> = {
  ELIGIBLE: "Eligible",
  DEMOTED: "Paused",
  PROBATION: "On probation",
};
const EVENT_LABEL: Record<EligibilityEvent["event_type"], string> = {
  DEMOTED: "Demoted",
  PROBATION_STARTED: "Probation started",
  PROBATION_FAILED: "Probation failed",
  PROMOTED: "Promoted",
  REINSTATED: "Reinstated by owner",
};

/** Shows strategies automatic demotion has paused; renders nothing when none are. */
export function StrategyPauses({ token, apiUrl }: { token: string; apiUrl: string }) {
  const [paused, setPaused] = useState<StrategyDemotion[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (signal?: AbortSignal) => {
    try {
      const response = await fetch(`${apiUrl}/paper/strategies/demotions`, {
        headers: { Authorization: `Bearer ${token}` }, signal,
      });
      if (!response.ok) return;
      const rows = await response.json() as StrategyDemotion[];
      if (!signal?.aborted) setPaused(rows.filter((row) => row.paused));
    } catch {
      // Status is advisory here; the server still refuses entries for paused strategies.
    }
  }, [apiUrl, token]);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    const timer = setInterval(() => void load(controller.signal), 30_000);
    return () => { controller.abort(); clearInterval(timer); };
  }, [load]);

  async function reinstate(key: string) {
    setBusy(key); setError(null);
    try {
      const response = await fetch(`${apiUrl}/paper/strategies/${encodeURIComponent(key)}/reinstate`, {
        method: "POST", headers: { Authorization: `Bearer ${token}` },
      });
      if (!response.ok) throw new Error("Could not reinstate the strategy");
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not reinstate the strategy");
    } finally { setBusy(null); }
  }

  if (!paused.length) return null;
  return <section className="worker-status worker-status--warning" aria-label="Paused strategies" aria-live="polite">
    <div className="worker-status-heading"><strong>Paused strategies</strong><span>New paper entries limited</span></div>
    <p>Automatic demotion limited these strategies. Open positions keep their exits. Reinstating counts only trades that close afterwards.</p>
    <ul>
      {paused.map((row) => <li key={row.strategy_key}>
        <strong>{label(row.strategy_key)}</strong>: {row.block_reason ?? row.summary ?? row.reason}{" "}
        <button className="refresh-button" type="button" disabled={busy === row.strategy_key}
          onClick={() => void reinstate(row.strategy_key)}>
          {busy === row.strategy_key ? "Reinstating…" : "Reinstate"}
        </button>
      </li>)}
    </ul>
    {error && <p role="alert">{error}</p>}
  </section>;
}

/** Research page: where each strategy stands and every recorded demotion and promotion. */
export function StrategyEligibilityHistory({ token, apiUrl }: { token: string; apiUrl: string }) {
  const [strategies, setStrategies] = useState<StrategyDemotion[] | null>(null);
  const [events, setEvents] = useState<EligibilityEvent[] | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    const get = async <T,>(path: string) => {
      const response = await fetch(`${apiUrl}${path}`, {
        headers: { Authorization: `Bearer ${token}` }, cache: "no-store", signal: controller.signal,
      });
      if (!response.ok) throw new Error(String(response.status));
      return await response.json() as T;
    };
    Promise.all([
      get<StrategyDemotion[]>("/paper/strategies/demotions"),
      get<EligibilityEvent[]>("/paper/strategies/eligibility-history"),
    ]).then(([rows, history]) => {
      if (!controller.signal.aborted) { setStrategies(rows); setEvents(history); setFailed(false); }
    }).catch(() => { if (!controller.signal.aborted) setFailed(true); });
    return () => controller.abort();
  }, [apiUrl, token]);

  if (failed) return <p className="error-banner" role="alert">Strategy eligibility is unavailable. Entries are still checked on the server.</p>;
  if (strategies === null || events === null) return null;
  return <section className="observed-performance" aria-label="Strategy eligibility">
    <div className="trading-dashboard-activity-heading"><div><h3>Strategy eligibility</h3><p>Closed paper trades decide which strategies may take new entries · simulated</p></div><span className="dashboard-status">Rule-based · not a forecast</span></div>
    {strategies.length === 0 ? <p className="trading-dashboard-empty">No paper strategies have been traded yet.</p> : <div className="observed-performance-scroll" role="region" aria-label="Strategy eligibility table; scroll horizontally for all columns" tabIndex={0}><table>
      <thead><tr><th scope="col">Strategy</th><th scope="col">Stage</th><th scope="col">Why</th><th scope="col">Closed-trade drawdown / limit, per lot</th><th scope="col">Needed for promotion</th></tr></thead>
      <tbody>{strategies.map((row) => <tr key={row.strategy_key}>
        <th scope="row"><strong>{label(row.strategy_key)}</strong></th>
        <td>{STAGE_LABEL[row.stage]}</td>
        <td>{row.block_reason ?? "Within its limits."}</td>
        <td>₹{row.check.max_drawdown_per_lot} / ₹{row.check.drawdown_limit_per_lot}<small>{row.check.limit_source === "BACKTEST" ? "Saved backtest baseline" : "No saved baseline: fallback limit"}</small></td>
        <td>{row.stage === "PROBATION" ? row.promotion_needs.join("; ") || "Ready" : row.stage === "DEMOTED" ? "Cooling-off first" : "Nothing"}</td>
      </tr>)}</tbody>
    </table></div>}
    <h4>Demotions and promotions</h4>
    {events.length === 0 ? <p className="trading-dashboard-empty">None recorded yet.</p> : <div className="observed-performance-scroll" role="region" aria-label="Strategy eligibility history" tabIndex={0}><table>
      <thead><tr><th scope="col">When (IST)</th><th scope="col">Strategy</th><th scope="col">Event</th><th scope="col">Reason</th></tr></thead>
      <tbody>{events.map((event, index) => <tr key={`${event.strategy_key}-${event.occurred_at}-${index}`}>
        <td>{moment(event.occurred_at)}</td>
        <th scope="row">{label(event.strategy_key)}</th>
        <td>{EVENT_LABEL[event.event_type]}</td>
        <td>{event.summary}</td>
      </tr>)}</tbody>
    </table></div>}
    <details className="workspace-disclosure"><summary>How eligibility works</summary><p>A strategy is paused when its closed paper trades reach the loss streak limit, its drawdown per lot passes the limit (a multiple of its saved backtest drawdown, or a fixed share of capital without one), or a closed trade has no usable P&amp;L. A pause blocks new entries and arming only; open positions keep their exits.</p><p>After a cooling-off period the strategy goes on probation: one lot, one position or armed entry at a time, and the ranking never selects it. It is promoted once the required probation trades have closed with a positive total P&amp;L per lot, inside tighter limits, and a saved backtest baseline exists. A breach restarts the cooling-off period. These rules only decide eligibility; they do not change a strategy's own rules or the account risk limits. Passing probation is a small simulated sample and says nothing about future results.</p></details>
  </section>;
}
