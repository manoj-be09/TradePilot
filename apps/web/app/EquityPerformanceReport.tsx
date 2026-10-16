"use client";

import { equityPerformance, type EquityPerformanceSummary } from "./observedPerformance";
const money = (value: number | string | null) => value === null || !Number.isFinite(Number(value)) ? "Unavailable" : new Intl.NumberFormat("en-IN", {style: "currency", currency: "INR", maximumFractionDigits: 2}).format(Number(value));

export function EquityPerformanceReport({summaries, stale}: {summaries: EquityPerformanceSummary[]; stale: boolean}) {
  const rows = equityPerformance(summaries);
  const diagnostics = summaries.filter(row => row.strategy.trim().toLowerCase() === "test_pulse");
  const excluded = diagnostics.reduce((sum, row) => sum + row.closed_trades + row.closed_unpriced + row.active_strategies, 0);
  return <section className="observed-performance" aria-label="Observed equity strategy performance">
    <div className="trading-dashboard-activity-heading"><div><h3>Strategy performance</h3><p>All-time equity paper outcomes · recorded results</p></div><span className="dashboard-status">Descriptive · not a forecast</span></div>
    <p>Uses the server&apos;s complete account-scoped totals, independent of the recent 200-deployment activity list. All strategies are shown regardless of the filter above. {excluded} active/closed diagnostic deployment(s) excluded.</p>
    {stale && <p role="alert" className="error-banner">Refresh failed: showing previously loaded totals.</p>}
    {rows.length === 0 ? <p className="trading-dashboard-empty">No non-diagnostic equity strategy results recorded yet.</p> : <div className="observed-performance-scroll" role="region" aria-label="Equity strategy comparison table; scroll horizontally for all metrics" tabIndex={0}><table>
      <caption>Grouped by strategy name across stocks and saved versions. These are descriptive totals, not matched or validated strategy cohorts.</caption>
      <thead><tr><th scope="col">Strategy</th><th scope="col">Priced closes</th><th scope="col">Recorded P&amp;L</th><th scope="col">Winning / losing P&amp;L</th><th scope="col">Observed win rate</th><th scope="col">Average win</th><th scope="col">Missing P&amp;L</th><th scope="col">Active</th></tr></thead>
      <tbody>{rows.map(row => <tr key={row.strategy}><th scope="row">{row.strategy.replaceAll("_", " ")}</th><td>{row.closed_trades}</td><td><strong>{money(row.closed_trades ? row.realized_net : null)}</strong><small>{row.closed_unpriced ? "Partial total" : "Saved execution results"}</small></td><td>{money(row.closed_trades ? row.gross_profit : null)}<small>{money(row.closed_trades ? row.gross_loss : null)}</small></td><td>{row.winRate === null ? "Unavailable" : `${row.winRate.toFixed(1)}%`}<small>{row.winning_trades} wins; breakeven included in denominator</small></td><td>{money(row.averageWin)}</td><td>{row.closed_unpriced}</td><td>{row.active_strategies}</td></tr>)}</tbody>
    </table></div>}
    <details className="workspace-disclosure"><summary>Method &amp; evidence limitations</summary><p>Paper outcomes remain separate from historical replay and exclude unfilled or open positions from P&amp;L. Missing P&amp;L is counted rather than treated as zero. The diagnostic test-pulse strategy is excluded here; account-level figures above may include it.</p><p>Saved equity results retain execution-time cost assumptions, but this aggregate does not certify complete cost provenance for legacy records. A uniform after-cost return is not asserted. The endpoint does not provide loss/breakeven counts, complete close chronology or version-separated risk, so average loss, drawdown, date range and open modeled exposure are unavailable here.</p><p>Win rate is observed historical frequency, not the probability of the next trade winning. Small samples, mixed stocks and mixed versions do not establish an edge. Use the separate Paper wallet check for account accounting evidence; that does not verify quote freshness or profitable future performance.</p></details>
  </section>;
}
