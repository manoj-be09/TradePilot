"use client";

import { useEffect, useState } from "react";

type Stage = "SHADOW" | "PAPER_APPROVED" | "SUPERSEDED" | "DEMOTED" | "RETIRED";
type RuleRow = {
  rule_key: string;
  version: number;
  stage: Stage;
  forward_from: string;
  checked_at: string | null;
  check: { trades?: number; average?: number; t_value?: number | null; needs?: string[] };
};
type RuleEvent = {
  rule_key: string;
  version: number;
  event_type: "REGISTERED" | "PROMOTED" | "SUPERSEDED" | "DEMOTED" | "ROLLED_BACK" | "RETIRED";
  summary: string;
  occurred_at: string;
};
type Engine = { t_bar: number; trials_tested: number; rules: RuleRow[]; events: RuleEvent[] };

const label = (key: string) => key.replaceAll("_", " ");
const moment = (value: string) => new Date(value).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", dateStyle: "medium", timeStyle: "short" });
const STAGE_LABEL: Record<Stage, string> = {
  SHADOW: "Forward shadow",
  PAPER_APPROVED: "Cleared for paper",
  SUPERSEDED: "Replaced by a newer version",
  DEMOTED: "Demoted",
  RETIRED: "Retired",
};
const EVENT_LABEL: Record<RuleEvent["event_type"], string> = {
  REGISTERED: "Frozen, shadow started",
  PROMOTED: "Promoted",
  SUPERSEDED: "Replaced",
  DEMOTED: "Demoted",
  ROLLED_BACK: "Rolled back",
  RETIRED: "Retired",
};

/** Research page: frozen research rules, where the rule engine has them, and every decision. */
export function RuleEngineTrail({ token, apiUrl }: { token: string; apiUrl: string }) {
  const [engine, setEngine] = useState<Engine | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    fetch(`${apiUrl}/research/rule-engine`, {
      headers: { Authorization: `Bearer ${token}` }, cache: "no-store", signal: controller.signal,
    }).then(async (response) => {
      if (!response.ok) throw new Error(String(response.status));
      const body = await response.json() as Engine;
      if (!controller.signal.aborted) { setEngine(body); setFailed(false); }
    }).catch(() => { if (!controller.signal.aborted) setFailed(true); });
    return () => controller.abort();
  }, [apiUrl, token]);

  if (failed) return <p className="error-banner" role="alert">The research rule engine is unavailable.</p>;
  if (engine === null) return null;
  return <section className="observed-performance" aria-label="Research rule engine">
    <div className="trading-dashboard-activity-heading"><div><h3>Research rule engine</h3><p>Frozen rules scored only on sessions after they were fixed · simulated</p></div><span className="dashboard-status">Rule-based · not a forecast</span></div>
    {engine.rules.length === 0 ? <p className="trading-dashboard-empty">The nightly job has not run the engine yet.</p> : <div className="observed-performance-scroll" role="region" aria-label="Research rules table; scroll horizontally for all columns" tabIndex={0}><table>
      <thead><tr><th scope="col">Rule</th><th scope="col">Stage</th><th scope="col">Shadow trades</th><th scope="col">Average after charges</th><th scope="col">t value / bar</th><th scope="col">Needed for promotion</th></tr></thead>
      <tbody>{engine.rules.map((row) => <tr key={`${row.rule_key}-${row.version}`}>
        <th scope="row"><strong>{label(row.rule_key)}</strong><small>v{row.version} · from {row.forward_from}</small></th>
        <td>{STAGE_LABEL[row.stage]}</td>
        <td>{row.check.trades ?? 0}</td>
        <td>{row.check.trades ? `₹${Math.round(row.check.average ?? 0).toLocaleString("en-IN")}` : "None yet"}</td>
        <td>{row.check.t_value == null ? "n/a" : row.check.t_value.toFixed(2)} / {engine.t_bar.toFixed(2)}</td>
        <td>{row.stage === "SHADOW" ? (row.check.needs ?? []).join("; ") || "Ready" : "Nothing"}</td>
      </tr>)}</tbody>
    </table></div>}
    <h4>Engine decisions</h4>
    {engine.events.length === 0 ? <p className="trading-dashboard-empty">None recorded yet.</p> : <div className="observed-performance-scroll" role="region" aria-label="Research rule engine decisions" tabIndex={0}><table>
      <thead><tr><th scope="col">When (IST)</th><th scope="col">Rule</th><th scope="col">Decision</th><th scope="col">Why</th></tr></thead>
      <tbody>{engine.events.map((event, index) => <tr key={`${event.rule_key}-${event.version}-${event.occurred_at}-${index}`}>
        <td>{moment(event.occurred_at)}</td>
        <th scope="row">{label(event.rule_key)} v{event.version}</th>
        <td>{EVENT_LABEL[event.event_type]}</td>
        <td>{event.summary}</td>
      </tr>)}</tbody>
    </table></div>}
    <details className="workspace-disclosure"><summary>How the engine decides</summary><p>Each night after the close the engine runs every frozen rule version over newly imported sessions from its start date, which is after it was fixed. Backtests never count. A version is promoted only with at least 20 shadow trades over at least 90 days, a positive average after charges, no trade worse than ₹20,000, a drawdown within ₹60,000, and a t value of at least {engine.t_bar.toFixed(2)}: the bar for one chance in twenty across all {engine.trials_tested.toLocaleString("en-IN")} rules tried on this history. A newer version must also beat the current one over the same sessions.</p><p>A promoted version is demoted on 5 losses in a row, a trade or drawdown past those limits, or results well below its shadow record. If a newer version is demoted, the engine rolls back to the one it replaced. Promotion only clears a rule for a separate paper account; the engine places no orders and changes no paper position. Simulated results say nothing certain about future results and are not investment advice.</p></details>
  </section>;
}
