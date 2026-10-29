"use client";

import { useCallback, useEffect, useState } from "react";

type StrategyDemotion = {
  strategy_key: string;
  paused: boolean;
  reason: string | null;
  summary: string | null;
  demoted_at: string | null;
};

const label = (key: string) => key.replaceAll("_", " ");

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
    <div className="worker-status-heading"><strong>Paused strategies</strong><span>New paper entries blocked</span></div>
    <p>Automatic demotion paused these strategies. Open positions keep their exits. Reinstating counts only trades that close afterwards.</p>
    <ul>
      {paused.map((row) => <li key={row.strategy_key}>
        <strong>{label(row.strategy_key)}</strong>: paused after {row.summary ?? row.reason}.{" "}
        <button className="refresh-button" type="button" disabled={busy === row.strategy_key}
          onClick={() => void reinstate(row.strategy_key)}>
          {busy === row.strategy_key ? "Reinstating…" : "Reinstate"}
        </button>
      </li>)}
    </ul>
    {error && <p role="alert">{error}</p>}
  </section>;
}
