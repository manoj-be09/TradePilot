"use client";

import { useEffect, useState } from "react";

type WorkerHealth = {
  status: string;
  heartbeat_at: string | null;
  last_success_at?: string | null;
  detail?: string;
  unverified_quotes_allowed?: boolean;
};

function formatUpdate(value: string | null | undefined): string {
  if (!value) return "never";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "unknown" : date.toLocaleString("en-IN", {
    dateStyle: "medium", timeStyle: "short",
  });
}

export function WorkerStatus({ token, apiUrl }: { token: string; apiUrl: string }) {
  const [health, setHealth] = useState<WorkerHealth | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const response = await fetch(`${apiUrl}/paper/worker`, {
          headers: { Authorization: `Bearer ${token}` }, signal: controller.signal,
        });
        if (!response.ok) throw new Error("Worker status unavailable");
        const result = await response.json() as WorkerHealth;
        if (!controller.signal.aborted) { setHealth(result); setUnavailable(false); }
      } catch {
        if (!controller.signal.aborted) setUnavailable(true);
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(poll, 5_000);
      }
    }
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [apiUrl, token]);
  const status = unavailable ? "UNAVAILABLE" : health?.status || "CHECKING";
  const healthy = status === "HEALTHY" && !!health?.last_success_at;
  const degraded = status === "DEGRADED";
  const message = healthy
    ? "The server is checking paper exits independently of this browser."
    : status === "RUNNING" || status === "CHECKING"
      ? "Waiting for a completed exit check. Automatic exits are not confirmed yet."
      : status === "NOT_STARTED"
        ? "The independent paper exit worker has not started. Start it before relying on automatic exits."
        : status === "DEGRADED"
          ? "The last exit check reported warnings. Review the worker and open positions."
          : "Exit monitoring is not confirmed healthy. Check the worker before relying on automatic exits.";
  return <section className={`worker-status ${healthy ? "worker-status--healthy" : "worker-status--warning"}`} aria-label="Paper exit monitoring" aria-live="polite">
    <div className="worker-status-heading"><strong>Paper exits: {status.toLowerCase().replaceAll("_", " ")}</strong><span>{healthy ? "Monitoring active" : degraded ? "Action may be needed" : "Checking"}</span></div>
    {!healthy ? <p>{message}</p> : null}
    <details className="worker-status-details">
      <summary>Monitoring details</summary>
      {!unavailable && health?.detail && <small className="worker-detail">{health.detail}</small>}
      {!unavailable && health?.heartbeat_at && <small>Last worker update: {formatUpdate(health.heartbeat_at)}</small>}
      {!unavailable && health?.last_success_at && <small>Last successful check: {formatUpdate(health.last_success_at)}</small>}
      {!unavailable && health?.unverified_quotes_allowed && <p>Options educational simulation allows unverified exchange timestamps. Strict equity mode still requires an exchange-timed book; explicitly opted-in indicative equity fills are labeled and excluded from verified strategy feedback.</p>}
    </details>
  </section>;
}
