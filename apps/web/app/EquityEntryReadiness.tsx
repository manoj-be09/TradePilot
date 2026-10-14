export type EquityEntryState = {
  detail?: string;
  latest_daily_signal_date?: string;
  latest_daily_signal_met?: boolean;
  latest_daily_signal_after_apply?: boolean;
  last_entry_check?: { at: string; detail: string };
};

export function EquityEntryReadiness({ strategy, state, rule }: {
  strategy: string; state: EquityEntryState; rule?: string;
}) {
  const diagnostic = strategy === "test_pulse";
  const pattern = state.latest_daily_signal_met;
  return <div className="entry-readiness">
    {!diagnostic && <p><b>Daily pattern:</b> {pattern === true ? "Met" : pattern === false ? "Not met" : "Not evaluated"}{state.latest_daily_signal_date ? ` · ${state.latest_daily_signal_date}` : ""}</p>}
    {!diagnostic && <p><b>Completed candle after Apply:</b> {state.latest_daily_signal_after_apply === true ? "Yes" : state.latest_daily_signal_after_apply === false ? "No — awaiting a newer completed candle" : "Not evaluated"}</p>}
    <details><summary>Entry criteria &amp; latest checks</summary>
      <p><b>Strategy rule:</b> {diagnostic ? "Needs three distinct qualifying prior quotes within 120 seconds, then a rising price signal. No daily candle is required." : rule ?? "Rule unavailable. Open Strategies to review the catalog before applying."}</p>
      {!diagnostic && <p>The rule uses completed daily candles. Current intraday price above the reference does not satisfy the entire pattern.</p>}
      <p><b>Execution requirements:</b> Open NSE session, matching provider, completed daily history no more than 4 days old, quote freshness under the selected fill policy, valid bid/ask, spread ≤0.5%, enough ask quantity, and sufficient allocation/risk. Ask must be within 2% of the signal close{diagnostic ? " (quote-average reference for this test)" : ""}.</p>
      <p><b>Latest worker state:</b> {state.detail ?? "Not evaluated"}</p>
      <p><b>Last open-session check:</b> {state.last_entry_check ? `${state.last_entry_check.detail} · ${new Date(state.last_entry_check.at).toLocaleString("en-IN")}` : "No saved check. Individual execution gates are not confirmed."}</p>
      <small>Checks may stop at the first blocker. Later gates are not implied to have passed.</small>
    </details>
  </div>;
}
