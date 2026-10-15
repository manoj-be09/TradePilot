import assert from "node:assert/strict";
import { applyRecommendedStrategies } from "../app/applyRecommendedStrategies.ts";

const stocks = Array.from({length: 8}, (_, i) => ({
  id: String(i), trading_symbol: `Stock ${i}`, provider: "upstox", instrument_key: String(i),
}));
const candidate = {key: "trend", name: "Trend", history_ready: true, proposed: {quantity: 1}};
const results = [];
const commands = [];
let cash = 2000;
const deployments = [{provider: "upstox", instrument_key: "0", status: "OPEN"}];
const actions = {
  current: () => true,
  wallet: async () => ({cash: String(cash), deployments}),
  research: async id => {
    if (id === "1") throw new Error("Provider unavailable");
    return {stale: id === "2", strategies: id === "3" ? [] : [
      {...candidate, key: "test_pulse"}, {...candidate, history_ready: false}, candidate,
    ]};
  },
  deploy: async (id, key) => {
    commands.push([id, key]); cash -= 1000;
    deployments.push({provider: "upstox", instrument_key: id, status: "WAITING_FOR_ENTRY"});
  },
  report: result => results.push(result),
};
await applyRecommendedStrategies(stocks, 1000, actions);
assert.deepEqual(commands, [["4", "trend"], ["5", "trend"]]);
assert.deepEqual(results.map(row => row.status), [
  "Skipped", "Failed", "Skipped", "Skipped", "Applied", "Applied", "Skipped", "Skipped",
]);
assert.match(results[2].detail, /stale/);
assert.match(results[6].detail, /cash/);
deployments.push(...[8, 9].map(i => ({provider: "upstox", instrument_key: String(i), status: "OPEN"})));
const capped = [];
await applyRecommendedStrategies([stocks[7]], 1000, {...actions, report: row => capped.push(row)});
assert.match(capped[0].detail, /Five active/);
await applyRecommendedStrategies(stocks, 1000, {...actions, current: () => false,
  wallet: () => assert.fail("Cancelled batch must not request anything")});
console.log("Batch apply: ranking, diagnostics exclusion, active/cash/cap skips, failure continuation and cancellation passed.");
