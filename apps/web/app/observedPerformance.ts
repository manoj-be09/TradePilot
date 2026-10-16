// Descriptive paper outcomes only. This does not qualify a strategy for execution.
export type PerformanceTrade = {
  id: string; strategy_key: string; strategy_name: string; underlying: string;
  status: "OPEN" | "CLOSED"; realized_pnl: string | null; max_loss: string;
  opened_at: string; closed_at: string | null;
  stop_loss_amount: string | null; target_profit_amount: string | null; trailing_stop_amount: string | null;
  entry_rule_snapshot: Record<string, unknown>;
  modeled_costs?: {coverage: string; model_id: string; pnl_after_modeled_charges: string | null; recorded_modeled_charges: string | null; complete_broker_costs: boolean} | null;
};

const canonical = (value: unknown): string => {
  if (value == null || typeof value !== "object") return JSON.stringify(value) ?? "null";
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  return `{${Object.entries(value).sort(([a], [b]) => a.localeCompare(b)).map(([key, item]) => `${JSON.stringify(key)}:${canonical(item)}`).join(",")}}`;
};
const cents = (value: string | null): number | null => {
  if (value == null || !/^-?\d+(?:\.\d{1,2})?$/.test(value)) return null;
  const [whole, fraction = ""] = value.split(".");
  const result = Number(`${whole}${fraction.padEnd(2, "0")}`);
  return Number.isSafeInteger(result) ? result : null;
};
const timestamp = (value: string | null) => value && /(?:Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value)) ? Date.parse(value) : null;
export const isTestTrade = (trade: PerformanceTrade) => trade.entry_rule_snapshot?.testing_only === true || /^TEST ONLY\b/i.test(trade.strategy_name);

export function strategyPerformance(trades: PerformanceTrade[]) {
  const groups = new Map<string, PerformanceTrade[]>();
  let excludedTests = 0;
  for (const trade of trades) {
    if (isTestTrade(trade)) { excludedTests++; continue; }
    const entry = trade.entry_rule_snapshot ?? {};
    const key = canonical([trade.strategy_key, trade.underlying, entry.market_data_provider ?? null,
      entry.strategy_definition_hash ?? null, entry.strategy_version_id ?? null, entry.conditions ?? null,
      entry.exit_policy ?? null, trade.stop_loss_amount, trade.target_profit_amount, trade.trailing_stop_amount]);
    const group = groups.get(key) ?? [];
    group.push(trade); groups.set(key, group);
  }
  const rows = [...groups.entries()].map(([key, group]) => {
    const first = group[0];
    const closed = group.filter(trade => trade.status === "CLOSED");
    const priced = closed.map(trade => ({trade, pnl: cents(trade.realized_pnl)})).filter((row): row is {trade: PerformanceTrade; pnl: number} => row.pnl !== null);
    const costRows = closed.filter(trade => trade.modeled_costs?.coverage === "COMPLETE"
      && trade.modeled_costs.complete_broker_costs === false
      && trade.modeled_costs.model_id === "option-premium-turnover-illustrative-v1"
      && cents(trade.modeled_costs.pnl_after_modeled_charges) !== null);
    const afterCharges = costRows.reduce((sum, trade) => sum + (cents(trade.modeled_costs!.pnl_after_modeled_charges) ?? 0), 0);
    const fullCostCoverage = closed.length > 0 && costRows.length === closed.length;
    const wins = priced.filter(row => row.pnl > 0), losses = priced.filter(row => row.pnl < 0);
    const total = priced.reduce((sum, row) => sum + row.pnl, 0);
    const grossWin = wins.reduce((sum, row) => sum + row.pnl, 0);
    const grossLoss = losses.reduce((sum, row) => sum + row.pnl, 0);
    const safe = [total, grossWin, grossLoss].every(Number.isSafeInteger);
    const events = new Map<number, number>();
    let timelineComplete = closed.length === priced.length;
    for (const {trade, pnl} of priced) {
      const at = timestamp(trade.closed_at), opened = timestamp(trade.opened_at);
      if (at === null || opened === null || at < opened) timelineComplete = false;
      else events.set(at, (events.get(at) ?? 0) + pnl);
    }
    let equity = 0, peak = 0, drawdown = 0;
    for (const [, change] of [...events].sort(([a], [b]) => a - b)) {
      equity += change; peak = Math.max(peak, equity); drawdown = Math.max(drawdown, peak - equity);
      if (![equity, peak, drawdown].every(Number.isSafeInteger)) timelineComplete = false;
    }
    const open = group.filter(trade => trade.status === "OPEN");
    const risks = open.map(trade => cents(trade.max_loss));
    const risk = risks.reduce<number>((sum, item) => sum + (item ?? 0), 0);
    const times = group.flatMap(trade => [timestamp(trade.opened_at), timestamp(trade.closed_at)]).filter((at): at is number => at !== null);
    return {key, name: first.strategy_name, underlying: first.underlying,
      provider: String(first.entry_rule_snapshot?.market_data_provider ?? "Unknown provider"),
      version: String(first.entry_rule_snapshot?.strategy_definition_hash ?? first.entry_rule_snapshot?.strategy_version_id ?? "Unversioned"),
      afterModeledCharges: fullCostCoverage && Number.isSafeInteger(afterCharges) ? afterCharges / 100 : null,
      costRecorded: costRows.length,
      stop: first.stop_loss_amount, target: first.target_profit_amount, trail: first.trailing_stop_amount,
      conditions: Array.isArray(first.entry_rule_snapshot?.conditions) ? first.entry_rule_snapshot.conditions.map(item => {
        if (!item || typeof item !== "object") return "Unrecognized condition";
        const rule = item as Record<string, unknown>;
        return [rule.metric, rule.operator, rule.threshold].map(value => typeof value === "string" || typeof value === "number" ? String(value) : "Unknown").join(" ");
      }) : [],
      closed: closed.length, priced: priced.length, missing: closed.length - priced.length,
      wins: wins.length, losses: losses.length, breakeven: priced.length - wins.length - losses.length,
      winRate: priced.length ? wins.length / priced.length * 100 : null,
      pnl: safe && priced.length ? total / 100 : null,
      averageWin: safe && wins.length ? grossWin / wins.length / 100 : null,
      averageLoss: safe && losses.length ? grossLoss / losses.length / 100 : null,
      drawdown: safe && timelineComplete && priced.length ? drawdown / 100 : null,
      open: open.length, openRisk: risks.every(item => item !== null && item >= 0) && Number.isSafeInteger(risk) ? risk / 100 : null,
      from: times.length ? new Date(Math.min(...times)).toISOString() : null,
      to: times.length ? new Date(Math.max(...times)).toISOString() : null,
    };
  }).sort((a, b) => {
    const aDate = timestamp(a.to) ?? Number.NEGATIVE_INFINITY;
    const bDate = timestamp(b.to) ?? Number.NEGATIVE_INFINITY;
    return bDate - aDate || a.name.localeCompare(b.name) || a.key.localeCompare(b.key);
  });
  return {rows, excludedTests};
}

export type EquityPerformanceSummary = {exit_policy?: Record<string, string> | null; exit_policy_version?: string | null; cohort_id?: string; symbol?: string; provider?: string; strategy_version?: string; allocation?: string; risk_budget?: string; quote_mode?: string | null; configuration_evidence?: string; losing_trades?: number; breakeven_trades?: number; average_loss?: string | null; strategy: string; gross_profit: string; gross_loss: string; realized_net: string; closed_trades: number; winning_trades: number; closed_unpriced: number; active_strategies: number};
export function equityPerformance(summaries: EquityPerformanceSummary[]) {
  return summaries.filter(row => row.strategy.trim().toLowerCase() !== "test_pulse").map(row => ({...row,
    winRate: row.closed_trades > 0 ? row.winning_trades / row.closed_trades * 100 : null,
    averageWin: row.winning_trades > 0 ? Number(row.gross_profit) / row.winning_trades : null,
  })).sort((a, b) => a.strategy.localeCompare(b.strategy));
}
