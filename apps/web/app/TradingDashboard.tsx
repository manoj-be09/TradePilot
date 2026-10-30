"use client";

import { useEffect, useState, type ReactNode } from "react";

type OptionTrade = {
  id: string;
  strategy_name: string;
  status: "OPEN" | "CLOSED";
  realized_pnl: string | null;
  unrealized_pnl: string | null;
  valuation_warning: string | null;
  opened_at: string;
  closed_at: string | null;
};
type FuturesTrade = {
  id: string;
  strategy_name: string | null;
  side: string;
  expiry: string;
  status: "OPEN" | "CLOSED";
  realized_pnl: string | null;
  unrealized_pnl: string | null;
  opened_at: string;
  closed_at: string | null;
};
type OptionDeployment = { id: string; strategy_key: string; strategy_name?: string | null; status: string; trade_id: string | null; created_at: string; detail?: string };
export type HomeAccount = { cash_balance: string; daily_realized_pnl: string; open_trade_count: number; daily_risk_remaining: string };

const money = (value: number | string | null | undefined) => value == null || !Number.isFinite(Number(value))
  ? "Unavailable"
  : new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 2 }).format(Number(value));
const signedMoney = (value: number | string | null | undefined) => {
  const amount = Number(value);
  if (value == null || !Number.isFinite(amount)) return "Unavailable";
  return `${amount > 0 ? "+" : amount < 0 ? "−" : ""}${money(Math.abs(amount))}`;
};
const pnlClass = (value: number | string | null | undefined) => {
  const amount = Number(value);
  return value == null || !Number.isFinite(amount) || amount === 0 ? "" : amount > 0 ? "home-gain" : "home-loss";
};
const time = (value: string) => new Date(value).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });
const label = (value: string) => value.replaceAll("_", " ");
const indiaDate = () => new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(new Date());
const fromFutures = (row: FuturesTrade): OptionTrade => ({
  id: row.id,
  strategy_name: row.strategy_name ?? `Manual futures ${row.side.toLowerCase()}`,
  status: row.status,
  realized_pnl: row.realized_pnl,
  unrealized_pnl: row.unrealized_pnl,
  valuation_warning: row.status === "OPEN" && row.expiry < indiaDate() ? "Expired · settlement pending" : null,
  opened_at: row.opened_at,
  closed_at: row.closed_at,
});

export function TradingDashboard({ token, apiUrl, account, suggestion, onOpenTrade }: {
  token: string;
  apiUrl: string;
  account: HomeAccount | null;
  suggestion: ReactNode;
  onOpenTrade: () => void;
}) {
  const [trades, setTrades] = useState<OptionTrade[] | null>(null);
  const [deployments, setDeployments] = useState<OptionDeployment[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    async function get<T>(path: string): Promise<T> {
      const response = await fetch(`${apiUrl}${path}`, { headers: { Authorization: `Bearer ${token}` }, cache: "no-store", signal: controller.signal });
      if (!response.ok) throw new Error(`Home data unavailable (${response.status})`);
      return response.json() as Promise<T>;
    }
    async function refresh() {
      try {
        const [portfolio, futures, waiting] = await Promise.all([
          get<{ trades: OptionTrade[] }>("/paper/trades"),
          get<FuturesTrade[]>("/paper/futures/trades"),
          get<OptionDeployment[]>("/paper/deployments"),
        ]);
        if (!stopped) { setTrades([...portfolio.trades, ...futures.map(fromFutures)]); setDeployments(waiting); setError(""); }
      } catch (cause) {
        if (!stopped) setError(cause instanceof Error ? cause.message : "Home data unavailable");
      } finally {
        if (!stopped) timer = setTimeout(refresh, 10_000);
      }
    }
    void refresh();
    return () => { stopped = true; controller.abort(); clearTimeout(timer); };
  }, [apiUrl, token]);

  const loaded = trades !== null && deployments !== null;
  const active = [
    ...(deployments ?? []).filter(row => row.status === "WAITING_FOR_ENTRY").map(row => ({
      id: row.id, name: row.strategy_name ?? label(row.strategy_key), state: "Waiting for entry", mark: null as string | null, note: row.detail ?? null, at: row.created_at,
    })),
    ...(trades ?? []).filter(row => row.status === "OPEN").map(row => ({
      id: row.id, name: row.strategy_name, state: "Open", mark: row.valuation_warning ? null : row.unrealized_pnl, note: row.valuation_warning, at: row.opened_at,
    })),
  ];
  const closed = (trades ?? []).filter(row => row.status === "CLOSED" && row.closed_at)
    .sort((left, right) => Date.parse(right.closed_at ?? "") - Date.parse(left.closed_at ?? ""));
  const lastClosed = closed[0];
  const realized = closed.map(row => Number(row.realized_pnl)).filter(Number.isFinite);
  const realizedTotal = realized.reduce((sum, value) => sum + value, 0);
  const wins = realized.filter(value => value > 0).length;

  return <section className="home" aria-label="Paper account home">
    <h2 className="home-title">Your paper account</h2>
    <div className="home-metrics">
      <article><span>Cash</span><strong>{account ? money(account.cash_balance) : "Unavailable"}</strong></article>
      <article><span>Today</span><strong className={pnlClass(account?.daily_realized_pnl)}>{account ? signedMoney(account.daily_realized_pnl) : "Unavailable"}</strong></article>
      <article><span>Open positions</span><strong>{account ? account.open_trade_count : "Unavailable"}</strong></article>
      <article><span>Risk left today</span><strong>{account ? money(account.daily_risk_remaining) : "Unavailable"}</strong></article>
    </div>

    {suggestion}

    <section className="home-card" aria-label="Your strategies">
      <div className="home-card-heading"><h3>Your strategies</h3><button className="refresh-button" onClick={onOpenTrade} type="button">{active.length ? "Manage" : "Add strategy"}</button></div>
      {error ? <p className="home-muted" role="alert">{error}. Showing the last loaded values, if any.</p> : null}
      {!loaded ? <p className="home-muted">{error ? "Strategies unavailable." : "Loading…"}</p>
        : active.length === 0 ? <p className="home-muted">Nothing running. Add a strategy from Trade and it will wait here for its entry signal.</p>
          : <ul className="home-list">{active.map(row => <li key={row.id}>
            <div><strong>{row.name}</strong><small>{row.state} · since {time(row.at)}{row.note ? ` · ${row.note}` : ""}</small></div>
            {row.state === "Open" ? <span className={pnlClass(row.mark)}>{signedMoney(row.mark)}</span> : null}
          </li>)}</ul>}
    </section>

    <section className="home-card" aria-label="Closed trades">
      <div className="home-card-heading"><h3>Closed trades</h3></div>
      {!loaded ? <p className="home-muted">Loading…</p>
        : !lastClosed ? <p className="home-muted">No closed trades yet.</p>
          : <>
            <ul className="home-list"><li>
              <div><strong>{lastClosed.strategy_name}</strong><small>Last closed {time(lastClosed.closed_at ?? lastClosed.opened_at)}</small></div>
              <span className={pnlClass(lastClosed.realized_pnl)}>{signedMoney(lastClosed.realized_pnl)}</span>
            </li></ul>
            <p className="home-muted">All time: <span className={pnlClass(realizedTotal)}>{signedMoney(realizedTotal)}</span> from {realized.length} {realized.length === 1 ? "trade" : "trades"} · {wins} won. Before charges; open positions excluded. Per-strategy results are in Research.</p>
          </>}
    </section>
  </section>;
}
