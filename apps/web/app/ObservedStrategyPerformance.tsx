"use client";

import { strategyPerformance, type PerformanceTrade } from "./observedPerformance";

const money = (value: number | null) => value === null ? "Unavailable" : !Number.isFinite(value) ? "Unavailable" : new Intl.NumberFormat("en-IN", {style: "currency", currency: "INR", maximumFractionDigits: 2}).format(value);
const day = (value: string | null) => value ? new Date(value).toLocaleDateString("en-IN", {timeZone: "Asia/Kolkata", day: "numeric", month: "short", year: "numeric"}) : "Unknown";

export function ObservedStrategyPerformance({trades, stale}: {trades: PerformanceTrade[]; stale: boolean}) {
  const {rows, excludedTests} = strategyPerformance(trades);
  return <section className="observed-performance" aria-label="Observed strategy performance">
    <div className="trading-dashboard-activity-heading"><div><h3>Strategy performance</h3><p>Recorded F&amp;O paper outcomes · before fees and taxes</p></div><span className="dashboard-status">Descriptive · not a forecast</span></div>
    <p className="observed-performance-scope">{excludedTests} test-only position(s) excluded. Open marks are excluded from returns. {stale ? "Refresh failed: showing previously loaded records." : "Uses all loaded F&O records; the dashboard strategy filter above does not restrict this table."}</p>
    {rows.length === 0 ? <p className="trading-dashboard-empty">No non-test F&amp;O positions recorded yet.</p> : <div className="observed-performance-scroll" role="region" aria-label="Strategy comparison table; scroll horizontally for all metrics" tabIndex={0}><table>
      <caption>Groups separate underlying, provider, saved version, entry conditions and exit settings. Unversioned records may still mix configurations; do not interpret groups as validated comparisons.</caption>
      <thead><tr><th scope="col">Strategy / cohort</th><th scope="col">Closed / priced</th><th scope="col">Recorded P&amp;L</th><th scope="col">Win / loss / flat</th><th scope="col">Observed win rate</th><th scope="col">Average win / loss</th><th scope="col">Closed-trade drawdown</th><th scope="col">Open / max modeled loss</th></tr></thead>
      <tbody>{rows.map(row => <tr key={row.key}>
        <th scope="row"><strong>{row.name}</strong><small>{row.underlying} · {row.provider}</small><small title={row.version}>{row.version === "Unversioned" ? row.version : `Version ${row.version.slice(0, 12)}`}</small><small>{day(row.from)} – {day(row.to)}</small><details><summary>Cohort settings</summary><p>Stop: {row.stop === null ? "Off" : money(Number(row.stop))}<br />Target: {row.target === null ? "Off" : money(Number(row.target))}<br />Trailing: {row.trail === null ? "Off" : money(Number(row.trail))}</p>{row.conditions.length ? <ul>{row.conditions.map((condition, index) => <li key={index}>{condition}</li>)}</ul> : <p>No saved entry conditions.</p>}</details></th>
        <td>{row.closed} / {row.priced}{row.missing > 0 && <small className="change-negative">{row.missing} missing/invalid P&amp;L</small>}</td>
        <td><strong className={row.pnl !== null && row.pnl < 0 ? "change-negative" : ""}>{money(row.pnl)}</strong><small>{row.missing ? "Partial total" : "Before costs"}</small><small>After costs: unavailable</small></td>
        <td>{row.wins} / {row.losses} / {row.breakeven}</td>
        <td>{row.winRate === null ? "Unavailable" : `${row.winRate.toFixed(1)}%`}<small>Priced closed trades only</small></td>
        <td>{money(row.averageWin)}<small>{money(row.averageLoss)}</small></td>
        <td>{money(row.drawdown)}<small>Realized path only</small></td>
        <td>{row.open}<small>{money(row.openRisk)}</small></td>
      </tr>)}</tbody>
    </table></div>}
    <details className="workspace-disclosure"><summary>Method &amp; evidence limitations</summary><p>P&amp;L is the saved simulated cash-flow result, not a verified after-cost return. Brokerage, taxes and a complete cost model are not recorded here. Win rate includes breakeven trades in its denominator; it is historical frequency, not a probability forecast. Missing or invalid P&amp;L is excluded and counted.</p><p>Closed-trade drawdown starts at zero and measures declines from the running realized peak, combining closes with the same timestamp. It excludes open-position losses, intraday fluctuations, funding and withdrawals; full account-equity drawdown is unavailable. Missing or inconsistent close timestamps withhold this metric.</p><p>These rows include manual and automatic paper trades and may include indicative quotes. Per-order quote provenance and ledger reconciliation have not been independently verified by this report. Small samples and mixed entry dates do not establish an edge, comparable exposure, or future performance. No group is validated by these observations.</p></details>
  </section>;
}
