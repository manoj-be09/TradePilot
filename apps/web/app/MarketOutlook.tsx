"use client";

import { useEffect, useState } from "react";

type Outlook = {
  status: "ready" | "unavailable";
  provider: string;
  benchmark: string;
  horizon: string;
  reason: string | null;
  session_date: string | null;
  observations: number;
  last_close: string | null;
  previous_close: string | null;
  last_session_change_pct: string | null;
  twenty_session_change_pct: string | null;
  recent_direction: "Rising" | "Falling" | "Mixed" | null;
  realized_volatility_20d_pct: string | null;
  relative_volatility_score: number | null;
  relative_volatility_label: string | null;
  reasons: string[];
};

const pct = (value: string | null, signed = false) => value === null ? "—" :
  `${signed && Number(value) > 0 ? "+" : ""}${Number(value).toFixed(2)}%`;
const price = (value: string | null) => value === null ? "—" :
  new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 }).format(Number(value));
const volatilityTone: Record<string, string> = {
  Low: "low",
  Typical: "typical",
  Elevated: "elevated",
  "Very elevated": "very-elevated",
};
const directionTone: Record<string, string> = {
  Rising: "rising",
  Falling: "falling",
  Mixed: "mixed",
};

export function MarketOutlook({ token, apiUrl }: { token: string; apiUrl: string }) {
  const [outlook, setOutlook] = useState<Outlook | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(true);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setBusy(true); setError("");
    fetch(`${apiUrl}/analytics/market-outlook`, {
      headers: { Authorization: `Bearer ${token}` }, cache: "no-store", signal: controller.signal,
    }).then(async response => {
      if (!response.ok) throw new Error(`Market conditions could not load (HTTP ${response.status}).`);
      return response.json() as Promise<Outlook>;
    }).then(result => { if (!controller.signal.aborted) setOutlook(result); })
      .catch(cause => { if (!controller.signal.aborted) { setOutlook(null); setError(cause instanceof Error ? cause.message : "Market conditions are unavailable."); } })
      .finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [apiUrl, token, refresh]);

  const ready = !busy && !error && outlook?.status === "ready";
  return <section aria-label="NIFTY 50 market conditions" className="market-outlook">
    <div className="market-outlook-heading"><div><div className="panel-kicker">NIFTY 50 · COMPLETED SESSIONS</div><h3>Market conditions</h3></div>
      <button className="refresh-button" disabled={busy} onClick={() => setRefresh(value => value + 1)} type="button">{busy ? "Checking…" : "Refresh"}</button>
    </div>
    <p className="market-outlook-context">Observed history only · No next-session prediction or trading signal.</p>
    {busy ? <p role="status">Checking daily history and the NSE session calendar…</p> :
      error || outlook?.status === "unavailable" ? <p className="market-outlook-unavailable" role="status">Market conditions unavailable: {error || outlook?.reason || "Unknown data issue."}</p> :
      ready && outlook ? <>
        <div className="market-outlook-grid">
          <article className={`market-outlook-tile market-outlook-tile--${volatilityTone[outlook.relative_volatility_label || ""] || "neutral"}`}><span>RECENT VOLATILITY</span><strong>{outlook.relative_volatility_label} · {outlook.relative_volatility_score}/100</strong>
            <p>Measured 20-session volatility: {pct(outlook.realized_volatility_20d_pct)} annualized.</p>
            <small>The score ranks this reading against up to 100 earlier windows; it is not a probability or predicted price move.</small></article>
          <article className={`market-outlook-tile market-outlook-tile--${directionTone[outlook.recent_direction || ""] || "neutral"}`}><span>RECENT DIRECTION</span><strong>{outlook.recent_direction}</strong>
            <p>Last session {pct(outlook.last_session_change_pct, true)} · Past 20 sessions {pct(outlook.twenty_session_change_pct, true)}.</p>
            <small>A backward-looking trend label, not a bullish or bearish forecast.</small></article>
        </div>
        <p className="market-outlook-source">NIFTY 50 close {price(outlook.last_close)} (previous {price(outlook.previous_close)}) · Completed session {outlook.session_date} · {outlook.observations} daily bars · Source {outlook.provider}.</p>
        <details><summary>How these readings are calculated</summary><ul>{outlook.reasons.map(reason => <li key={reason}>{reason}</li>)}</ul>
          <p>Data are checked against the provider's NSE session calendar. Missing or invalid history suppresses both readings. They do not alter paper orders, strategy rankings, or stops.</p></details>
      </> : null}
  </section>;
}
