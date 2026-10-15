type Stock = { id: string; trading_symbol: string; provider: string; instrument_key: string };
type Wallet = { cash: string; deployments: { provider: string; instrument_key: string; status: string }[] };
type Research = { stale: boolean; strategies: { key: string; name: string; history_ready: boolean; proposed: unknown }[] };
export type ApplyResult = { stock: string; status: "Applied" | "Skipped" | "Failed"; detail: string };

// Sequential commands keep each cash reservation visible to the next stock.
export async function applyRecommendedStrategies(
  stocks: Stock[], budget: number,
  actions: {
    current: () => boolean;
    wallet: () => Promise<Wallet>;
    research: (id: string) => Promise<Research>;
    deploy: (id: string, strategy: string) => Promise<unknown>;
    report: (result: ApplyResult) => void;
  },
) {
  for (const stock of stocks) {
    if (!actions.current()) break;
    let status: ApplyResult["status"] = "Skipped";
    let detail: string;
    try {
      const wallet = await actions.wallet();
      if (!actions.current()) break;
      const active = wallet.deployments.filter(row =>
        ["WAITING_FOR_ENTRY", "OPEN", "EXIT_REQUESTED"].includes(row.status));
      if (active.some(row => row.provider === stock.provider && row.instrument_key === stock.instrument_key)) {
        detail = "Already has an active strategy";
      } else if (active.length >= 5) {
        detail = "Five active strategies limit reached";
      } else if (Number(wallet.cash) < budget) {
        detail = "Insufficient unreserved paper cash";
      } else {
        const research = await actions.research(stock.id);
        if (!actions.current()) break;
        // The API already orders candidates by evidence tier, score and key.
        const candidate = research.strategies.find(row =>
          row.key !== "test_pulse" && row.history_ready && row.proposed);
        if (research.stale) detail = "Daily history is stale";
        else if (!candidate) detail = "No eligible paper strategy";
        else {
          await actions.deploy(stock.id, candidate.key);
          status = "Applied";
          detail = candidate.name;
        }
      }
    } catch (cause) {
      status = "Failed";
      detail = cause instanceof Error ? cause.message : "Could not apply strategy";
    }
    if (actions.current()) actions.report({ stock: stock.trading_symbol, status, detail });
  }
}
