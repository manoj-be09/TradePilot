// Run: node scripts/check-observed-performance.cjs [path-to-typescript-module]
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const ts = require(process.argv[2] || path.resolve("apps/web/node_modules/typescript"));
const source = fs.readFileSync(path.resolve("apps/web/app/observedPerformance.ts"), "utf8");
const output = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020}}).outputText;
const mod = {exports: {}};
new Function("exports", "module", output)(mod.exports, mod);
const {strategyPerformance} = mod.exports;
const trade = (id, pnl, extra = {}) => ({id, strategy_key: "long_call", strategy_name: "Long call", underlying: "NIFTY", status: "CLOSED", realized_pnl: pnl, max_loss: "100.00", opened_at: "2026-10-10T09:00:00Z", closed_at: `2026-10-${id}T10:00:00Z`, stop_loss_amount: null, target_profit_amount: null, trailing_stop_amount: null, entry_rule_snapshot: {market_data_provider: "upstox"}, ...extra});
let report = strategyPerformance([trade("11", "100.10"), trade("12", "-40.05"), trade("13", "0.00"), trade("14", "-20.00"), trade("15", null), trade("16", "NaN"), trade("17", null, {status: "OPEN", closed_at: null}), trade("18", "9000", {entry_rule_snapshot: {testing_only: true}})]);
assert.equal(report.excludedTests, 1);
let row = report.rows[0];
assert.equal(row.priced, 4); assert.equal(row.closed, 6); assert.equal(row.missing, 2);
assert.equal(row.pnl, 40.05); assert.equal(row.winRate, 25);
assert.equal(row.averageWin, 100.1); assert.equal(row.averageLoss, -30.025);
assert.equal(row.drawdown, null); assert.equal(row.open, 1); assert.equal(row.openRisk, 100);
row = strategyPerformance([trade("11", "100.10"), trade("12", "-40.05"), trade("13", "-20.00")]).rows[0];
assert.equal(row.drawdown, 60.05);
assert.equal(strategyPerformance([trade("11", "-15")]).rows[0].drawdown, 15);
// Simultaneous closes must not acquire an arbitrary drawdown from response ordering.
const sameTime = [trade("11", "100"), trade("12", "-100", {closed_at: "2026-10-11T10:00:00Z"})];
assert.equal(strategyPerformance(sameTime).rows[0].drawdown, 0);
assert.equal(strategyPerformance([...sameTime].reverse()).rows[0].drawdown, 0);
assert.equal(strategyPerformance([trade("11", "1", {closed_at: "bad"})]).rows[0].drawdown, null);
assert.equal(strategyPerformance([trade("11", "1", {closed_at: "2026-10-01T10:00:00Z"})]).rows[0].drawdown, null);
assert.equal(strategyPerformance([trade("11", null, {status: "OPEN", closed_at: null, max_loss: null})]).rows[0].openRisk, null);
assert.equal(strategyPerformance([trade("11", "1", {strategy_name: "TEST ONLY · Expiry"})]).rows.length, 0);
assert.equal(strategyPerformance([trade("11", "0")]).rows[0].winRate, 0);
assert.equal(strategyPerformance([trade("11", "Infinity")]).rows[0].pnl, null);
assert.equal(strategyPerformance([trade("11", "1"), trade("12", "2", {underlying: "BANKNIFTY"}), trade("13", "3", {entry_rule_snapshot: {market_data_provider: "other"}})]).rows.length, 3);
assert.equal(strategyPerformance([trade("11", "1", {target_profit_amount: "10"}), trade("12", "2", {target_profit_amount: "20"})]).rows.length, 2);
const entryA = {conditions: [{metric: "spot", threshold: "1"}], strategy_definition_hash: "abc"};
const entryB = {strategy_definition_hash: "abc", conditions: [{threshold: "1", metric: "spot"}]};
assert.equal(strategyPerformance([trade("11", "1", {entry_rule_snapshot: entryA}), trade("12", "2", {entry_rule_snapshot: entryB})]).rows.length, 1);
assert.deepEqual(strategyPerformance([]), {rows: [], excludedTests: 0});
console.log("Observed-performance checks passed: accounting, exclusions, chronology, ties, cohorts and unavailable evidence.");

const summaries = [
 {strategy: "test_pulse", closed_trades: 99, winning_trades: 99, gross_profit: "999", gross_loss: "0", realized_net: "999", closed_unpriced: 0, active_strategies: 1},
 {strategy: "momentum", closed_trades: 4, winning_trades: 2, gross_profit: "150.50", gross_loss: "-40", realized_net: "110.50", closed_unpriced: 1, active_strategies: 0},
 {strategy: "channel20", closed_trades: 0, winning_trades: 0, gross_profit: "0", gross_loss: "0", realized_net: "0", closed_unpriced: 0, active_strategies: 1},
];
const equity = mod.exports.equityPerformance(summaries);
assert.equal(equity.length, 2); assert.equal(equity[0].strategy, "channel20");
assert.equal(equity[0].winRate, null); assert.equal(equity[0].averageWin, null);
assert.equal(equity[1].winRate, 50); assert.equal(equity[1].averageWin, 75.25);
assert.equal(equity[1].closed_unpriced, 1); assert.equal(equity[1].realized_net, "110.50");
console.log("Equity all-time summary checks passed: diagnostic exclusion, empty samples, averages and missing evidence.");

const modeled = {coverage: "COMPLETE", model_id: "option-premium-turnover-illustrative-v1", pnl_after_modeled_charges: "94.72", recorded_modeled_charges: "5.28", complete_broker_costs: false};
assert.equal(strategyPerformance([trade("11", "100", {modeled_costs: modeled})]).rows[0].afterModeledCharges, 94.72);
assert.equal(strategyPerformance([trade("11", "100", {modeled_costs: modeled}), trade("12", "50")]).rows[0].afterModeledCharges, null);
assert.equal(strategyPerformance([trade("11", "100", {modeled_costs: {...modeled, coverage: "PARTIAL"}})]).rows[0].afterModeledCharges, null);
assert.equal(strategyPerformance([trade("11", "100", {modeled_costs: {...modeled, model_id: "unknown"}})]).rows[0].afterModeledCharges, null);
console.log("Modeled-charge reporting checks passed: complete-only coverage; no legacy reconstruction.");
