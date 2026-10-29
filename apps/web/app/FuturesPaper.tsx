"use client";

import { useCallback, useEffect, useState } from "react";

type Contract = { instrument_key: string; trading_symbol: string; expiry: string; lot_size: number };

type FuturesQuote = {
  contract: Contract;
  quote: { bid: string | null; ask: string | null; last_price: string; quote_timestamp: string; timestamp_basis: string };
  margin_pct: number;
  margin_per_lot: string | null;
  margin_disclaimer: string;
};

type FuturesTrade = {
  id: string;
  trading_symbol: string;
  expiry: string;
  side: "LONG" | "SHORT";
  lots: number;
  quantity_units: number;
  entry_price: string;
  margin_blocked: string;
  stop_loss_amount: string;
  target_profit_amount: string | null;
  trailing_stop_amount: string | null;
  last_mark_price: string | null;
  last_marked_at: string | null;
  unrealized_pnl: string | null;
  exit_price: string | null;
  realized_pnl: string | null;
  exit_reason: string | null;
  status: "OPEN" | "CLOSED";
  opened_at: string;
  closed_at: string | null;
  execution_warnings: string[];
};

const rupees = (value: string | null) =>
  value === null ? "—" : `₹${Number(value).toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;

const signed = (value: string | null) => {
  if (value === null) return "—";
  const amount = Number(value);
  return `${amount >= 0 ? "+" : "−"}₹${Math.abs(amount).toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;
};

async function readError(response: Response): Promise<string> {
  try {
    const body = await response.json();
    return typeof body.detail === "string" ? body.detail : "Request was rejected.";
  } catch {
    return `Request failed (${response.status}).`;
  }
}

export function FuturesPaper({ token, apiUrl }: { token: string; apiUrl: string }) {
  const [contracts, setContracts] = useState<Contract[]>([]);
  const [instrumentKey, setInstrumentKey] = useState("");
  const [quote, setQuote] = useState<FuturesQuote | null>(null);
  const [trades, setTrades] = useState<FuturesTrade[]>([]);
  const [side, setSide] = useState<"LONG" | "SHORT">("LONG");
  const [lots, setLots] = useState("1");
  const [stopLoss, setStopLoss] = useState("");
  const [target, setTarget] = useState("");
  const [trailing, setTrailing] = useState("");
  const [acknowledged, setAcknowledged] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const headers = { Authorization: `Bearer ${token}` };

  const loadTrades = useCallback(async () => {
    const response = await fetch(`${apiUrl}/paper/futures/trades`, { headers: { Authorization: `Bearer ${token}` } });
    if (response.ok) setTrades(await response.json());
  }, [apiUrl, token]);

  useEffect(() => {
    fetch(`${apiUrl}/paper/futures/contracts`, { headers: { Authorization: `Bearer ${token}` } })
      .then(async (response) => {
        if (!response.ok) throw new Error(await readError(response));
        return response.json() as Promise<Contract[]>;
      })
      .then((rows) => { setContracts(rows); setInstrumentKey((current) => current || rows[0]?.instrument_key || ""); })
      .catch((reason: Error) => setError(`Futures contracts are unavailable: ${reason.message}`));
    void loadTrades();
    const timer = window.setInterval(() => void loadTrades(), 15000);
    return () => window.clearInterval(timer);
  }, [apiUrl, token, loadTrades]);

  const loadQuote = useCallback(async () => {
    if (!instrumentKey) return;
    const response = await fetch(`${apiUrl}/paper/futures/quote?instrument_key=${encodeURIComponent(instrumentKey)}`, { headers: { Authorization: `Bearer ${token}` } });
    if (response.ok) { setQuote(await response.json()); } else { setQuote(null); setError(await readError(response)); }
  }, [apiUrl, token, instrumentKey]);

  useEffect(() => { void loadQuote(); }, [loadQuote]);

  async function openTrade() {
    setBusy(true); setError(""); setMessage("");
    try {
      const response = await fetch(`${apiUrl}/paper/futures/trades`, {
        method: "POST",
        headers: { ...headers, "Content-Type": "application/json", "Idempotency-Key": crypto.randomUUID().replaceAll("-", "") },
        body: JSON.stringify({
          instrument_key: instrumentKey, side, lots: Number(lots), stop_loss_amount: stopLoss,
          target_profit_amount: target || null, trailing_stop_amount: trailing || null,
          acknowledged_unbounded_risk: acknowledged,
        }),
      });
      if (!response.ok) { setError(await readError(response)); return; }
      const trade = (await response.json()) as FuturesTrade;
      setMessage([`Paper ${trade.side.toLowerCase()} opened at ${rupees(trade.entry_price)}; margin blocked ${rupees(trade.margin_blocked)}.`, ...trade.execution_warnings].join(" "));
      setAcknowledged(false);
      await loadTrades();
    } finally {
      setBusy(false);
    }
  }

  async function closeTrade(id: string) {
    setBusy(true); setError(""); setMessage("");
    try {
      const response = await fetch(`${apiUrl}/paper/futures/trades/${id}/close`, { method: "POST", headers });
      if (!response.ok) { setError(await readError(response)); return; }
      const trade = (await response.json()) as FuturesTrade;
      setMessage(`Paper futures closed at ${rupees(trade.exit_price)} · realized ${signed(trade.realized_pnl)}.`);
      await loadTrades();
    } finally {
      setBusy(false);
    }
  }

  const lotCount = Number(lots) || 0;
  const marginEstimate = quote?.margin_per_lot ? Number(quote.margin_per_lot) * lotCount : null;
  const open = trades.filter((trade) => trade.status === "OPEN");
  const closed = trades.filter((trade) => trade.status === "CLOSED").slice(0, 10);
  const canOpen = !busy && instrumentKey && lotCount >= 1 && Number(stopLoss) > 0 && acknowledged;

  return (
    <details className="strategy-builder futures-paper" id="paper-futures">
      <summary>NIFTY futures (paper){open.length ? ` · ${open.length} open` : ""}</summary>
      <p className="strategy-library-note">Simulated fills at the live touch. Futures risk is not capped by the instrument, so a stop-loss is required and counts toward your risk limits. Live broker orders remain disabled.</p>
      <div className="strategy-controls futures-controls">
        <label>Contract
          <select value={instrumentKey} onChange={(event) => setInstrumentKey(event.target.value)}>
            {contracts.map((contract) => <option key={contract.instrument_key} value={contract.instrument_key}>{contract.trading_symbol} · lot {contract.lot_size}</option>)}
          </select>
        </label>
        <label>Direction
          <select value={side} onChange={(event) => setSide(event.target.value as "LONG" | "SHORT")}>
            <option value="LONG">Long (buy)</option>
            <option value="SHORT">Short (sell)</option>
          </select>
        </label>
        <label>Lots<input inputMode="numeric" min={1} max={50} type="number" value={lots} onChange={(event) => setLots(event.target.value)} /></label>
        <label>Stop-loss ₹ (required)<input inputMode="decimal" min={1} type="number" value={stopLoss} onChange={(event) => setStopLoss(event.target.value)} /></label>
        <label>Target ₹<input inputMode="decimal" min={1} type="number" value={target} onChange={(event) => setTarget(event.target.value)} /></label>
        <label>Trailing stop ₹<input inputMode="decimal" min={1} type="number" value={trailing} onChange={(event) => setTrailing(event.target.value)} /></label>
      </div>
      {quote ? (
        <p className="futures-quote" role="status">
          Bid {rupees(quote.quote.bid)} · Ask {rupees(quote.quote.ask)} · as of {new Date(quote.quote.quote_timestamp).toLocaleTimeString("en-IN")}
          {marginEstimate !== null ? ` · margin ≈ ${rupees(String(marginEstimate))} (${quote.margin_pct}% illustrative)` : ""}
          <button className="inline-button" disabled={busy} onClick={() => void loadQuote()} type="button">Refresh quote</button>
        </p>
      ) : null}
      {quote ? <p className="strategy-library-note">{quote.margin_disclaimer}</p> : null}
      <label className="futures-ack">
        <input checked={acknowledged} onChange={(event) => setAcknowledged(event.target.checked)} type="checkbox" />
        I understand a price gap can lose more than the stop-loss amount.
      </label>
      <button className="refresh-button" disabled={!canOpen} onClick={() => void openTrade()} type="button">Open paper futures position</button>
      {error ? <p className="error-banner" role="alert">{error}</p> : null}
      {message ? <p className="strategy-library-note" role="status">{message}</p> : null}

      <div className="futures-positions">
        <strong>Open futures positions</strong>
        {open.length === 0 ? <p className="specialist-empty">No open futures positions.</p> : open.map((trade) => (
          <article className="futures-position" key={trade.id}>
            <div><b>{trade.side} {trade.lots} × {trade.trading_symbol}</b><small>Entry {rupees(trade.entry_price)} · margin {rupees(trade.margin_blocked)} · stop {rupees(trade.stop_loss_amount)}{trade.target_profit_amount ? ` · target ${rupees(trade.target_profit_amount)}` : ""}</small></div>
            <div><span className={Number(trade.unrealized_pnl ?? 0) >= 0 ? "futures-pnl--up" : "futures-pnl--down"}>{signed(trade.unrealized_pnl)}</span><small>{trade.last_mark_price ? `Mark ${rupees(trade.last_mark_price)} · ${new Date(trade.last_marked_at ?? trade.opened_at).toLocaleTimeString("en-IN")}` : "Waiting for the worker's first mark"}</small></div>
            <button className="inline-button" disabled={busy} onClick={() => void closeTrade(trade.id)} type="button">Close at market</button>
          </article>
        ))}
        {closed.length ? <>
          <strong>Recently closed</strong>
          {closed.map((trade) => (
            <article className="futures-position" key={trade.id}>
              <div><b>{trade.side} {trade.lots} × {trade.trading_symbol}</b><small>{rupees(trade.entry_price)} → {rupees(trade.exit_price)} · {(trade.exit_reason ?? "").replaceAll("_", " ").toLowerCase()}</small></div>
              <div><span className={Number(trade.realized_pnl ?? 0) >= 0 ? "futures-pnl--up" : "futures-pnl--down"}>{signed(trade.realized_pnl)}</span><small>{trade.closed_at ? new Date(trade.closed_at).toLocaleString("en-IN") : ""}</small></div>
            </article>
          ))}
        </> : null}
      </div>
    </details>
  );
}
