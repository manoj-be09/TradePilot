"use client";

import { useEffect, useState } from "react";
import { strategyPerformance, type PerformanceTrade } from "./observedPerformance";

const money = (value: number | null) => value === null ? "Unavailable" : !Number.isFinite(value) ? "Unavailable" : new Intl.NumberFormat("en-IN", {style: "currency", currency: "INR", maximumFractionDigits: 2}).format(value);
const day = (value: string | null) => value ? new Date(value).toLocaleDateString("en-IN", {timeZone: "Asia/Kolkata", day: "numeric", month: "short", year: "numeric"}) : "Unknown";

type LedgerReport = {status: "MATCHED" | "MISMATCH"; cash_difference: string; order_count: number; open_trade_count: number; closed_trade_count: number; issue_count: number; issues: string[]};

export function ObservedStrategyPerformance({trades, stale, apiUrl, token}: {trades: PerformanceTrade[]; stale: boolean; apiUrl: string; token: string}) {
  const [ledger, setLedger] = useState<LedgerReport | null>(null);
  const [ledgerError, setLedgerError] = useState("");
  const [checkedAt, setCheckedAt] = useState("");
  const [checking, setChecking] = useState(false);
  const [check, setCheck] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setLedger(null); setLedgerError(""); setCheckedAt("");
    if (check === 0) return () => controller.abort();
    setChecking(true);
    fetch(`${apiUrl}/paper/account/ledger-reconciliation`, {headers: {Authorization: `Bearer ${token}`}, cache: "no-store", signal: controller.signal})
      .then(async response => {
        if (!response.ok) throw new Error("Account ledger check unavailable. No accounting assurance is shown.");
        return response.json() as Promise<LedgerReport>;
      })
      .then(report => { if (!controller.signal.aborted) { setLedger(report); setCheckedAt(new Date().toISOString()); } })
      .catch(() => { if (!controller.signal.aborted) setLedgerError("Account ledger check unavailable. No accounting assurance is shown."); })
      .finally(() => { if (!controller.signal.aborted) setChecking(false); });
    return () => controller.abort();
  }, [apiUrl, token, check]);
  const countsAgree = ledger && ledger.open_trade_count === trades.filter(trade => trade.status === "OPEN").length && ledger.closed_trade_count === trades.filter(trade => trade.status === "CLOSED").length;
  const {rows, excludedTests} = strategyPerformance(trades);
  return <section className="observed-performance" aria-label="Observed strategy performance">
    <div className="trading-dashboard-activity-heading"><div><h3>Strategy performance</h3><p>Recorded F&amp;O paper outcomes · primary metrics before charges</p></div><span className="dashboard-status">Descriptive · not a forecast</span></div>
    <p className="observed-performance-scope">{excludedTests} test-only position(s) excluded. Open marks are excluded from returns. {stale ? "Refresh failed: showing previously loaded records." : "Uses all loaded F&O records; the dashboard strategy filter above does not restrict this table."}</p>
    <details className="workspace-disclosure"><summary>Account accounting evidence</summary>
      <p>Cross-check the complete F&amp;O account, including test trades. This is a separate read-only snapshot, not a per-cohort or quote-quality certification.</p>
      <button type="button" className="inline-button" disabled={checking} onClick={() => setCheck(value => value + 1)}>{checking ? "Checking ledger…" : "Check account ledger"}</button>
      {ledgerError && <p role="alert" className="error-banner">{ledgerError}</p>}
      {ledger && <div role="status"><p><strong>Ledger: {ledger.status}</strong> · Cash difference {money(Number(ledger.cash_difference))} · {ledger.issue_count} issue(s)</p><p>{ledger.order_count} orders · {ledger.open_trade_count} open · {ledger.closed_trade_count} closed · Checked {new Date(checkedAt).toLocaleString("en-IN", {timeZone: "Asia/Kolkata"})} IST</p>{!countsAgree && <p className="error-banner">Trade counts differ from the loaded report. Refresh the dashboard and check again before relying on these results.</p>}{ledger.issues.length > 0 && <ul>{ledger.issues.map((issue, index) => <li key={index}>{issue}</li>)}</ul>}<p>Matching cash arithmetic does not prove complete historical audit coverage, exchange quote freshness or after-cost profitability.</p></div>}
    </details>
    {rows.length === 0 ? <p className="trading-dashboard-empty">No non-test F&amp;O positions recorded yet.</p> : <div className="observed-performance-scroll" role="region" aria-label="Strategy comparison table; scroll horizontally for all metrics" tabIndex={0}><table>
      <caption>Groups separate underlying, provider, saved version, entry conditions and exit settings. Unversioned records may still mix configurations; do not interpret groups as validated comparisons.</caption>
      <thead><tr><th scope="col">Strategy / cohort</th><th scope="col">Closed / priced</th><th scope="col">Recorded P&amp;L</th><th scope="col">Win / loss / flat</th><th scope="col">Observed win rate</th><th scope="col">Average win / loss</th><th scope="col">Closed-trade drawdown</th><th scope="col">Open / max modeled loss</th></tr></thead>
      <tbody>{rows.map(row => <tr key={row.key}>
        <th scope="row"><strong>{row.name}</strong><small>{row.underlying} · {row.provider}</small><small title={row.version}>{row.version === "Unversioned" ? row.version : `Version ${row.version.slice(0, 12)}`}</small><small>{day(row.from)} – {day(row.to)}</small><details><summary>Cohort settings</summary><p>Stop: {row.stop === null ? "Off" : money(Number(row.stop))}<br />Target: {row.target === null ? "Off" : money(Number(row.target))}<br />Trailing: {row.trail === null ? "Off" : money(Number(row.trail))}</p>{row.conditions.length ? <ul>{row.conditions.map((condition, index) => <li key={index}>{condition}</li>)}</ul> : <p>No saved entry conditions.</p>}</details></th>
        <td>{row.closed} / {row.priced}{row.missing > 0 && <small className="change-negative">{row.missing} missing/invalid P&amp;L</small>}</td>
        <td><strong className={row.pnl !== null && row.pnl < 0 ? "change-negative" : ""}>{money(row.pnl)}</strong><small>{row.missing ? "Partial total" : "Before costs"}</small><small>After illustrative charges: {money(row.afterModeledCharges)}</small><small>{row.costRecorded}/{row.closed} closed trades with complete charge records</small></td>
        <td>{row.wins} / {row.losses} / {row.breakeven}</td>
        <td>{row.winRate === null ? "Unavailable" : `${row.winRate.toFixed(1)}%`}<small>Priced closed trades only</small></td>
        <td>{money(row.averageWin)}<small>{money(row.averageLoss)}</small></td>
        <td>{money(row.drawdown)}<small>Realized path only</small></td>
        <td>{row.open}<small>{money(row.openRisk)}</small></td>
      </tr>)}</tbody>
    </table></div>}
    <details className="workspace-disclosure"><summary>Method &amp; evidence limitations</summary><p>P&amp;L is the saved simulated cash-flow result, not a verified after-cost return. New premium fills may carry versioned illustrative charges: 0.10% of absolute premium turnover per leg, rounded per leg to ₹0.01. The adjusted total is shown only with matching opening and closing records for every closed trade in the group. This excludes taxes, statutory/exchange charges, fixed brokerage, market impact and extra slippage; it is not an all-in broker-cost return. Historical missing charges are not reconstructed. Settlement charges are unsupported. Cash balances and risk-rule decisions remain based on existing recorded cash flows. Win rate, average wins/losses and drawdown use original before-charge P&amp;L. Win rate includes breakeven trades in its denominator; it is historical frequency, not a probability forecast. Missing or invalid P&amp;L is excluded and counted.</p><p>Closed-trade drawdown starts at zero and measures declines from the running realized peak, combining closes with the same timestamp. It excludes open-position losses, intraday fluctuations, funding and withdrawals; full account-equity drawdown is unavailable. Missing or inconsistent close timestamps withhold this metric.</p><p>These rows include manual and automatic paper trades and may include indicative quotes. Per-order quote provenance is not verified here. The optional account ledger check is separate from the loaded trade snapshot and does not certify individual cohorts. Small samples and mixed entry dates do not establish an edge, comparable exposure, or future performance. No group is validated by these observations.</p></details>
  </section>;
}
