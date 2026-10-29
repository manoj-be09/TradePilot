"use client";

import { useEffect, useState } from "react";

type Stance = "BULLISH" | "BEARISH" | "RANGE" | "VOLATILITY";

type RankedCandidate = {
  strategy_key: string;
  strategy_version: string;
  name: string;
  stance: Stance;
  status: "ELIGIBLE" | "BLOCKED" | "NO_VIEW";
  score: string;
  conviction: string;
  reasons: string[];
  legs: Array<{ option_type: "CE" | "PE"; side: "BUY" | "SELL"; strike: string; quantity_ratio: number; price: string }>;
  net_premium_per_unit: string | null;
  maximum_profit_per_unit: string | null;
  maximum_profit_unbounded: boolean;
  maximum_loss_per_lot: string | null;
  breakevens: string[];
  lot_size: number | null;
  estimated_round_trip_costs_per_lot: string | null;
  costs_verified: boolean;
  reward_to_risk_after_costs: string | null;
  reward_basis: "EXPIRY_MAXIMUM" | "ONE_SIGMA_MOVE" | null;
};

export type RankingResult = {
  ranking_version: string;
  evaluated_at: string;
  chain_as_of: string;
  regime: string;
  atm_iv_pct: string | null;
  realized_vol_pct: string | null;
  specialists: Array<{ stance: Stance; conviction: string; weight: string; reasons: string[] }>;
  action: "PAPER_CANDIDATE" | "NO_TRADE";
  selected_strategy_key: string | null;
  reasons: string[];
  candidates: RankedCandidate[];
};

const STANCE_LABEL: Record<Stance, string> = {
  BULLISH: "Bull specialist",
  BEARISH: "Bear specialist",
  RANGE: "Range specialist",
  VOLATILITY: "Volatility specialist",
};

const STATUS_LABEL: Record<RankedCandidate["status"], string> = {
  ELIGIBLE: "Eligible",
  BLOCKED: "Blocked",
  NO_VIEW: "No specialist view",
};

const money = (value: string | null) =>
  value === null ? "Unavailable" : `₹${Number(value).toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;

export function StrategyRanking({
  token, apiUrl, chain, referenceStrike, onUse,
}: {
  token: string;
  apiUrl: string;
  chain: { as_of: string; expiry: string } | null;
  referenceStrike: string;
  onUse: (strategyKey: string) => void;
}) {
  const [result, setResult] = useState<RankingResult | null>(null);
  const [error, setError] = useState("");
  const [showAll, setShowAll] = useState(false);

  useEffect(() => {
    if (!chain || !referenceStrike) {
      setResult(null);
      return;
    }
    const controller = new AbortController();
    setError("");
    fetch(`${apiUrl}/analytics/options/ranking`, {
      method: "POST",
      signal: controller.signal,
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify({ chain, reference_strike: referenceStrike }),
    })
      .then(async (response) => {
        if (!response.ok) throw new Error(String(response.status));
        return response.json() as Promise<RankingResult>;
      })
      .then((value) => { if (!controller.signal.aborted) setResult(value); })
      .catch(() => { if (!controller.signal.aborted) { setResult(null); setError("Strategy ranking is unavailable."); } });
    return () => controller.abort();
  }, [apiUrl, token, chain, referenceStrike]);

  if (!chain) return null;
  const ranked = result?.candidates.filter((item) => item.status !== "NO_VIEW") || [];
  const shown = showAll ? ranked : ranked.slice(0, 4);
  const selected = result?.selected_strategy_key;

  return (
    <section className="specialist-ranking" aria-label="NIFTY strategy ranking">
      <div className="specialist-ranking-heading">
        <div>
          <strong>Strategy ranking</strong>
          <small>Four market specialists propose · the selector ranks after costs and risk limits · simulated paper only</small>
        </div>
        <span className={`specialist-action specialist-action--${result?.action === "PAPER_CANDIDATE" ? "go" : "wait"}`}>
          {result ? (result.action === "PAPER_CANDIDATE" ? "Paper candidate" : "No trade") : error ? "Unavailable" : "Ranking…"}
        </span>
      </div>
      {error ? <p className="strategy-library-note" role="status">{error}</p> : null}
      {result ? (
        <>
          <p className="specialist-context">
            Regime {result.regime.replaceAll("_", " ")} · ATM IV {result.atm_iv_pct ? `${Number(result.atm_iv_pct).toFixed(1)}%` : "unavailable"} · realized volatility {result.realized_vol_pct ? `${Number(result.realized_vol_pct).toFixed(1)}%` : "unavailable"} · as of {new Date(result.chain_as_of).toLocaleTimeString("en-IN")}
          </p>
          <div className="specialist-grid">
            {result.specialists.map((view) => (
              <article className={`specialist-card specialist-card--${view.stance.toLowerCase()}`} key={view.stance}>
                <span>{STANCE_LABEL[view.stance]}</span>
                <div className="specialist-meter" role="meter" aria-label={`${STANCE_LABEL[view.stance]} conviction`} aria-valuemin={0} aria-valuemax={1} aria-valuenow={Number(view.conviction)}>
                  <i style={{ width: `${Math.round(Number(view.conviction) * 100)}%` }} />
                </div>
                <b>{Math.round(Number(view.conviction) * 100)}% conviction</b>
                <small>{view.reasons.join(" ")}</small>
              </article>
            ))}
          </div>
          <p className="strategy-library-note" role="status">{result.reasons.join(" ")}</p>
          {ranked.length === 0 ? <p className="specialist-empty">No specialist has a view right now, so no strategy is ranked. Ranking resumes when the market regime supports one of the four views.</p> : null}
          <div className="specialist-candidates">
            {shown.map((item, index) => (
              <article className={`specialist-candidate specialist-candidate--${item.status.toLowerCase()} ${item.strategy_key === selected ? "selected" : ""}`} key={item.strategy_key}>
                <div className="specialist-candidate-heading">
                  <strong>{index + 1}. {item.name}</strong>
                  <span>{item.strategy_key === selected ? "Selected" : STATUS_LABEL[item.status]} · score {Number(item.score).toFixed(2)}</span>
                </div>
                <small>{STANCE_LABEL[item.stance]} · {item.legs.map((leg) => `${leg.side} ${leg.quantity_ratio}× ${Number(leg.strike)} ${leg.option_type} @ ${leg.price}`).join(" · ") || "Basket unavailable"}</small>
                <dl className="specialist-metrics">
                  <div><dt>Max loss / lot</dt><dd>{money(item.maximum_loss_per_lot)}</dd></div>
                  <div><dt>Max profit / lot</dt><dd>{item.maximum_profit_unbounded ? "Unbounded" : item.maximum_profit_per_unit && item.lot_size ? money(String(Number(item.maximum_profit_per_unit) * item.lot_size)) : "Unavailable"}</dd></div>
                  <div><dt>Est. costs / lot</dt><dd>{money(item.estimated_round_trip_costs_per_lot)}{item.costs_verified ? "" : "*"}</dd></div>
                  <div><dt>Reward : risk after costs</dt><dd>{item.reward_to_risk_after_costs ? `${Number(item.reward_to_risk_after_costs).toFixed(2)} : 1${item.reward_basis === "ONE_SIGMA_MOVE" ? " at 1σ move" : ""}` : "Unavailable"}</dd></div>
                  <div><dt>Expiry breakeven</dt><dd>{item.breakevens.length ? item.breakevens.map((value) => Number(value).toFixed(2)).join(" / ") : "Unavailable"}</dd></div>
                </dl>
                {item.reasons.length ? <small className="specialist-blocked">{item.reasons.join(" ")}</small> : null}
                <button className="inline-button" disabled={item.status !== "ELIGIBLE"} onClick={() => onUse(item.strategy_key)} type="button">Use in paper ticket</button>
              </article>
            ))}
          </div>
          {ranked.length > 4 ? <button className="inline-button" onClick={() => setShowAll(!showAll)} type="button">{showAll ? "Show top 4" : `Show all ${ranked.length} ranked templates`}</button> : null}
          <p className="strategy-library-note">Scores order templates by specialist conviction and after-cost reward/risk; they are heuristics, not probabilities of profit. * Some charge rates are not verified against an official source. Using a candidate only fills the paper ticket; every entry still passes the paper risk and quote checks.</p>
        </>
      ) : null}
    </section>
  );
}
