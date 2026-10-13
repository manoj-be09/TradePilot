"use client";

import { useEffect, useRef, useState } from "react";
import type { ChronologicalValidation } from "./EquityStrategies";

export function StudySuccessorControl({ study, apiUrl, token, onUpdated }: {
  study: ChronologicalValidation; apiUrl: string; token: string;
  onUpdated: (report: ChronologicalValidation) => void;
}) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const request = useRef<AbortController | null>(null);

  useEffect(() => {
    setReason(""); setBusy(false); setError(""); setNotice("");
    return () => request.current?.abort();
  }, [study.study_id, token, apiUrl]);

  async function review() {
    if (busy || !study.study_id || reason.trim().length < 8) return;
    const controller = new AbortController();
    request.current = controller;
    setBusy(true); setError(""); setNotice("");
    try {
      const response = await fetch(`${apiUrl}/equity/validation-studies/${study.study_id}/successor`, {
        method: "POST", headers: {
          Authorization: `Bearer ${token}`, "Content-Type": "application/json",
        }, body: JSON.stringify({ reason: reason.trim() }), signal: controller.signal,
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(response.status === 404
          ? "Study or successor endpoint unavailable. Check the API update."
          : typeof data.detail === "string" ? data.detail : "Source review could not be completed.");
      }
      const result = await response.json() as ChronologicalValidation;
      if (controller.signal.aborted) return;
      setBusy(false);
      if (result.frozen) onUpdated(result);
      else setNotice(result.reason || "Insufficient earlier-window evidence. Original study stays selected.");
    } catch (cause) {
      if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "Review unavailable.");
    } finally {
      if (!controller.signal.aborted && request.current === controller) setBusy(false);
    }
  }

  return <details className="equity-context-details">
    <summary>Review updated calculation code · generation {study.generation ?? 1}</summary>
    <p className="muted">Reuses this study&apos;s saved candles, costs and reserved window. The original stays unchanged. Requires a calculation source update.</p>
    <label>Reason for this review<input value={reason} maxLength={200} onChange={event => setReason(event.target.value)} placeholder="Describe the reviewed source update" /></label>
    <button type="button" disabled={busy || reason.trim().length < 8} onClick={() => void review()}>{busy ? "Reviewing…" : "Review successor"}</button>
    {error && <p role="alert" className="error-banner">{error}</p>}
    {notice && <p role="status" className="muted">{notice}</p>}
  </details>;
}
