"use client";

import { useEffect, useState } from "react";

type Stage = "UNDERPOWERED" | "REJECTED" | "SHADOW" | "PROBATION" | "APPROVED" | "SUPERSEDED" | "DEMOTED" | "RETIRED" | "BASELINE";
type Check = {
  trades?: number;
  average?: number | null;
  evidence?: number;
  bar?: number;
  futility?: number;
  paper_trades?: number;
  needs?: string[];
  paper_vs_shadow?: { sessions: number; paper_average: number; shadow_average: number | null };
};
type HypothesisRow = {
  key: string;
  family: string;
  origin: "HUMAN" | "GRID" | "LLM";
  stage: Stage;
  override: boolean;
  forward_from: string;
  alpha: number | null;
  check: Check;
};
type LearningEvent = { key: string; event_type: string; reason: string; summary: string; occurred_at: string };
type Learning = {
  budget: { hypotheses_tested: number; remaining_alpha: number; next_slice: number };
  cost_model: { version: number; made: string; slippage_per_trade: number; pairs: number } | null;
  hypotheses: HypothesisRow[];
  events: LearningEvent[];
};

const label = (key: string) => key.replaceAll("_", " ");
const rupees = (value: number) => `₹${Math.round(value).toLocaleString("en-IN")}`;
const moment = (value: string) => new Date(value).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", dateStyle: "medium", timeStyle: "short" });
const STAGE_LABEL: Record<Stage, string> = {
  UNDERPOWERED: "Refused: could not prove itself in time",
  REJECTED: "Refused by the backtest screen",
  SHADOW: "Forward shadow",
  PROBATION: "Paper probation (1 lot)",
  APPROVED: "Approved for paper",
  SUPERSEDED: "Replaced by a better rule",
  DEMOTED: "Demoted",
  RETIRED: "Retired",
  BASELINE: "Baseline for comparison",
};
const ORIGIN_LABEL: Record<HypothesisRow["origin"], string> = { HUMAN: "Owner", GRID: "Grid", LLM: "Claude" };

/** Research page: the v2 learning loop's hypotheses, its error budget and every decision. */
export function LearningLoop({ token, apiUrl }: { token: string; apiUrl: string }) {
  const [learning, setLearning] = useState<Learning | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    fetch(`${apiUrl}/research/learning`, {
      headers: { Authorization: `Bearer ${token}` }, cache: "no-store", signal: controller.signal,
    }).then(async (response) => {
      if (!response.ok) throw new Error(String(response.status));
      const body = await response.json() as Learning;
      if (!controller.signal.aborted) { setLearning(body); setFailed(false); }
    }).catch(() => { if (!controller.signal.aborted) setFailed(true); });
    return () => controller.abort();
  }, [apiUrl, token]);

  if (failed) return <p className="error-banner" role="alert">The learning loop is unavailable.</p>;
  if (learning === null) return null;
  const { budget, cost_model: cost } = learning;
  return <section className="observed-performance" aria-label="Learning loop">
    <div className="trading-dashboard-activity-heading"><div><h3>Learning loop</h3><p>Pre-registered ideas judged only on forward sessions · simulated</p></div><span className="dashboard-status">Evidence-based · not a forecast</span></div>
    <p>{budget.hypotheses_tested.toLocaleString("en-IN")} idea(s) tested so far; {(budget.remaining_alpha * 100).toFixed(3)}% of the 5% error budget is left, and the next idea gets {(budget.next_slice * 100).toFixed(4)}%.{cost ? ` Cost model v${cost.version} (${cost.made}): ${rupees(cost.slippage_per_trade)} a trade from ${cost.pairs} paper fills.` : " No cost calibration from paper fills yet."}</p>
    {learning.hypotheses.length === 0 ? <p className="trading-dashboard-empty">No hypotheses yet; the loop starts once the hypothesis file exists.</p> : <div className="observed-performance-scroll" role="region" aria-label="Learning hypotheses table; scroll horizontally for all columns" tabIndex={0}><table>
      <thead><tr><th scope="col">Idea</th><th scope="col">Stage</th><th scope="col">Shadow trades</th><th scope="col">Average after charges</th><th scope="col">Evidence / bar</th><th scope="col">Paper vs shadow</th><th scope="col">Still needed</th></tr></thead>
      <tbody>{learning.hypotheses.map((row) => <tr key={row.key}>
        <th scope="row"><strong>{label(row.key)}</strong><small>{ORIGIN_LABEL[row.origin]}{row.override ? " · owner override" : ""} · from {row.forward_from}</small></th>
        <td>{STAGE_LABEL[row.stage]}</td>
        <td>{row.check.trades ?? 0}</td>
        <td>{row.check.trades && row.check.average != null ? rupees(row.check.average) : "None yet"}</td>
        <td>{row.check.evidence == null ? "n/a" : `${row.check.evidence.toFixed(1)} / ${row.check.bar == null ? "n/a" : Math.round(row.check.bar).toLocaleString("en-IN")}`}</td>
        <td>{row.check.paper_vs_shadow ? `${rupees(row.check.paper_vs_shadow.paper_average)} vs ${row.check.paper_vs_shadow.shadow_average == null ? "n/a" : rupees(row.check.paper_vs_shadow.shadow_average)} over ${row.check.paper_vs_shadow.sessions}` : "No paper trades"}</td>
        <td>{(row.check.needs ?? []).join("; ") || (row.stage === "SHADOW" || row.stage === "PROBATION" ? "Ready" : "Nothing")}</td>
      </tr>)}</tbody>
    </table></div>}
    <h4>Loop decisions</h4>
    {learning.events.length === 0 ? <p className="trading-dashboard-empty">None recorded yet.</p> : <div className="observed-performance-scroll" role="region" aria-label="Learning loop decisions" tabIndex={0}><table>
      <thead><tr><th scope="col">When (IST)</th><th scope="col">Idea</th><th scope="col">Decision</th><th scope="col">Why</th></tr></thead>
      <tbody>{learning.events.map((event, index) => <tr key={`${event.key}-${event.occurred_at}-${index}`}>
        <td>{moment(event.occurred_at)}</td>
        <th scope="row">{label(event.key)}</th>
        <td>{label(event.event_type.toLowerCase())}</td>
        <td>{event.summary}</td>
      </tr>)}</tbody>
    </table></div>}
    <details className="workspace-disclosure"><summary>How the loop decides</summary><p>Every idea is written down with its expected effect before it is tested. Machine ideas first meet a backtest screen that can only reject them. An idea that could not show its effect within about six months is refused. The rest run in forward shadow from the session after they were frozen, each with its own slice of one 5% error budget, so testing more ideas never makes a lucky promotion more likely. The evidence measure is safe to check every night.</p><p>An idea moves to paper probation (one lot) when its evidence reaches its bar after at least 20 trades and it beats its baseline. It is approved after 10 paper trades that roughly match shadow, and only if it beats the current rule over the same sessions. Clear evidence of losing, a loss limit, or 5 losses in a row demotes or retires it. Simulated results say nothing certain about future results and are not investment advice.</p></details>
  </section>;
}
