"use client";

import { useEffect, useState } from "react";

type Attempt = {
  id: string; created_at: string; symbol: string; status: string;
  reason: string | null; frozen: boolean; requested_allocation: string;
  strategies_reviewed: number; reviewable_strategies: number;
};

export function ValidationAttemptHistory({ apiUrl, token, refresh }: {
  apiUrl: string; token: string; refresh: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [attempts, setAttempts] = useState<Attempt[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    setAttempts([]); setError(""); setBusy(false);
    if (!open) return;
    const controller = new AbortController();
    setBusy(true);
    fetch(`${apiUrl}/equity/validation-attempts`, {
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store", signal: controller.signal,
    }).then(async response => {
      if (!response.ok) throw new Error("Research history could not be loaded.");
      return await response.json() as Attempt[];
    }).then(rows => {
      if (!controller.signal.aborted) setAttempts(rows);
    }).catch(() => {
      if (!controller.signal.aborted) setError("Research history could not be loaded.");
    }).finally(() => {
      if (!controller.signal.aborted) setBusy(false);
    });
    return () => controller.abort();
  }, [apiUrl, token, open, refresh, reload]);

  return <details className="equity-context-details" onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>Research attempt history</summary>
    <p className="muted">Latest 50 requests across your watchlist, including insufficient results and data failures. Reopening a frozen study is another request, not a new experiment.</p>
    <button type="button" disabled={busy} onClick={() => setReload(value => value + 1)}>Refresh history</button>
    {busy && <p role="status">Loading history…</p>}
    {error && <p role="alert">{error}</p>}
    {!busy && !error && attempts.length === 0 && <p>No recorded attempts yet.</p>}
    {attempts.map(attempt => <div className="equity-shadow-card" key={attempt.id}>
      <strong>{attempt.symbol} · {attempt.frozen ? "Frozen study returned" : attempt.status === "unavailable" ? "Data unavailable" : "Insufficient evidence"}</strong>
      <small>{new Date(attempt.created_at).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" })} · requested allocation ₹{Number(attempt.requested_allocation).toLocaleString("en-IN")}</small>
      <span>{attempt.reason ?? `${attempt.reviewable_strategies}/${attempt.strategies_reviewed} rules have sufficient earlier-window trades`}</span>
    </div>)}
  </details>;
}
