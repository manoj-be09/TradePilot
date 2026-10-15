"use client";

import { useEffect, useRef, useState } from "react";

export function ExpiryTestControl({ token, apiUrl, expiry, strike, disabled, onApplied }: {
  token: string; apiUrl: string; expiry: string; strike: string; disabled: boolean;
  onApplied: () => void;
}) {
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [supported, setSupported] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    setSupported(false);
    void fetch(`${apiUrl}/paper/expiry-test-policy`, {headers: {Authorization: `Bearer ${token}`}, signal: controller.signal})
      .then(async response => {
        if (!response.ok) return;
        const result = await response.json();
        if (!controller.signal.aborted) setSupported(result.version === "nifty-option-expiry-test-v1");
      }).catch(() => {});
    return () => controller.abort();
  }, [apiUrl, token]);
  const command = useRef<{ body: string; key: string } | null>(null);
  async function apply() {
    if (busy || disabled || !consent || !supported) return;
    const body = JSON.stringify({ strategy_key: "long_call", expiry,
      reference_strike: strike, lots: 1, expiry_test_only: true });
    if (command.current?.body !== body) command.current = {body, key: crypto.randomUUID()};
    setBusy(true); setMessage("");
    try {
      const response = await fetch(`${apiUrl}/paper/trades`, { method: "POST", body,
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json",
          "Idempotency-Key": command.current.key } });
      const result = await response.json();
      if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "Expiry test entry rejected");
      command.current = null; setConsent(false);
      setMessage("Expiry test opened. It holds through expiry; review official settlement after expiry. Manual close remains available.");
      onApplied();
    } catch (cause) { setMessage(cause instanceof Error ? cause.message : "Entry status unknown; refresh positions before retrying."); }
    finally { setBusy(false); }
  }
  return <details className="equity-context-details"><summary>TEST ONLY · NIFTY expiry settlement</summary>
    <p>One long call lot at {strike}, expiry {expiry}. Maximum premium loss ₹10,000; at most one open expiry test. Uses the current server quote policy and all normal session, quote, cash and risk checks.</p>
    <p>This test has no automatic P&amp;L stop, target, trailing stop or expiry square-off. You can lose the full premium. Manual close remains available before expiry. Settlement needs official evidence and your review after expiry. Test outcomes do not qualify for strategy feedback.</p>
    <label><input type="checkbox" checked={consent} disabled={busy} onChange={event => setConsent(event.target.checked)} /> I want a paper-only expiry test with these limits.</label>
    {!supported && <p role="status">Expiry-test entry is unavailable until the supporting API and worker are deployed.</p>}
    <button className="primary-button" type="button" disabled={!supported || disabled || busy || !consent || !expiry || !strike} onClick={() => void apply()}>{busy ? "Submitting…" : "Open one expiry-test call lot"}</button>
    {message && <p role="status">{message}</p>}
  </details>;
}
