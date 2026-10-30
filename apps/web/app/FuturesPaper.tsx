"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { StrategyDetails } from "./StrategyDetails";

type Contract = { instrument_key: string; trading_symbol: string; expiry: string; lot_size: number };

type FuturesQuote = {
  contract: Contract;
  quote: { bid: string | null; ask: string | null; last_price: string; quote_timestamp: string; timestamp_basis: string };
  margin_per_lot: string | null;
  margin_breakdown: { span: string; exposure: string; worst_scenario: string } | null;
  margin_disclaimer: string;
};

export type FuturesTrade = {
  id: string;
  strategy_key: string | null;
  strategy_name: string | null;
  intraday: boolean;
  trading_symbol: string;
  expiry: string;
  side: "LONG" | "SHORT";
  lots: number;
  quantity_units: number;
  entry_price: string;
  margin_blocked: string;
  max_loss: string;
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

type FuturesStrategy = {
  key: string;
  name: string;
  description: string;
  version: string;
  side: "LONG" | "SHORT";
  intraday: boolean;
  stop_points: string;
  target_points: string;
  trailing_points: string;
  conditions: { metric: string; operator: string; threshold: string }[];
  note: string;
};

const OPERATOR_LABELS: Record<string, string> = { GT: ">", GTE: "≥", LT: "<", LTE: "≤" };

function futuresRuleText(condition: FuturesStrategy["conditions"][number]) {
  if (condition.metric === "regime_direction") return `5-minute NIFTY regime points ${Number(condition.threshold) > 0 ? "up" : "down"}`;
  return `${FUTURES_RULE_LABELS[condition.metric] ?? condition.metric} ${OPERATOR_LABELS[condition.operator] ?? condition.operator} ${condition.threshold}`;
}

export const FUTURES_RULE_LABELS: Record<string, string> = {
  regime_direction: "5-minute regime direction (1 up, −1 down)",
  put_call_ratio_oi: "Open-interest PCR",
  futures_relative_spread_pct: "Futures spread %",
  futures_basis_pct: "Futures basis %",
};
const ROLLOVER_DAYS = 2;

const rupees = (value: string | null) =>
  value === null ? "—" : `₹${Number(value).toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;

const signed = (value: string | null) => {
  if (value === null) return "—";
  const amount = Number(value);
  return `${amount >= 0 ? "+" : "−"}₹${Math.abs(amount).toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;
};

const indiaDate = () => new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(new Date());
const daysUntil = (expiry: string) => (Date.parse(`${expiry}T00:00:00Z`) - Date.parse(`${indiaDate()}T00:00:00Z`)) / 86_400_000;

async function readError(response: Response): Promise<string> {
  try {
    const body = await response.json();
    return typeof body.detail === "string" ? body.detail : "Request was rejected.";
  } catch {
    return `Request failed (${response.status}).`;
  }
}

export function FuturesPaper({ token, apiUrl, waitingKeys, entriesPaused, onChanged }: {
  token: string;
  apiUrl: string;
  waitingKeys: string[];
  entriesPaused: boolean;
  onChanged: () => void;
}) {
  const [contracts, setContracts] = useState<Contract[]>([]);
  const [strategies, setStrategies] = useState<FuturesStrategy[]>([]);
  const [strategyLots, setStrategyLots] = useState(1);
  const [detailKey, setDetailKey] = useState("");
  const [instrumentKey, setInstrumentKey] = useState("");
  const [quote, setQuote] = useState<FuturesQuote | null>(null);
  const [side, setSide] = useState<"LONG" | "SHORT">("LONG");
  const [lots, setLots] = useState("1");
  const [stopLoss, setStopLoss] = useState("");
  const [target, setTarget] = useState("");
  const [trailing, setTrailing] = useState("");
  const [acknowledged, setAcknowledged] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const armCommand = useRef<{ payload: string; key: string } | null>(null);
  const headers = { Authorization: `Bearer ${token}` };

  useEffect(() => {
    fetch(`${apiUrl}/paper/futures/contracts`, { headers: { Authorization: `Bearer ${token}` } })
      .then(async (response) => {
        if (!response.ok) throw new Error(await readError(response));
        return response.json() as Promise<Contract[]>;
      })
      .then((rows) => { setContracts(rows); setInstrumentKey((current) => current || rows[0]?.instrument_key || ""); })
      .catch((reason: Error) => setError(`Futures contracts are unavailable: ${reason.message}`));
    fetch(`${apiUrl}/paper/futures/strategies`, { headers: { Authorization: `Bearer ${token}` } })
      .then(async (response) => {
        if (!response.ok) throw new Error(await readError(response));
        return response.json() as Promise<FuturesStrategy[]>;
      })
      .then(setStrategies)
      .catch((reason: Error) => setError(`Futures strategies are unavailable: ${reason.message}`));
  }, [apiUrl, token]);

  const loadQuote = useCallback(async () => {
    if (!instrumentKey) return;
    const response = await fetch(`${apiUrl}/paper/futures/quote?instrument_key=${encodeURIComponent(instrumentKey)}`, { headers: { Authorization: `Bearer ${token}` } });
    if (response.ok) { setQuote(await response.json()); } else { setQuote(null); setError(await readError(response)); }
  }, [apiUrl, token, instrumentKey]);

  useEffect(() => { void loadQuote(); }, [loadQuote]);

  async function startStrategy(strategy: FuturesStrategy) {
    setBusy(true); setError(""); setMessage("");
    const payload = JSON.stringify({
      strategy_key: strategy.key, expected_template_version: strategy.version,
      lots: strategyLots, acknowledged_unbounded_risk: true,
    });
    if (armCommand.current?.payload !== payload) {
      armCommand.current = { payload, key: `arm_${crypto.randomUUID().replaceAll("-", "")}` };
    }
    try {
      const response = await fetch(`${apiUrl}/paper/futures/deployments`, {
        method: "POST",
        headers: { ...headers, "Content-Type": "application/json", "Idempotency-Key": armCommand.current.key },
        body: payload,
      });
      if (!response.ok) { setError(await readError(response)); return; }
      armCommand.current = null;
      setMessage(`${strategy.name} started in paper mode. It waits for its entry rules and risk checks before opening a simulated position. Cancel it any time under Running.`);
      onChanged();
    } finally {
      setBusy(false);
    }
  }

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
      onChanged();
    } finally {
      setBusy(false);
    }
  }

  const lotCount = Number(lots) || 0;
  const marginEstimate = quote?.margin_per_lot ? Number(quote.margin_per_lot) * lotCount : null;
  const canOpen = !busy && instrumentKey && lotCount >= 1 && Number(stopLoss) > 0 && acknowledged;
  const frontContract = contracts.find((contract) => daysUntil(contract.expiry) > ROLLOVER_DAYS);
  const unit = frontContract ? frontContract.lot_size * strategyLots : null;
  const amount = (points: string) => unit === null ? "unavailable" : rupees(String(Number(points) * unit));
  const detail = strategies.find((strategy) => strategy.key === detailKey);
  const detailMargin = quote?.margin_per_lot && quote.contract.instrument_key === frontContract?.instrument_key
    ? Number(quote.margin_per_lot) * strategyLots : null;

  return (
    <section className="futures-paper" id="paper-futures" aria-label="NIFTY futures paper strategies">
      <p className="trade-note">{frontContract ? `Fills on ${frontContract.trading_symbol}. ` : "Contract unavailable. "}Futures risk isn't capped: <strong>a price gap can lose more than the stop-loss</strong>, and starting a strategy accepts that for this paper position.</p>
      <label className="futures-lots">Lots per strategy
        <input disabled={busy} inputMode="numeric" min={1} max={10} type="number" value={strategyLots} onChange={(event) => setStrategyLots(Math.max(1, Math.min(10, Math.trunc(Number(event.target.value) || 1))))} />
      </label>
      <ul className="strategy-start-list" aria-label="Futures strategies">
        {strategies.map((strategy) => {
          const waiting = waitingKeys.includes(strategy.key);
          return (
            <li className="strategy-tile strategy-tile--futures" key={strategy.key}>
              <button aria-label={`${strategy.name}: rules and details`} className="strategy-tile-open" onClick={() => setDetailKey(strategy.key)} type="button">
                <strong>{strategy.name}</strong>
                <small>{strategy.intraday ? "Exits the same day" : "Held overnight"}<u>Rules and details</u></small>
                <small className="strategy-tile-figures"><span className="detail-profit">Target +{amount(strategy.target_points)}</span><span className="detail-loss">Stop −{amount(strategy.stop_points)}</span></small>
              </button>
              {waiting ? <span>Waiting</span> : <button aria-label={`Start ${strategy.name} in paper`} className="strategy-play" disabled={busy || entriesPaused || !frontContract} onClick={() => void startStrategy(strategy)} title={entriesPaused ? "Entries are paused" : "Start in paper"} type="button">▶</button>}
            </li>
          );
        })}
      </ul>
      {detail ? <StrategyDetails
        action={waitingKeys.includes(detail.key) ? <span>Waiting for entry. Cancel it under Running.</span> : <button className="primary-button" disabled={busy || entriesPaused || !frontContract} onClick={() => { setDetailKey(""); void startStrategy(detail); }} type="button">▶ Start in paper</button>}
        context={`${frontContract?.trading_symbol ?? "Contract unavailable"}, ${strategyLots} lot${strategyLots === 1 ? "" : "s"}. Simulated.`}
        entry={[
          ...detail.conditions.map((condition) => ({ text: futuresRuleText(condition) })),
          { text: "The market is open, the quotes pass quality checks and the account's risk limits have room" },
        ]}
        exit={[
          { text: `Target: profit reaches +${amount(detail.target_points)}` },
          { text: `Stop-loss: loss reaches −${amount(detail.stop_points)}` },
          { text: `Trailing stop: once profit has reached ${amount(detail.trailing_points)}, it falls by that much from its peak` },
          detail.intraday
            ? { text: "Every day: 20 minutes before the close, so it isn't held overnight" }
            : { text: `${ROLLOVER_DAYS} days before expiry: it closes, then waits for its entry rules on the next contract` },
          { text: "You press Close under Running" },
        ]}
        figures={[
          { label: "Target", value: `+${amount(detail.target_points)}`, tone: "profit" },
          { label: "Stop-loss", value: `−${amount(detail.stop_points)}`, tone: "loss" },
          { label: "Max profit", value: "No fixed limit" },
          { label: "Max loss", value: "No fixed limit" },
          ...(detailMargin === null ? [] : [{ label: "Estimated margin", value: rupees(String(detailMargin)) }]),
        ]}
        name={detail.name}
        notes={[
          "Futures risk isn't capped. A price gap can lose more than the stop-loss.",
          "Profit and loss are simulated and before charges.",
          "These rules are exploratory and not validated. They decide when a simulated entry may be attempted; they are not a forecast.",
        ]}
        onClose={() => setDetailKey("")}
        summary={detail.description}
        tone="futures"
      /> : null}
      {strategies.length === 0 && !error ? <p className="trade-empty">Loading futures strategies…</p> : null}
      {strategies[0] ? <p className="strategy-library-note">{strategies[0].note}</p> : null}
      {error ? <p className="error-banner" role="alert">{error}</p> : null}
      {message ? <p className="strategy-library-note" role="status">{message}</p> : null}

      <details className="strategy-builder">
        <summary>Manual futures ticket</summary>
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
            {marginEstimate !== null ? ` · margin ≈ ${rupees(String(marginEstimate))} (estimate)` : ""}
            <button className="inline-button" disabled={busy} onClick={() => void loadQuote()} type="button">Refresh quote</button>
          </p>
        ) : null}
        {quote?.margin_breakdown ? (
          <p className="strategy-library-note">
            Per lot: SPAN-style {rupees(quote.margin_breakdown.span)} (worst case {quote.margin_breakdown.worst_scenario}) + exposure {rupees(quote.margin_breakdown.exposure)}. {quote.margin_disclaimer}
          </p>
        ) : quote ? <p className="strategy-library-note">{quote.margin_disclaimer}</p> : null}
        <label className="futures-ack">
          <input checked={acknowledged} onChange={(event) => setAcknowledged(event.target.checked)} type="checkbox" />
          I understand a price gap can lose more than the stop-loss amount.
        </label>
        <button className="refresh-button" disabled={!canOpen} onClick={() => void openTrade()} type="button">Open paper futures position</button>
      </details>
    </section>
  );
}

export function futuresTradeName(trade: FuturesTrade): string {
  return trade.strategy_name ?? `Manual futures ${trade.side.toLowerCase()}`;
}

export function FuturesPositionCard({ trade, token, apiUrl, onChanged }: {
  trade: FuturesTrade;
  token: string;
  apiUrl: string;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [finalValue, setFinalValue] = useState("");
  const [source, setSource] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const expired = trade.status === "OPEN" && trade.expiry < indiaDate();
  const direction = trade.side === "LONG" ? 1 : -1;
  const settlementEstimate = Number(finalValue) > 0
    ? String((Number(finalValue) - Number(trade.entry_price)) * trade.quantity_units * direction)
    : null;

  async function post(path: string, body?: object) {
    setBusy(true); setError("");
    try {
      const response = await fetch(`${apiUrl}/paper/futures/trades/${trade.id}/${path}`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}`, ...(body ? { "Content-Type": "application/json" } : {}) },
        ...(body ? { body: JSON.stringify(body) } : {}),
      });
      if (!response.ok) { setError(await readError(response)); return; }
      onChanged();
    } finally {
      setBusy(false);
    }
  }

  const pnl = trade.status === "OPEN" ? trade.unrealized_pnl : trade.realized_pnl;
  return (
    <article className={`position-card position-card--${trade.status.toLowerCase()}`}>
      <div className="workspace-section-heading"><strong>{futuresTradeName(trade)}</strong><span className={`trade-state trade-${expired ? "expired" : trade.status.toLowerCase()}`}>{expired ? "EXPIRED · SETTLEMENT PENDING" : trade.status}</span></div>
      <small>{trade.trading_symbol} · Expiry {trade.expiry} · {trade.lots} lot(s) · {trade.side === "LONG" ? "Long" : "Short"}{trade.intraday ? " · Intraday" : ""}</small>
      <div className="position-card-metrics">
        <div><span>Margin {trade.status === "OPEN" ? "blocked" : "released"}</span><strong>{rupees(trade.margin_blocked)}</strong></div>
        <div><span>{trade.status === "OPEN" ? "Marked P&L" : "Realized P&L"}</span><strong className={Number(pnl ?? 0) >= 0 ? "futures-pnl--up" : "futures-pnl--down"}>{signed(pnl)}</strong></div>
        <div className="position-metric--risk"><span>Stop-loss risk budget</span><strong>{rupees(trade.max_loss)}</strong></div>
        <div><span>{trade.status === "OPEN" ? "Last mark" : "Exit price"}</span><strong>{rupees(trade.status === "OPEN" ? trade.last_mark_price : trade.exit_price)}</strong></div>
      </div>
      <small className="position-model-note">Simulated fill at {rupees(trade.entry_price)} · before charges. A gap can lose more than the stop-loss.</small>
      {trade.execution_warnings.length ? <p className="table-warning">{trade.execution_warnings.join(" · ")}</p> : null}
      {trade.exit_reason ? <small>{trade.exit_reason.replaceAll("_", " ")}{trade.closed_at ? ` · ${new Date(trade.closed_at).toLocaleString("en-IN")}` : ""}</small> : null}
      <details><summary>Stops</summary><p>Stop {rupees(trade.stop_loss_amount)} · Target {trade.target_profit_amount ? rupees(trade.target_profit_amount) : "Off"} · Trail {trade.trailing_stop_amount ? rupees(trade.trailing_stop_amount) : "Off"}{trade.last_marked_at ? ` · Marked ${new Date(trade.last_marked_at).toLocaleTimeString("en-IN")}` : " · Waiting for the worker's first mark"}</p></details>
      {trade.status === "OPEN" && !expired ? <button className="inline-button" disabled={busy} onClick={() => void post("close")} type="button">Close paper position</button> : null}
      {expired ? (
        <details open><summary>Review expiry settlement</summary>
          <p>This position passed expiry without a square-off. Record the final index value only after checking it against the official NSE close.</p>
          <div className="strategy-controls futures-controls">
            <label>Final index value<input inputMode="decimal" min={1} type="number" value={finalValue} onChange={(event) => setFinalValue(event.target.value)} /></label>
            <label>Source reference<input maxLength={500} value={source} onChange={(event) => setSource(event.target.value)} /></label>
          </div>
          <p role="status">Settlement P&amp;L at this value: {settlementEstimate === null ? "enter a value" : signed(settlementEstimate)} (simulated, before charges)</p>
          <label className="futures-ack"><input checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} type="checkbox" /> I checked this value and it is final.</label>
          <button className="inline-button" disabled={busy || !confirmed || settlementEstimate === null || source.trim().length < 8} onClick={() => void post("expiry-settlement", { settlement_date: trade.expiry, final_index_value: Number(finalValue).toFixed(2), source_reference: source.trim(), confirmed_final: true })} type="button">Record settlement</button>
        </details>
      ) : null}
      {error ? <p className="error-banner" role="alert">{error}</p> : null}
    </article>
  );
}
