"use client";

import { useEffect, useState } from "react";
import { HistoricalPriceChart } from "./HistoricalPriceChart";

type Instrument = { instrument_key: string; trading_symbol: string; name: string | null; exchange: string; segment: string; instrument_type: string };
type Selected = { instrumentKey: string; symbol: string; providerId: string };

export function ChartWorkspace({ token, apiUrl }: { token: string; apiUrl: string }) {
  const [providerId, setProviderId] = useState("");
  const [nifty, setNifty] = useState<Selected | null>(null);
  const [selected, setSelected] = useState<Selected | null>(null);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Instrument[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    async function initialize() {
      try {
        const headers = { Authorization: `Bearer ${token}` };
        const capabilityResponse = await fetch(`${apiUrl}/market-data/capabilities`, { headers, cache: "no-store", signal: controller.signal });
        if (!capabilityResponse.ok) throw new Error("Market-data provider is unavailable.");
        const capabilities = await capabilityResponse.json() as { provider: string };
        if (!capabilities.provider) throw new Error("No market-data provider is active.");
        const params = new URLSearchParams({ query: "NIFTY 50", exchange: "NSE", segment: "INDEX", provider_id: capabilities.provider });
        const response = await fetch(`${apiUrl}/market-data/instrument-search?${params}`, { headers, cache: "no-store", signal: controller.signal });
        if (!response.ok) throw new Error("NIFTY 50 instrument search is unavailable.");
        const found = await response.json() as Instrument[];
        const exact = found.find(row => row.exchange === "NSE" && row.instrument_type === "INDEX" && row.name?.toLocaleLowerCase("en-IN") === "nifty 50");
        if (!exact) throw new Error("NIFTY 50 was not found on the active provider.");
        if (!controller.signal.aborted) {
          const chart = { instrumentKey: exact.instrument_key, symbol: "NIFTY 50", providerId: capabilities.provider };
          setProviderId(capabilities.provider);
          setNifty(chart);
          setSelected(chart);
          setError("");
        }
      } catch (cause) {
        if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "Default chart unavailable.");
      }
    }
    void initialize();
    return () => controller.abort();
  }, [apiUrl, token]);

  useEffect(() => {
    if (!providerId || query.trim().length < 3) { setResults([]); setBusy(false); return; }
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      setBusy(true);
      try {
        const params = new URLSearchParams({ query: query.trim(), exchange: "NSE", segment: "EQ", provider_id: providerId });
        const response = await fetch(`${apiUrl}/market-data/instrument-search?${params}`, {
          headers: { Authorization: `Bearer ${token}` }, cache: "no-store", signal: controller.signal,
        });
        if (!response.ok) throw new Error("Stock search is unavailable.");
        const found = await response.json() as Instrument[];
        if (!controller.signal.aborted) { setResults(found.filter(row => row.exchange === "NSE" && ["EQUITY", "EQ", "ETF"].includes(row.instrument_type)).slice(0, 20)); setError(""); }
      } catch (cause) {
        if (!controller.signal.aborted) { setResults([]); setError(cause instanceof Error ? cause.message : "Stock search is unavailable."); }
      } finally { if (!controller.signal.aborted) setBusy(false); }
    }, 400);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [apiUrl, providerId, query, token]);

  return <section className="chart-workspace" aria-label="Market charts">
    <div className="chart-workspace-heading"><div><span className="panel-kicker">MARKET CHARTS</span><h2>Analyze a market</h2><p>NIFTY 50 opens first. Search an NSE stock to inspect its candles, volume, and trend indicators.</p></div>{nifty && <button className="refresh-button" onClick={() => { setSelected(nifty); setQuery(""); setResults([]); }} type="button">NIFTY 50</button>}</div>
    <label className="chart-workspace-search">Search NSE stock
      <input autoComplete="off" maxLength={50} onChange={event => setQuery(event.target.value)} placeholder="Search by symbol or company, e.g. RELIANCE" type="search" value={query} />
    </label>
    {busy && <p className="muted" role="status">Searching stocks…</p>}
    {error && <p className="error-banner" role="alert">{error}</p>}
    {results.length > 0 && <div className="chart-workspace-results" aria-label="Stock search results">{results.map(row => <button key={row.instrument_key} onClick={() => { setSelected({ instrumentKey: row.instrument_key, symbol: row.trading_symbol, providerId }); setQuery(""); setResults([]); }} type="button"><strong>{row.trading_symbol}</strong><span>{row.name || row.instrument_type} · {row.exchange}</span></button>)}</div>}
    {!selected && !error && <p className="muted">Finding the NIFTY 50 instrument…</p>}
    {selected && <HistoricalPriceChart key={`${selected.providerId}:${selected.instrumentKey}`} instrumentKey={selected.instrumentKey} providerId={selected.providerId} symbol={selected.symbol} token={token} apiUrl={apiUrl} />}
    <p className="chart-workspace-note">Market observations are read-only. The chart’s latest quote and incomplete candle can change; neither is an executable paper fill.</p>
  </section>;
}
