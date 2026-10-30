"use client";

import { useEffect, useState } from "react";
import { HistoricalPriceChart } from "./HistoricalPriceChart";

type Instrument = { instrument_key: string; trading_symbol: string; name: string | null; exchange: string; segment: string; instrument_type: string };
type Selected = { instrumentKey: string; symbol: string; providerId: string };

export function ChartWorkspace({ token, apiUrl }: { token: string; apiUrl: string }) {
  const [selected, setSelected] = useState<Selected | null>(null);
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
          setSelected({ instrumentKey: exact.instrument_key, symbol: "NIFTY 50", providerId: capabilities.provider });
          setError("");
        }
      } catch (cause) {
        if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "Default chart unavailable.");
      }
    }
    void initialize();
    return () => controller.abort();
  }, [apiUrl, token]);

  return <section className="chart-workspace" aria-label="NIFTY 50 chart">
    <div className="chart-workspace-heading"><div><h2>NIFTY 50 chart</h2><p>Candles, volume and trend indicators. The option chain is below.</p></div></div>
    {error && <p className="error-banner" role="alert">{error}</p>}
    {!selected && !error && <p className="muted">Finding the NIFTY 50 instrument…</p>}
    {selected && <HistoricalPriceChart key={`${selected.providerId}:${selected.instrumentKey}`} instrumentKey={selected.instrumentKey} providerId={selected.providerId} symbol={selected.symbol} token={token} apiUrl={apiUrl} />}
    <p className="chart-workspace-note">Market observations are read-only. The chart’s latest quote and incomplete candle can change; neither is an executable paper fill.</p>
  </section>;
}
