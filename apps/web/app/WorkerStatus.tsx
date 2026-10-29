"use client";

import { useEffect, useState } from "react";

type WorkerHealth = {
  status: string;
  heartbeat_at: string | null;
  last_success_at?: string | null;
  detail?: string;
  unverified_quotes_allowed?: boolean;
  own_expired_open_positions?: number;
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
      ? "A shared worker cycle is in progress. Review each position for its latest exit check."
      : status === "NOT_STARTED"
        ? "The independent paper exit worker has not started. Start it before relying on automatic exits."
        : status === "DEGRADED"
          ? "The last check reported warnings. Review your positions’ latest checks; this does not mean every strategy has stopped."
          : "Exit monitoring is not confirmed healthy. Check the worker before relying on automatic exits.";
  const label = healthy ? "running" : status === "NOT_STARTED" ? "not started"
    : degraded ? "warnings" : status === "UNAVAILABLE" ? "unavailable" : "checking";
  return <details className={`status-chip status-chip-menu ${healthy ? "status-chip--ok" : "status-chip--warn"}`} aria-live="polite">
    <summary aria-label={`Paper exit worker: ${label}`}>Exit worker: {label}</summary>
    <div className="status-chip-panel">
      <p>{message}</p>
      {!unavailable && health?.detail && <small className="worker-detail">{health.detail}</small>}
      {!unavailable && health?.heartbeat_at && <small>Last worker update: {formatUpdate(health.heartbeat_at)}</small>}
      {!unavailable && health?.last_success_at && <small>Last fully healthy cycle: {formatUpdate(health.last_success_at)}</small>}
      {!unavailable && !!health?.own_expired_open_positions && <small>
        Your account has {health.own_expired_open_positions} expired open paper
        {" "}position{health.own_expired_open_positions === 1 ? "" : "s"} awaiting settlement review.
      </small>}
      {!unavailable && health?.unverified_quotes_allowed && <small>Option quotes without an exchange timestamp are allowed for this local paper simulation.</small>}
    </div>
  </details>;
}
