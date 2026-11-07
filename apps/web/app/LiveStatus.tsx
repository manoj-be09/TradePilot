"use client";

import { useEffect, useState } from "react";

type LiveStatusData = { live_enabled: boolean; halted: boolean; active_releases: number };

// Halting is always safe, so the dashboard can halt; releasing stays on the command line.
export function LiveStatus({ token, apiUrl }: { token: string; apiUrl: string }) {
  const [status, setStatus] = useState<LiveStatusData | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function load(signal?: AbortSignal) {
    try {
      const response = await fetch(`${apiUrl}/live/status`, { headers: { Authorization: `Bearer ${token}` }, signal });
      if (response.ok) setStatus(await response.json() as LiveStatusData);
    } catch { /* keep the last known state */ }
  }

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [token, apiUrl]); // eslint-disable-line react-hooks/exhaustive-deps

  async function halt() {
    setBusy(true); setError("");
    try {
      const response = await fetch(`${apiUrl}/live/halt`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify({ reason: "Halted from the dashboard" }),
      });
      if (!response.ok) throw new Error();
      setStatus(await response.json() as LiveStatusData);
    } catch {
      setError("Could not halt live trading");
    } finally {
      setBusy(false);
    }
  }

  if (!status) return <span className="status-chip">Live: unknown</span>;
  const warn = status.live_enabled && !status.halted;
  return (
    <span className={`status-chip ${warn ? "status-chip--warn" : "status-chip--ok"}`} title={error || undefined}>
      Live: {status.live_enabled ? "on" : "off"}{status.halted ? " · halted" : ""}
      {!status.halted ? <button className="status-chip-action" disabled={busy} onClick={() => void halt()} type="button">Halt</button> : null}
    </span>
  );
}
