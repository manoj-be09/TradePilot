"use client";

import { useState } from "react";

export type PerformanceStats = {
  closed_trades: number; winning_trades: number; losing_trades: number; breakeven_trades: number;
  win_rate: number | null; net_pnl: number; average_net_pnl: number | null;
  average_win: number | null; average_loss: number | null; profit_factor: number | null;
  profit_factor_status: "available" | "no_losses" | "no_decisive_trades";
  best_trade: number | null; worst_trade: number | null; closed_trade_drawdown: number;
  average_r: number | null; closed_trade_drawdown_r: number | null; risk_recorded_trades: number;
  first_exit_at: string | null; last_exit_at: string | null;
  open_trade_excluded?: boolean; open_trades_excluded?: number;
  invalid_records_excluded?: number; fallback_exit_dates?: number; strategy_versions?: string[];
  outcomes_without_verified_snapshot?: number; feedback?: PerformanceStats;
};
export type StrategyPerformanceData = {
  historical: PerformanceStats; paper: PerformanceStats; ranking_source: "historical_replay_with_separate_paper_feedback";
};

const money = (value: number | null) => value == null ? "Not available" :
  new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 2 }).format(value);
const when = (value: string) => new Date(value).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });
const riskUnits = (value: number | null) => value == null ? "Not available" : `${value.toFixed(2)}R`;

export function StrategyPerformance({ name, symbol, data, historyFrom, historyTo, asOf, stale }: {
  name: string; symbol: string; data: StrategyPerformanceData;
  historyFrom: string; historyTo: string; asOf: string; stale: boolean;
}) {
  const [source, setSource] = useState<"historical" | "paper">("historical");
  const stats = data[source];
  const hasTrades = stats.closed_trades > 0;
  return <details className="strategy-performance">
    <summary>View performance</summary>
    <section aria-label={`${name} performance for ${symbol}`}>
      <p><strong>{name} · {symbol}</strong></p>
      <div className="performance-switch" role="group" aria-label="Performance source">
        <button type="button" aria-pressed={source === "historical"} onClick={() => setSource("historical")}>Historical replay</button>
        <button type="button" aria-pressed={source === "paper"} onClick={() => setSource("paper")}>Live-paper results</button>
      </div>
      <p className="muted">{source === "historical"
        ? "Main ranking evidence. Simulated trades at the selected allocation and risk settings."
        : "Your recorded paper trades for this stock. Current-version outcomes with a verified close snapshot make a small ranking adjustment; all others remain visible here but do not change the rank."}</p>
      <p><strong>{stats.closed_trades} closed trades</strong> · {stats.winning_trades} wins · {stats.losing_trades} losses · {stats.breakeven_trades} breakeven</p>
      {!hasTrades && <p className="performance-empty" role="status">{source === "paper"
        ? "No closed paper trades yet. Applying or opening a strategy does not count as a completed trade. Results appear after it closes and the report refreshes."
        : "No closed replay trades in this history. Performance is not yet available."}</p>}
      <dl className="performance-metrics">
        <div><dt>Win rate</dt><dd>{stats.win_rate == null ? "Not available" : `${stats.win_rate.toFixed(1)}%`}</dd></div>
        <div><dt>Net P&L</dt><dd>{money(hasTrades ? stats.net_pnl : null)}</dd></div>
        <div><dt>Net expectancy / trade</dt><dd>{money(stats.average_net_pnl)}</dd></div>
        <div><dt>Average return / initial risk</dt><dd>{riskUnits(stats.average_r)}</dd></div>
        <div><dt>Average winning trade</dt><dd>{money(stats.average_win)}</dd></div>
        <div><dt>Average losing trade</dt><dd>{money(stats.average_loss)}</dd></div>
        <div><dt>Profit factor</dt><dd>{stats.profit_factor == null ? "Not available" : stats.profit_factor.toFixed(2)}</dd>{stats.profit_factor_status === "no_losses" && <small>No losing trades; ratio undefined</small>}</div>
        <div><dt>Closed-trade drawdown</dt><dd>{money(hasTrades ? stats.closed_trade_drawdown : null)}</dd></div>
        <div><dt>Best closed trade</dt><dd>{money(stats.best_trade)}</dd></div>
        <div><dt>Worst closed trade</dt><dd>{money(stats.worst_trade)}</dd></div>
      </dl>
      {hasTrades && stats.closed_trades < 20 && <p className="muted">Small sample. These results are not reliable evidence of future profitability.</p>}
      {hasTrades && stats.risk_recorded_trades !== stats.closed_trades && <p className="muted">Original risk is recorded for {stats.risk_recorded_trades} of {stats.closed_trades} trades. Risk-normalized results are unavailable for this sample.</p>}
      <p className="muted">{source === "historical" ? <>
        History: {when(historyFrom)} to {when(historyTo)}. {stale ? "History is stale." : "Completed daily candles only; not a live forecast."}
      </> : stats.first_exit_at && stats.last_exit_at ? <>
        Closed between {when(stats.first_exit_at)} and {when(stats.last_exit_at)}.
      </> : "No completed-trade date range yet."} Updated {when(asOf)}.</p>
      <p className="muted">{source === "historical" ? Number(stats.open_trade_excluded ?? false) : stats.open_trades_excluded ?? 0} open trades excluded. Drawdown measures closed trades only and omits intratrade and unrealized losses.</p>
      {!!stats.invalid_records_excluded && <p className="error-banner">{stats.invalid_records_excluded} closed records have invalid or missing P&L and are excluded.</p>}
      {!!stats.fallback_exit_dates && <p className="muted">{stats.fallback_exit_dates} legacy records use their last saved time because an exit timestamp was not recorded.</p>}
      {source === "paper" && !!stats.outcomes_without_verified_snapshot && <p className="muted">{stats.outcomes_without_verified_snapshot} older or incomplete outcomes are excluded from ranking feedback.</p>}
      <details><summary>How to read these numbers</summary>
        <p>Net expectancy is the historical average net profit or loss per closed trade. Profit factor is total winning net P&L divided by the absolute total losing net P&L. Breakeven trades count in win rate.</p>
        <p>1R is each trade&apos;s original modeled stop loss including costs. A trailing stop does not change that denominator. Gap losses can exceed 1R. Average R requires original risk for every included trade.</p>
        <p>Both sources include modeled costs. Replay uses illustrative 0.10% fees and 0.05% slippage per side. Paper results retain costs recorded at execution. Neither represents actual broker fills.</p>
        <p>Replay and paper periods can overlap and are never pooled. Replay has no corporate-action normalization or out-of-sample validation. Win rate is not a probability of your next trade winning.</p>
        {!!stats.strategy_versions?.length && <p>Recorded paper versions: {stats.strategy_versions.join(", ")}.</p>}
      </details>
    </section>
  </details>;
}
