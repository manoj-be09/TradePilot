"use client";

import { useState } from "react";

type ExitSnapshot = {
  provider: string;
  market_as_of: string | null;
  quality_status: string | null;
  unrealized_pnl: string | null;
  peak_unrealized_pnl?: string;
  stop_loss_amount?: string | null;
  target_profit_amount?: string | null;
  trailing_stop_amount?: string | null;
  decision: string;
  reason?: string;
  leg_quotes: Array<{
    side: string;
    option_type: string;
    strike: string;
    bid: string | null;
    ask: string | null;
    selected_side_quote: string | null;
    quote_timestamp: string | null;
    book_as_of?: string | null;
    book_timestamp_basis?: string | null;
  }>;
};

type ExitObservation = {
  id: string;
  created_at: string;
  details: { snapshot: ExitSnapshot };
};

type ExitReconciliation = {
  status: "MATCHED" | "INCOMPLETE" | "MISMATCH";
  evaluated_count: number;
  skipped_count: number;
  detail: string;
};

function localTime(value: string) {
  return new Date(value).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });
}

export function OptionTradeObservations({
  tradeId, apiUrl, token,
}: { tradeId: string; apiUrl: string; token: string | null }) {
  const [rows, setRows] = useState<ExitObservation[] | null>(null);
  const [reconciliation, setReconciliation] = useState<ExitReconciliation | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function load() {
    if (!token) return;
    setLoading(true);
    setError("");
    setRows(null);
    setReconciliation(null);
    try {
      const [checks, replay] = await Promise.all([
        fetch(`${apiUrl}/paper/trades/${tradeId}/observations`, {
          headers: { Authorization: `Bearer ${token}` }, cache: "no-store",
        }),
        fetch(`${apiUrl}/paper/trades/${tradeId}/exit-reconciliation`, {
          headers: { Authorization: `Bearer ${token}` }, cache: "no-store",
        }),
      ]);
      if (!checks.ok || !replay.ok) throw new Error("Exit observations are unavailable");
      setRows(await checks.json() as ExitObservation[]);
      setReconciliation(await replay.json() as ExitReconciliation);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Exit observations are unavailable");
    } finally {
      setLoading(false);
    }
  }

  return <details onToggle={event => {
    if (event.currentTarget.open && rows === null && !loading) void load();
  }}>
    <summary>Exit checks</summary>
    <p className="muted">Latest 200 paper exit observations, including skipped checks</p>
    <button className="inline-button" type="button" disabled={loading} onClick={() => void load()}>
      {loading ? "Loading…" : "Refresh checks"}
    </button>
    {error && <p className="error-banner" role="alert">{error}</p>}
    {reconciliation && <p className="muted" aria-live="polite">
      Observed exit replay: <strong>{reconciliation.status.toLowerCase()}</strong> · {reconciliation.detail}
      {` (${reconciliation.evaluated_count} evaluated, ${reconciliation.skipped_count} skipped)`}
    </p>}
    {rows?.length === 0 && <p className="muted">No exit check has been recorded for this position.</p>}
    {rows && rows.length > 0 && <ul>{rows.map(row => {
      const snapshot = row.details.snapshot;
      return <li key={row.id}>
        <strong>{snapshot.decision === "HOLD" ? "Position held" : snapshot.decision === "SKIPPED" ? "Exit check skipped" : snapshot.decision.replaceAll("_", " ")}</strong>
        {` · ${localTime(row.created_at)}`}
        {snapshot.decision === "SKIPPED" ? ` · ${snapshot.reason ?? "Reason unavailable"}` : ` · paper P&L ₹${snapshot.unrealized_pnl}`}
        <details>
          <summary>{snapshot.market_as_of ? `Observed ${localTime(snapshot.market_as_of)}` : "No eligible market snapshot"} · {snapshot.provider} · quality {snapshot.quality_status ?? "unavailable"}</summary>
          {snapshot.decision !== "SKIPPED" && <p>Peak ₹{snapshot.peak_unrealized_pnl} · stop ₹{snapshot.stop_loss_amount ?? "none"} · target ₹{snapshot.target_profit_amount ?? "none"} · trail ₹{snapshot.trailing_stop_amount ?? "none"}</p>}
          {snapshot.leg_quotes.length ? <ul>{snapshot.leg_quotes.map((leg, index) => <li key={`${leg.option_type}-${leg.strike}-${index}`}>
            {leg.side} {leg.option_type} {leg.strike} · bid {leg.bid ?? "unavailable"} / ask {leg.ask ?? "unavailable"} · selected side {leg.selected_side_quote ?? "unavailable"}{leg.quote_timestamp ? ` · quote ${localTime(leg.quote_timestamp)}` : ""} · book {leg.book_timestamp_basis === "exchange" && leg.book_as_of ? localTime(leg.book_as_of) : "exchange time unverified"}
          </li>)}</ul> : <p>No selected-leg quote was available for this check.</p>}
          <p className="muted">Observed quotes and simulated decisions, not broker fills.</p>
        </details>
      </li>;
    })}</ul>}
  </details>;
}
