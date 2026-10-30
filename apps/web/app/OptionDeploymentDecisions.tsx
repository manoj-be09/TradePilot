"use client";

import { useState } from "react";

type ConditionResult = {
  metric: string;
  operator: string;
  threshold: string;
  actual: string | null;
  passed: boolean | null;
};
type Snapshot = {
  decision: "RULES_PASS" | "RULES_NOT_MET" | "QUALITY_FAIL";
  market_as_of: string;
  provider: string;
  quality: { status: string; issues?: Array<{ code: string; message: string }> };
  entry_rule_snapshot?: { results: ConditionResult[] };
  futures_quote?: { bid: string | null; ask: string | null; timestamp_basis: string };
  leg_quotes?: {
    status: string;
    legs: Array<{
      option_type: string;
      strike: string;
      side: string;
      bid: string | null;
      ask: string | null;
      selected_side_quote: string | null;
      quote_timestamp: string | null;
      book_as_of?: string | null;
      book_timestamp_basis?: string | null;
    }>;
  };
};
type Decision = {
  id: string;
  event_type: string;
  created_at: string;
  details: { snapshot?: Snapshot; reason?: string; trade_id?: string };
};

const label: Record<string, string> = {
  "paper.deployment.armed": "Armed for paper entry",
  "paper.deployment.filled": "Paper entry filled",
  "paper.deployment.cancelled": "Waiting strategy cancelled",
  "paper.deployment.expired": "Waiting strategy expired",
  "paper.deployment.rejected": "Waiting strategy rejected",
  "paper.deployment.entry_blocked": "Paper entry blocked",
  "paper.deployment.contract_rolled": "Waiting strategy moved to the next futures contract",
};

function localTime(value: string) {
  return new Date(value).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });
}

export function OptionDeploymentDecisions({
  deploymentId, apiUrl, token,
}: { deploymentId: string; apiUrl: string; token: string | null }) {
  const [rows, setRows] = useState<Decision[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function load() {
    if (!token) return;
    setLoading(true); setError("");
    try {
      const response = await fetch(`${apiUrl}/paper/deployments/${deploymentId}/decisions`, {
        headers: { Authorization: `Bearer ${token}` }, cache: "no-store",
      });
      if (!response.ok) throw new Error("Paper decision history is unavailable");
      setRows(await response.json() as Decision[]);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Paper decision history is unavailable");
    } finally { setLoading(false); }
  }

  return <details onToggle={event => {
    if (event.currentTarget.open && rows === null && !loading) void load();
  }}>
    <summary>Entry check history</summary>
    <p className="muted">Provider checks and paper entry state · latest 200 recorded decisions</p>
    <button className="inline-button" type="button" disabled={loading} onClick={() => void load()}>
      {loading ? "Loading…" : "Refresh decisions"}
    </button>
    {error && <p className="error-banner" role="alert">{error}</p>}
    {rows?.length === 0 && <p className="muted">No automated market check has been recorded yet.</p>}
    {rows && rows.length > 0 && !rows.some(row => row.event_type === "paper.deployment.entry_checked" || row.event_type === "paper.deployment.entry_blocked") && <p className="muted">No worker entry evaluation has been recorded yet. Check the worker status and market session.</p>}
    {rows?.find(row => row.event_type === "paper.deployment.entry_blocked")?.details.reason && <p className="paper-warning">Last recorded blocker: {rows.find(row => row.event_type === "paper.deployment.entry_blocked")?.details.reason}</p>}
    {rows && rows.length > 0 && <ul>{rows.map(row => {
      const snapshot = row.details.snapshot;
      return <li key={row.id}>
        <strong>{snapshot ? snapshot.decision === "RULES_PASS" ? "Entry rules matched; final checks pending" : snapshot.decision === "QUALITY_FAIL" ? "Market data failed quality check" : "Waiting for entry conditions" : label[row.event_type] ?? row.event_type}</strong>
        {` · ${localTime(row.created_at)}`}
        {snapshot && <details>
          <summary>Checked {localTime(snapshot.market_as_of)} · {snapshot.provider} · quality {snapshot.quality.status}</summary>
          {snapshot.entry_rule_snapshot?.results?.length ? <ul>{snapshot.entry_rule_snapshot.results.map((result, index) => <li key={`${result.metric}-${index}`}>
            {result.metric.replaceAll("_", " ")} {result.operator} {result.threshold}: {result.actual ?? "unavailable"} · {result.passed === true ? "passed" : result.passed === false ? "not met" : "unknown"}
          </li>)}</ul> : <p>Entry conditions were not evaluated because the chain failed quality checks.</p>}
          {snapshot.quality.issues?.length ? <p className="paper-warning">Data checks: {snapshot.quality.issues.map(issue => issue.message).join(" · ")}</p> : null}
          {snapshot.leg_quotes?.legs.length ? <><p>Selected-leg quotes ({snapshot.leg_quotes.status.toLowerCase()}); observations only, not verified paper fills:</p><ul>{snapshot.leg_quotes.legs.map((leg, index) => <li key={`${leg.option_type}-${leg.strike}-${index}`}>
            {leg.side} {leg.option_type} {leg.strike} · bid {leg.bid ?? "unavailable"} / ask {leg.ask ?? "unavailable"} · selected side {leg.selected_side_quote ?? "unavailable"}{leg.quote_timestamp ? ` · quote ${localTime(leg.quote_timestamp)}` : ""} · book {leg.book_timestamp_basis === "exchange" && leg.book_as_of ? localTime(leg.book_as_of) : "exchange time unverified"}
          </li>)}</ul></> : snapshot.futures_quote ? <p>Futures quote · bid {snapshot.futures_quote.bid ?? "unavailable"} / ask {snapshot.futures_quote.ask ?? "unavailable"} · {snapshot.futures_quote.timestamp_basis === "exchange" ? "exchange time" : "exchange time unverified"}; an observation, not a verified paper fill.</p> : <p>No selected-leg quote was available for this check.</p>}
        </details>}
        {row.details.reason && <span> · {row.details.reason}</span>}
      </li>;
    })}</ul>}
  </details>;
}
