"use client";

import { useState } from "react";

export type QuoteObservation = {
  checked_at: string; quote_at: string; timestamp_basis: string;
  trade_at?: string | null; book_at?: string | null; book_basis: string;
  last: string; bid?: string | null; ask?: string | null;
  bid_size?: number | null; ask_size?: number | null; entry_gate_ok: boolean;
};
const time = (value?: string | null) => value ? new Date(value).toLocaleString("en-IN") : "Not recorded";

export function QuoteLog({ samples = [] }: { samples?: QuoteObservation[] }) {
  return <details><summary>Quote observations · {samples.length} saved</summary>
    <p className="muted">Latest 10 of up to 100 worker samples. These are not every market tick. A passed entry quote gate does not mean the strategy signal matched.</p>
    {samples.length === 0 ? <p>No quote samples recorded.</p> : <ul>{samples.slice(-10).reverse().map((sample, index) => <li key={`${sample.checked_at}-${index}`}>
      <details><summary>{time(sample.checked_at)} · ₹{sample.last} · {sample.entry_gate_ok ? "Entry quote gate passed" : "Entry quote gate blocked"}</summary>
        <p>Bid ₹{sample.bid ?? "—"} × {sample.bid_size ?? "—"} · Ask ₹{sample.ask ?? "—"} × {sample.ask_size ?? "—"}</p>
        <p>Worker check: {time(sample.checked_at)}<br />Quote: {time(sample.quote_at)} · {sample.timestamp_basis}<br />Last trade: {time(sample.trade_at)}<br />Book: {time(sample.book_at)} · {sample.book_basis}</p>
      </details>
    </li>)}</ul>}
  </details>;
}

type Reconciliation = {
  status: string; recorded_cash: string; expected_cash: string; cash_difference: string;
  issue_count: number; issues: string[]; audit_coverage: string; audit_issue_count: number;
  cash_journal: { status: string; entry_count: number; issue_count: number; issues: string[] };
};
export function EquityWalletCheck({ apiUrl, token }: { apiUrl: string; token: string }) {
  const [result, setResult] = useState<Reconciliation | null>(null);
  const [checkedAt, setCheckedAt] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function check() {
    setBusy(true); setError(""); setResult(null); setCheckedAt(null);
    try {
      const response = await fetch(`${apiUrl}/equity/account/ledger-reconciliation`, {
        headers: { Authorization: `Bearer ${token}` }, cache: "no-store",
      });
      if (!response.ok) throw new Error(`Balance check unavailable (${response.status}).`);
      setResult(await response.json() as Reconciliation); setCheckedAt(new Date().toISOString());
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Balance check unavailable."); }
    finally { setBusy(false); }
  }
  return <details className="equity-method"><summary>Wallet & cash journal</summary>
    <button type="button" disabled={busy} onClick={() => void check()}>{busy ? "Checking…" : "Check balances"}</button>
    {error && <p role="alert" className="error-banner">{error}</p>}
    {result && <><p>Wallet: <strong>{result.status}</strong> · Cash journal: <strong>{result.cash_journal.status}</strong> · {result.cash_journal.entry_count} cash records</p>
      <p>Recorded ₹{result.recorded_cash} · Expected ₹{result.expected_cash} · Difference ₹{result.cash_difference}<br />Audit coverage: {result.audit_coverage} · {result.audit_issue_count} audit issues<br />Checked {time(checkedAt)}; this is a snapshot.</p>
      {[...result.issues, ...result.cash_journal.issues].map((issue, index) => <p role="alert" key={index}>{issue}</p>)}
    </>}
    <p className="muted">Read-only reconciliation. This does not change cash or trades. Continuous feed connection logs are not included in this view.</p>
  </details>;
}
