"use client";

import { Fragment, useEffect, useRef, useState } from "react";
import { HistoricalPriceChart } from "./HistoricalPriceChart";

type Instrument = {
  id: string;
  exchange: string;
  trading_symbol: string;
  underlying: string;
  instrument_type: "INDEX" | "EQUITY" | "ETF" | "FUTURE" | "OPTION";
  expiry: string | null;
  provider: string;
};

type DiscoveredInstrument = {
  instrument_key: string;
  trading_symbol: string;
  name: string | null;
  exchange: string;
  segment: string;
  instrument_type: string;
  expiry: string | null;
};

type MarketQuote = {
  instrument_token: string;
  timestamp: string;
  timestamp_basis: "exchange" | "provider_response" | "api_response_received" | "unknown";
  last_trade_time: string | null;
  last_price: string;
  bid: string | null;
  ask: string | null;
};

type WatchlistItem = DiscoveredInstrument & { id: string; provider: string };

const quoteTimeLabels: Record<MarketQuote["timestamp_basis"], string> = {
  exchange: "Exchange time",
  provider_response: "Provider response",
  api_response_received: "API receipt",
  unknown: "Time source unknown",
};

type CapabilityState = "requestable" | "unconfigured" | "not_implemented" | "safety_blocked" | "educational_only" | "disabled";
type CapabilityMatrix = {
  provider: string;
  provider_configured: boolean;
  note: string;
  assets: Array<{
    instrument_type: string;
    discovery: CapabilityState;
    snapshot_quote: CapabilityState;
    daily_history: CapabilityState;
    paper_entry: CapabilityState;
    live_order: CapabilityState;
  }>;
};

const capabilityLabels: Record<CapabilityState, string> = {
  requestable: "Requestable",
  unconfigured: "Token needed",
  not_implemented: "Not built",
  safety_blocked: "Safety blocked",
  educational_only: "Educational only",
  disabled: "Disabled",
};

const discoveryMarketLabels: Record<string, { label: string; heading: string; help: string; placeholder: string }> = {
  EQ: {
    label: "Stocks and ETFs",
    heading: "Find a stock or ETF",
    help: "Type a name or symbol, then choose Save to watchlist. Equity paper strategies support NSE shares and ETFs.",
    placeholder: "e.g. RELIANCE",
  },
  INDEX: {
    label: "Indices",
    heading: "Find an index",
    help: "Use this for index charts and quotes. Equity paper strategies are not available for indices.",
    placeholder: "e.g. NIFTY",
  },
  FUT: {
    label: "Futures",
    heading: "Find a future",
    help: "Use this for futures discovery, charts and quotes where your market-data plan supports them.",
    placeholder: "e.g. NIFTY",
  },
  OPT: {
    label: "Options",
    heading: "Find an option",
    help: "Use this for option discovery, charts and quotes where your market-data plan supports them.",
    placeholder: "e.g. NIFTY",
  },
};

export function InstrumentCatalog({ token, apiUrl, defaultOpen = false, compact = false }: { token: string; apiUrl: string; defaultOpen?: boolean; compact?: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  const [search, setSearch] = useState("");
  const [kind, setKind] = useState("");
  const [rows, setRows] = useState<Instrument[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [discoveryQuery, setDiscoveryQuery] = useState("");
  const [discoveryExchange, setDiscoveryExchange] = useState("NSE");
  const [discoverySegment, setDiscoverySegment] = useState("EQ");
  const [discovered, setDiscovered] = useState<DiscoveredInstrument[]>([]);
  const [discoveryBusy, setDiscoveryBusy] = useState(false);
  const [discoveryError, setDiscoveryError] = useState("");
  const [quote, setQuote] = useState<MarketQuote | null>(null);
  const [quoteBusyKey, setQuoteBusyKey] = useState("");
  const [quoteError, setQuoteError] = useState("");
  const [quoteKey, setQuoteKey] = useState("");
  const [quotePlace, setQuotePlace] = useState<"watchlist" | "discovery">("watchlist");
  const quoteRequest = useRef(0);
  const discoverySection = useRef<HTMLElement | null>(null);
  const [watchlist, setWatchlist] = useState<WatchlistItem[]>([]);
  const [watchlistBusy, setWatchlistBusy] = useState(false);
  const [watchlistError, setWatchlistError] = useState("");
  const [watchlistMessage, setWatchlistMessage] = useState("");
  const [watchlistRefresh, setWatchlistRefresh] = useState(0);
  const [savingKey, setSavingKey] = useState("");
  const [removingId, setRemovingId] = useState("");
  const [historySelection, setHistorySelection] = useState<{
    instrumentKey: string; symbol: string; providerId: string;
  } | null>(null);
  const [capabilities, setCapabilities] = useState<CapabilityMatrix | null>(null);
  const [capabilitiesError, setCapabilitiesError] = useState("");

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    fetch(`${apiUrl}/market-data/capabilities`, {
      headers: { Authorization: `Bearer ${token}` }, cache: "no-store", signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) throw new Error("Capabilities unavailable");
        return await response.json() as CapabilityMatrix;
      })
      .then((result) => { if (!controller.signal.aborted) { setCapabilities(result); setCapabilitiesError(""); } })
      .catch(() => { if (!controller.signal.aborted) setCapabilitiesError("Could not load the workflow capability map."); });
    return () => controller.abort();
  }, [apiUrl, open, token]);

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setWatchlistBusy(true);
    fetch(`${apiUrl}/watchlist`, {
      headers: { Authorization: `Bearer ${token}` }, signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) throw new Error("Watchlist unavailable");
        return await response.json() as WatchlistItem[];
      })
      .then((items) => { if (!controller.signal.aborted) { setWatchlist(items); setWatchlistError(""); } })
      .catch(() => { if (!controller.signal.aborted) setWatchlistError("Could not load your watchlist."); })
      .finally(() => { if (!controller.signal.aborted) setWatchlistBusy(false); });
    return () => controller.abort();
  }, [apiUrl, open, token, watchlistRefresh]);

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      setBusy(true);
      setError("");
      try {
        const query = new URLSearchParams({ limit: "50" });
        if (kind) query.set("instrument_type", kind);
        if (search.trim()) query.set("search", search.trim());
        const response = await fetch(`${apiUrl}/instruments?${query}`, {
          headers: { Authorization: `Bearer ${token}` }, signal: controller.signal,
        });
        if (!response.ok) throw new Error("Catalog could not be loaded");
        const result = await response.json() as Instrument[];
        if (!controller.signal.aborted) setRows(result);
      } catch {
        if (!controller.signal.aborted) { setRows([]); setError("Catalog could not be loaded. Try again after the API is ready."); }
      } finally {
        if (!controller.signal.aborted) setBusy(false);
      }
    }, 250);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [apiUrl, kind, open, search, token]);

  useEffect(() => {
    const onWatchlistChange = () => setWatchlistRefresh(value => value + 1);
    window.addEventListener("market-watchlist-changed", onWatchlistChange);
    return () => window.removeEventListener("market-watchlist-changed", onWatchlistChange);
  }, []);

  useEffect(() => {
    setDiscovered([]);
    const queryText = discoveryQuery.trim();
    if (!open || queryText.length < 3) {
      setDiscovered([]);
      setDiscoveryBusy(false);
      setDiscoveryError("");
      return;
    }
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      setDiscoveryBusy(true);
      setDiscoveryError("");
      try {
        const params = new URLSearchParams({
          query: queryText, exchange: discoveryExchange, segment: discoverySegment,
          provider_id: capabilities?.provider || "upstox",
        });
        const response = await fetch(`${apiUrl}/market-data/instrument-search?${params}`, {
          headers: { Authorization: `Bearer ${token}` }, signal: controller.signal,
        });
        if (!response.ok) throw new Error("Provider search unavailable");
        const result = await response.json() as DiscoveredInstrument[];
        if (!controller.signal.aborted) setDiscovered(result);
      } catch {
        if (!controller.signal.aborted) {
          setDiscovered([]);
          setDiscoveryError("Instrument search is unavailable. Check the read-only data connection.");
        }
      } finally {
        if (!controller.signal.aborted) setDiscoveryBusy(false);
      }
    }, 500);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [apiUrl, capabilities?.provider, discoveryExchange, discoveryQuery, discoverySegment, open, token]);

  async function loadQuote(instrumentKey: string, providerId: string, place: "watchlist" | "discovery") {
    const requestId = ++quoteRequest.current;
    setQuoteKey(instrumentKey);
    setQuotePlace(place);
    setQuoteBusyKey(instrumentKey);
    setQuoteError("");
    setQuote(null);
    try {
      const params = new URLSearchParams({ instrument_key: instrumentKey, provider_id: providerId });
      const response = await fetch(`${apiUrl}/market-data/quote?${params}`, {
        headers: { Authorization: `Bearer ${token}` }, cache: "no-store",
      });
      if (!response.ok) throw new Error("Quote unavailable");
      const result = await response.json() as MarketQuote;
      if (requestId === quoteRequest.current) setQuote(result);
    } catch {
      if (requestId === quoteRequest.current) {
        setQuoteError("The quote is unavailable. Try again after checking market data.");
      }
    } finally {
      if (requestId === quoteRequest.current) setQuoteBusyKey("");
    }
  }

  function quotePanel(instrumentKey: string, place: "watchlist" | "discovery") {
    if (quoteKey !== instrumentKey || quotePlace !== place) return null;
    if (quoteBusyKey) return <div className="catalog-quote" role="status">Loading quote…</div>;
    if (quoteError) return <div className="error-banner" role="alert">{quoteError}</div>;
    if (!quote) return null;
    const tradedAt = quote.last_trade_time ? new Date(quote.last_trade_time) : null;
    const tradeAge = tradedAt ? Date.now() - tradedAt.getTime() : Infinity;
    const freshTrade = tradeAge >= 0 && tradeAge <= 60_000;
    return <div className="catalog-quote" role="status">
      <strong>₹{quote.last_price}</strong> last traded · Bid {quote.bid ?? "—"} · Ask {quote.ask ?? "—"}<br />
      {quoteTimeLabels[quote.timestamp_basis]} {new Date(quote.timestamp).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" })} IST.
      {tradedAt ? <> Last trade {tradedAt.toLocaleString("en-IN", { timeZone: "Asia/Kolkata" })} IST.</> : " Exchange trade time unavailable."}
      <br /><b>{freshTrade ? "Recent exchange trade" : "Historical or unverified trade time"}</b> · Snapshot only; a response timestamp does not prove a tradable price.
    </div>;
  }

  async function saveWatchlist(row: DiscoveredInstrument) {
    setSavingKey(row.instrument_key);
    setWatchlistError("");
    setWatchlistMessage("");
    try {
      const response = await fetch(`${apiUrl}/watchlist`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify({
          instrument_key: row.instrument_key, query: discoveryQuery.trim(),
          exchange: discoveryExchange, segment: discoverySegment,
          provider_id: capabilities?.provider || "upstox",
        }),
      });
      if (!response.ok) throw new Error("Could not save instrument");
      setWatchlistMessage(`${row.trading_symbol} saved to your watchlist.`);
      setWatchlistRefresh((value) => value + 1);
      window.dispatchEvent(new Event("market-watchlist-changed"));
    } catch {
      setWatchlistError("Could not save that instrument. Search again and retry.");
    } finally {
      setSavingKey("");
    }
  }

  async function removeWatchlist(item: WatchlistItem) {
    setRemovingId(item.id);
    setWatchlistError("");
    setWatchlistMessage("");
    try {
      const response = await fetch(`${apiUrl}/watchlist/${item.id}`, {
        method: "DELETE", headers: { Authorization: `Bearer ${token}` },
      });
      if (!response.ok) throw new Error("Could not remove instrument");
      setWatchlistMessage(`${item.trading_symbol} removed from your watchlist.`);
      setWatchlistRefresh((value) => value + 1);
      window.dispatchEvent(new Event("market-watchlist-changed"));
    } catch {
      setWatchlistError("Could not remove that watchlist item. Try again.");
    } finally {
      setRemovingId("");
    }
  }

  function searchLiveProvider() {
    const query = search.trim();
    if (!query) return;
    const segmentByKind: Record<string, string> = {
      EQUITY: "EQ", ETF: "EQ", INDEX: "INDEX", FUTURE: "FUT", OPTION: "OPT",
    };
    setDiscoveryQuery(query);
    setDiscoverySegment(segmentByKind[kind] || "EQ");
    window.setTimeout(() => discoverySection.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 0);
  }

  const selectedDiscoveryMarket = discoveryMarketLabels[discoverySegment] || discoveryMarketLabels.EQ;

  return <details className="panel instrument-catalog" open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>{compact ? "Find stocks & manage watchlist" : "Market instrument catalog"} <span>Equities · ETFs · futures · options</span></summary>
    {!compact && <>
    <p className="muted">Browse the local instrument catalog, or search your connected provider below.</p>
    <details className="catalog-capabilities">
      <summary>Data and trading availability</summary>
      {capabilitiesError ? <p className="error-banner" role="alert">{capabilitiesError}</p> : capabilities ? <>
        <p className="muted">{capabilities.note}</p>
        <div className="catalog-table-scroll"><table className="catalog-table">
          <thead><tr><th>Asset</th><th>Discover</th><th>Quote</th><th>Daily history</th><th>Paper entry</th><th>Live order</th></tr></thead>
          <tbody>{capabilities.assets.map((item) => <tr key={item.instrument_type}>
            <td>{item.instrument_type}</td><td>{capabilityLabels[item.discovery]}</td><td>{capabilityLabels[item.snapshot_quote]}</td><td>{capabilityLabels[item.daily_history]}</td><td>{capabilityLabels[item.paper_entry]}</td><td>{capabilityLabels[item.live_order]}</td>
          </tr>)}</tbody>
        </table></div>
        <p className="muted">Options paper entries are safety blocked until the required quote checks pass. “Educational only” means an operator explicitly allowed unverified quote times; it is not a live-trading approval.</p>
      </> : <p className="muted" role="status">Loading workflow availability…</p>}
    </details>
    <div className="catalog-controls">
      <label htmlFor="catalog-search">Search symbol or underlying
        <input id="catalog-search" maxLength={64} onChange={(event) => setSearch(event.target.value)} placeholder="e.g. NIFTY" type="search" value={search} />
      </label>
      <label htmlFor="catalog-kind">Instrument type
        <select id="catalog-kind" onChange={(event) => setKind(event.target.value)} value={kind}>
          <option value="">All types</option>
          <option value="EQUITY">Equity</option>
          <option value="ETF">ETF</option>
          <option value="INDEX">Index</option>
          <option value="FUTURE">Future</option>
          <option value="OPTION">Option</option>
        </select>
      </label>
    </div>
    {error ? <p className="error-banner" role="alert">{error}</p> : busy ? <p className="muted" role="status">Loading instruments…</p> : rows.length ? (
      <div className="catalog-table-scroll"><table className="catalog-table">
        <thead><tr><th>Symbol</th><th>Type</th><th>Exchange</th><th>Expiry</th><th>Source</th></tr></thead>
        <tbody>{rows.map((row) => <tr key={row.id}><td>{row.trading_symbol}</td><td>{row.instrument_type}</td><td>{row.exchange}</td><td>{row.expiry || "—"}</td><td>{row.provider}</td></tr>)}</tbody>
      </table></div>
    ) : <div className="catalog-empty"><p className="muted">{search.trim() ? <>No {kind ? kind.toLowerCase() : "local"} match for <strong>{search.trim()}</strong> in your saved catalog.</> : "No matching instruments have been added to this local catalog yet."}</p>{search.trim() ? <button className="primary-button" onClick={searchLiveProvider} type="button">Search live market data</button> : null}</div>}
    {rows.length === 50 ? <p className="muted">Showing the first 50 matches. Narrow the search to find a symbol.</p> : null}
    </>}
    <section className="catalog-watchlist" aria-label="Your watchlist">
      <h3>Your watchlist</h3>
      <p className="muted">Save a stock to compare its strategies above. Use History to open its chart.</p>
      {watchlistError ? <p className="error-banner" role="alert">{watchlistError}</p> : null}
      {watchlistMessage ? <p className="catalog-message" role="status">{watchlistMessage}</p> : null}
      {watchlistBusy ? <p className="muted" role="status">Loading watchlist…</p> : watchlist.length ? <div className="catalog-table-scroll"><table className="catalog-table">
        <thead><tr><th>Symbol</th><th>Type</th><th>Exchange</th><th>Expiry</th><th>Actions</th></tr></thead>
        <tbody>{watchlist.map((item) => <Fragment key={item.id}><tr>
          <td><strong>{item.trading_symbol}</strong>{item.name ? <small>{item.name}</small> : null}</td>
          <td>{item.instrument_type}</td><td>{item.exchange}</td><td>{item.expiry || "—"}</td>
          <td className="catalog-actions"><button className="refresh-button" disabled={!!quoteBusyKey} onClick={() => void loadQuote(item.instrument_key, item.provider, "watchlist")} type="button">View quote</button><button className="inline-button" onClick={() => setHistorySelection({ instrumentKey: item.instrument_key, symbol: item.trading_symbol, providerId: item.provider })} type="button">History</button><button className="inline-button" disabled={!!removingId} onClick={() => void removeWatchlist(item)} type="button">{removingId === item.id ? "Removing…" : "Remove"}</button></td>
        </tr>{quoteKey === item.instrument_key && quotePlace === "watchlist" && <tr><td colSpan={5}>{quotePanel(item.instrument_key, "watchlist")}</td></tr>}</Fragment>)}</tbody>
      </table></div> : <p className="muted">No saved instruments yet. Search below to add one.</p>}
    </section>
    <section className="catalog-discovery" aria-label="Read-only market instrument discovery" ref={discoverySection}>
      <h3>{selectedDiscoveryMarket.heading}</h3>
      <p className="muted">{selectedDiscoveryMarket.help}</p>
      <div className="catalog-controls">
        <label htmlFor="discovery-search">Symbol or name
          <input id="discovery-search" maxLength={50} onChange={(event) => setDiscoveryQuery(event.target.value)} placeholder={selectedDiscoveryMarket.placeholder} type="search" value={discoveryQuery} />
        </label>
        <label htmlFor="discovery-exchange">Exchange
          <select id="discovery-exchange" onChange={(event) => setDiscoveryExchange(event.target.value)} value={discoveryExchange}>
            <option value="NSE">NSE</option><option value="BSE">BSE</option><option value="MCX">MCX</option>
          </select>
        </label>
        <label htmlFor="discovery-segment">Market
          <select id="discovery-segment" onChange={(event) => setDiscoverySegment(event.target.value)} value={discoverySegment}>
            <option value="EQ">{discoveryMarketLabels.EQ.label}</option><option value="INDEX">{discoveryMarketLabels.INDEX.label}</option><option value="FUT">{discoveryMarketLabels.FUT.label}</option><option value="OPT">{discoveryMarketLabels.OPT.label}</option>
          </select>
        </label>
      </div>
      {discoveryError ? <p className="error-banner" role="alert">{discoveryError}</p> : null}
      {discoveryBusy ? <p className="muted" role="status">Searching instruments…</p> : null}
      {!discoveryBusy && !discoveryError && discoveryQuery.trim().length >= 3 && !discovered.length ? <p className="muted">No matching provider instruments found.</p> : null}
      {discovered.length ? <div className="catalog-table-scroll"><table className="catalog-table">
        <thead><tr><th>Symbol</th><th>Provider type</th><th>Exchange</th><th>Expiry</th><th>Actions</th></tr></thead>
        <tbody>{discovered.map((row) => <Fragment key={row.instrument_key}><tr>
          <td><strong>{row.trading_symbol}</strong>{row.name ? <small>{row.name}</small> : null}</td>
          <td>{row.instrument_type}</td><td>{row.exchange}</td><td>{row.expiry || "—"}</td>
          <td className="catalog-actions"><button className="refresh-button" disabled={!!quoteBusyKey} onClick={() => void loadQuote(row.instrument_key, capabilities?.provider || "upstox", "discovery")} type="button">{quoteBusyKey === row.instrument_key ? "Loading…" : "View quote"}</button><button className="inline-button" onClick={() => setHistorySelection({ instrumentKey: row.instrument_key, symbol: row.trading_symbol, providerId: capabilities?.provider || "upstox" })} type="button">History</button><button className="inline-button" disabled={!!savingKey || watchlist.some((item) => item.provider === (capabilities?.provider || "upstox") && item.instrument_key === row.instrument_key)} onClick={() => void saveWatchlist(row)} type="button">{savingKey === row.instrument_key ? "Saving…" : watchlist.some((item) => item.provider === (capabilities?.provider || "upstox") && item.instrument_key === row.instrument_key) ? "Saved" : "Save"}</button></td>
        </tr>{quoteKey === row.instrument_key && quotePlace === "discovery" && <tr><td colSpan={5}>{quotePanel(row.instrument_key, "discovery")}</td></tr>}</Fragment>)}</tbody>
      </table></div> : null}
    </section>
    {historySelection ? <HistoricalPriceChart key={`${historySelection.providerId}:${historySelection.instrumentKey}`} instrumentKey={historySelection.instrumentKey} providerId={historySelection.providerId} symbol={historySelection.symbol} token={token} apiUrl={apiUrl} onClose={() => setHistorySelection(null)} /> : null}
  </details>;
}
