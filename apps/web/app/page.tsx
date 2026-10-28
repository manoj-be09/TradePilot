"use client";

import { FormEvent, useEffect, useMemo, useRef, useState } from "react";

import { WorkerStatus } from "./WorkerStatus";
import { ExpiryTestControl } from "./ExpiryTestControl";
import { InstrumentCatalog } from "./InstrumentCatalog";
import { EquityStrategies } from "./EquityStrategies";
import { OptionDeploymentDecisions } from "./OptionDeploymentDecisions";
import { OptionTradeObservations } from "./OptionTradeObservations";
import { MarketOutlook } from "./MarketOutlook";
import { StrategyRanking } from "./StrategyRanking";
import { BuildUpdateNotice } from "./BuildUpdateNotice";
import { TradingDashboard } from "./TradingDashboard";
import { ChartWorkspace } from "./ChartWorkspace";
import { ResearchWorkspace } from "./ResearchWorkspace";
import { usePaperEntry } from "./usePaperEntry";
import { STRATEGY_CATALOG, STRATEGY_CATALOG_META, StrategyCatalogStatus, StrategyMarketBias } from "./strategyCatalog";

const API_URL = (process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
const MAX_WAITING_PAPER_STRATEGIES = 26;

type TokenResponse = {
  access_token: string;
  token_type: string;
  expires_in: number;
};

type SecurityActivity = {
  id: string;
  event_type: "auth.registration.succeeded" | "auth.login.succeeded" | "auth.password_change.failed" | "auth.password_changed" | "auth.session.revoked" | "auth.session.legacy_upgraded";
  created_at: string;
};

const SECURITY_ACTIVITY_LABELS: Record<SecurityActivity["event_type"], string> = {
  "auth.registration.succeeded": "Account created",
  "auth.login.succeeded": "Signed in",
  "auth.password_change.failed": "Incorrect current password",
  "auth.password_changed": "Password changed",
  "auth.session.revoked": "Session signed out",
  "auth.session.legacy_upgraded": "Session upgraded",
};

type ProviderHealth = {
  provider: "upstox";
  configured: boolean;
  healthy: boolean;
  detail: string;
};

type ProviderOptionContract = {
  instrument_key: string;
  trading_symbol: string;
  expiry: string;
  strike: string;
  option_type: string;
  lot_size: number;
  tick_size: string;
};

type OptionContractQuote = {
  instrument_token: string;
  trading_symbol: string | null;
  expiry: string;
  strike: string;
  option_type: "CE" | "PE" | string;
  quote: {
    instrument_token: string;
    timestamp: string;
    last_price: string;
    bid: string | null;
    ask: string | null;
    volume: number | null;
  };
  open_interest: number | null;
  previous_open_interest: number | null;
  open_interest_change: number | null;
  change: string | number | null;
  bid_quantity: number | null;
  ask_quantity: number | null;
  implied_volatility: string | null;
  greeks: Record<string, string | null>;
};

type OptionChain = {
  underlying: string;
  underlying_spot: string | null;
  expiry: string;
  as_of: string;
  timestamp_basis: string;
  contracts: OptionContractQuote[];
  quality: {
    status: "PASS" | "WARN" | "FAIL";
    checked_at: string;
    source_timestamp_verified: boolean;
    contract_count: number;
    strike_count: number;
    complete_strike_count: number;
    missing_field_count: number;
    crossed_market_count: number;
    abnormal_spread_count: number;
    issues: { code: string; severity: "warning" | "critical"; count: number; message: string }[];
  } | null;
};

type OptionChainFeatures = {
  underlying: string;
  expiry: string;
  as_of: string;
  put_call_ratio_oi: string | null;
  put_call_ratio_volume: string | null;
  call_open_interest: number | null;
  put_open_interest: number | null;
  call_open_interest_change: number | null;
  put_open_interest_change: number | null;
  call_volume: number | null;
  put_volume: number | null;
  atm_strike: string | null;
  atm_call_iv_pct: string | null;
  atm_put_iv_pct: string | null;
  atm_iv_skew_put_minus_call_points: string | null;
  largest_call_oi_strike: string | null;
  largest_put_oi_strike: string | null;
  largest_call_oi_share_pct: string | null;
  largest_put_oi_share_pct: string | null;
  two_sided_quote_count: number;
  median_relative_spread_pct: string | null;
  contracts_with_delta: number;
  contracts_with_gamma: number;
  contracts_with_theta: number;
  contracts_with_vega: number;
  iv_rank_pct: string | null;
  iv_percentile_pct: string | null;
  unavailable: string[];
  methodology: string[];
};

type MarketFitAssessment = {
  selector_version: string;
  chain_source: "CLIENT_SUBMITTED_CHAIN";
  payoff_basis: "PER_OPTION_UNIT_BEFORE_COSTS";
  chain_as_of: string;
  regime_as_of: string | null;
  regime: string;
  action: "NO_TRADE";
  reasons: string[];
  probability_of_profit: null;
  candidates: {
    strategy_key: string;
    strategy_version: string;
    name: string;
    fit: "MATCH" | "UNKNOWN" | "BLOCKED";
    reasons: string[];
    legs: { option_type: "CE" | "PE"; side: "BUY" | "SELL"; strike: string; quantity: number; entry_quote: string; bid: string; ask: string }[];
    maximum_profit: string | null;
    maximum_loss: string | null;
    breakevens: string[];
    provisional_stop: string | null;
    provisional_target: string | null;
    entry_policy: string;
    exit_policy: string;
  }[];
};

type StrikeRow = {
  strike: string;
  call?: OptionContractQuote;
  put?: OptionContractQuote;
};

type SidebarSection = "dashboard" | "charts" | "algo" | "broker" | "backtest" | "bridge" | "research";

type PaperStrategy = {
  key: string;
  version: string;
  name: string;
  description: string;
  legs: { option_type: "CE" | "PE"; side: "BUY" | "SELL"; strike_offset: number; quantity_ratio: number }[];
  closed_trade_count: number;
  winning_trade_count: number;
  qualified_automated_trade_count: number;
  qualified_automated_wins: number;
  qualified_policy_variant_count: number;
  market_condition_evidence: { condition: "BULLISH" | "BEARISH" | "NEUTRAL"; trade_count: number; winning_trade_count: number; average_net_pnl: string | null; average_r: string | null }[];
  observed_win_rate_pct: string | null;
  estimated_win_probability_pct: string | null;
  average_net_pnl: string | null;
  expected_net_pnl: string | null;
};

type CustomLegDraft = {
  option_type: "CE" | "PE";
  side: "BUY" | "SELL";
  strike: string;
  ratio: number;
};

type PreviewDefinition = {
  option_type: "CE" | "PE";
  side: "BUY" | "SELL";
  strike: string | null;
  strike_offset: number | null;
  ratio: number;
};

type PaperAccount = {
  initial_cash: string;
  cash_balance: string;
  kill_switch_enabled: boolean;
  open_trade_count: number;
  open_max_loss: string;
  daily_realized_pnl: string;
  daily_loss_limit: string;
  daily_risk_remaining: string;
  live_trading_enabled: false;
};

type PaperTrade = {
  underlying: string;
  id: string;
  strategy_name: string;
  expiry: string;
  reference_strike: string;
  lots: number;
  lot_size: number;
  max_loss: string;
  entry_cash_flow: string;
  entry_rule_snapshot: {
    strategy_version_id?: string | null;
    strategy_definition_hash?: string | null;
    conditions?: { metric: RuleMetric; operator: RuleOperator; threshold: string }[];
    results?: { metric: RuleMetric; operator: RuleOperator; threshold: string; actual: string | null; passed: boolean | null }[];
    entry_market_context?: {
      version?: string; source?: string; observed_at?: string; available_at?: string; aggregate_scope?: string;
      underlying?: string; underlying_spot?: string; expiry?: string; reference_strike?: string | null;
      calendar_days_to_expiry?: number | null; contracts_observed?: number | null;
      quality_status?: string; source_timestamp_verified?: boolean;
      call_volume?: number | null; put_volume?: number | null; returned_chain_volume?: number | null;
      returned_chain_bid_quantity?: number | null; returned_chain_ask_quantity?: number | null;
      contracts_with_bid_quantity?: number | null; contracts_with_ask_quantity?: number | null;
      call_open_interest?: number | null; put_open_interest?: number | null;
      call_open_interest_change?: number | null; put_open_interest_change?: number | null;
      put_call_ratio_oi?: string | null; put_call_ratio_volume?: string | null;
      atm_call_iv_pct?: string | null; atm_put_iv_pct?: string | null; atm_strike?: string | null;
      atm_iv_skew_put_minus_call_points?: string | null;
      median_relative_spread_pct?: string | null; two_sided_quote_count?: number | null;
      contracts_with_delta?: number | null; contracts_with_gamma?: number | null;
      contracts_with_theta?: number | null; contracts_with_vega?: number | null;
      largest_call_oi_strike?: string | null; largest_put_oi_strike?: string | null;
      quality_issues?: { code: string; severity: string; count: number }[];
      buyer_count?: number | null; seller_count?: number | null;
      news?: { status: string; received_at?: string; coverage_reviewed: boolean;
        items: { heading: string; article_link: string; published_at: string }[] } | null;
      validated_support?: string | null;
      validated_resistance?: string | null; realized_volatility?: string | null;
    };
  };
  stop_loss_amount: string | null;
  target_profit_amount: string | null;
  trailing_stop_amount: string | null;
  peak_unrealized_pnl: string;
  exit_reason: "STOP_LOSS" | "TARGET" | "TRAILING_STOP" | "EXPIRY_SQUARE_OFF" | "MANUAL" | "EXPIRY_SETTLEMENT" | null;
  realized_pnl: string | null;
  unrealized_pnl: string | null;
  status: "OPEN" | "CLOSED";
  opened_at: string;
  closed_at: string | null;
  valuation_warning: string | null;
  execution_warnings: string[];
  legs: {
    trading_symbol: string | null;
    option_type: "CE" | "PE";
    side: "BUY" | "SELL";
    strike: string;
    quantity_units: number;
    entry_price: string;
  }[];
};

function EntryMarketEvidence({ context }: { context: NonNullable<PaperTrade["entry_rule_snapshot"]["entry_market_context"]> }) {
  return <details><summary>Market evidence saved at entry</summary>
    <p>{context.source || "Source unrecorded"} · market time {context.observed_at ? new Date(context.observed_at).toLocaleString("en-IN") : "unavailable"} · server availability {context.available_at ? new Date(context.available_at).toLocaleString("en-IN") : "unavailable"} · quality {context.quality_status || "unknown"} · chain/quote timestamp {context.source_timestamp_verified ? "verified by saved quality check" : "unverified"} (book timing is separate)</p>
    <p>{context.underlying ?? "Underlying unrecorded"} · spot {context.underlying_spot ?? "unavailable"} · expiry {context.expiry ?? "unavailable"} · calendar days remaining {context.calendar_days_to_expiry ?? "unavailable"} · reference strike {context.reference_strike ?? "unavailable"}.</p>
    <p>Returned-chain volume: {context.returned_chain_volume ?? "unavailable"} across {context.contracts_observed ?? "unavailable"} contracts ({context.two_sided_quote_count ?? "unavailable"} with two-sided quotes). Call / put volume: {context.call_volume ?? "unavailable"} / {context.put_volume ?? "unavailable"}; open interest: {context.call_open_interest ?? "unavailable"} / {context.put_open_interest ?? "unavailable"}; OI change: {context.call_open_interest_change ?? "unavailable"} / {context.put_open_interest_change ?? "unavailable"}. These are totals only for returned contracts, not total NIFTY market volume.</p>
    <p>Displayed bid / ask quantity across returned contracts: {context.returned_chain_bid_quantity ?? "unavailable"} / {context.returned_chain_ask_quantity ?? "unavailable"} (coverage {context.contracts_with_bid_quantity ?? "unavailable"} / {context.contracts_with_ask_quantity ?? "unavailable"} of {context.contracts_observed ?? "unavailable"}). These quantities are neither unique buyer/seller counts nor full-market depth.</p>
    <p>Returned-chain put/call ratio by OI: {context.put_call_ratio_oi ?? "unavailable"}; by traded volume: {context.put_call_ratio_volume ?? "unavailable"}. Ratios describe this chain snapshot and do not predict direction by themselves.</p>
    <p>ATM strike {context.atm_strike ?? "unavailable"}; call / put IV {context.atm_call_iv_pct ?? "unavailable"} / {context.atm_put_iv_pct ?? "unavailable"}; put-minus-call IV skew {context.atm_iv_skew_put_minus_call_points ?? "unavailable"} points. Median spread: {context.median_relative_spread_pct ?? "unavailable"}%. Largest call / put OI strikes: {context.largest_call_oi_strike ?? "unavailable"} / {context.largest_put_oi_strike ?? "unavailable"}. OI concentrations are descriptive, not validated support or resistance.</p>
    <p>Contracts with delta / gamma / theta / vega: {context.contracts_with_delta ?? "unavailable"} / {context.contracts_with_gamma ?? "unavailable"} / {context.contracts_with_theta ?? "unavailable"} / {context.contracts_with_vega ?? "unavailable"}. {context.quality_issues ? (context.quality_issues.length ? `Quality issues: ${context.quality_issues.map(issue => `${issue.code} (${issue.count})`).join(", ")}.` : "No quality issues in the saved check.") : "Quality issue codes were not saved in this older snapshot."}</p>
    <p>Buyer/seller counts, realized volatility and validated support/resistance: unavailable in this recorded snapshot. Displayed bid/ask quantities and quote times for selected legs are retained in the open-order fill evidence. These observations are not a validated prediction.</p>
    {context.news ? <div><p>Upstox news: {context.news.status.replaceAll("_", " ").toLowerCase()}; NIFTY index coverage remains unverified. Received {context.news.received_at ? new Date(context.news.received_at).toLocaleString("en-IN") : "time unavailable"}. Articles published after receipt are excluded.</p>
      {context.news.items.length ? <ul>{context.news.items.map(item => <li key={`${item.published_at}:${item.article_link}`}><a href={item.article_link} target="_blank" rel="noreferrer">{item.heading}</a> · published {new Date(item.published_at).toLocaleString("en-IN")}</li>)}</ul> : null}
    </div> : <p>News was not recorded for this older entry.</p>}
  </details>;
}

function PositionMaximumProfit({ trade, token }: { trade: PaperTrade; token: string }) {
  const [profit, setProfit] = useState<string>("Calculating…");
  const legs = JSON.stringify(trade.legs);
  const underlying = trade.underlying;
  useEffect(() => {
    const controller = new AbortController();
    setProfit("Calculating…");
    const savedLegs = JSON.parse(legs) as PaperTrade["legs"];
    fetch(`${API_URL}/analytics/payoff`, {
      method: "POST", signal: controller.signal,
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify({ underlying, multiplier: 1, price_min: "0",
        price_max: String(2 * Math.max(...savedLegs.map(leg => Number(leg.strike)))), grid_points: 2,
        legs: savedLegs.map(leg => ({ option_type: leg.option_type, strike: leg.strike,
          quantity: leg.quantity_units * (leg.side === "BUY" ? 1 : -1), entry_premium: leg.entry_price })),
      }),
    }).then(async response => {
      if (!response.ok) throw new Error("Payoff unavailable");
      return response.json() as Promise<PayoffEstimate>;
    }).then(result => {
      if (!controller.signal.aborted) setProfit(result.maximum_profit_unbounded ? "Unbounded" : result.maximum_profit == null ? "Unavailable" : `₹${formatPrice(result.maximum_profit)}`);
    }).catch(() => { if (!controller.signal.aborted) setProfit("Unavailable"); });
    return () => controller.abort();
  }, [token, underlying, legs]);
  return <div className="position-metric--profit"><span>Max modeled profit</span><strong>{profit}</strong></div>;
}

type RuleMetric =
  | "put_call_ratio_oi"
  | "put_call_ratio_volume"
  | "atm_iv_skew_put_minus_call_points"
  | "largest_call_oi_share_pct"
  | "largest_put_oi_share_pct"
  | "median_relative_spread_pct"
  | "underlying_spot";

type RuleOperator = "GT" | "GTE" | "LT" | "LTE";

type EntryConditionDraft = {
  metric: RuleMetric;
  operator: RuleOperator;
  threshold: string;
};

type StrategyVersion = {
  id: string; name: string; version_number: number; definition_hash: string; created_at: string;
  definition: { option_index?: {symbol: string; instrument_key: string; exchange: string; derivatives_exchange: string} | null; template_key: string; template_version: string; template_legs: PaperStrategy["legs"]; lots: number; entry_conditions: EntryConditionDraft[]; stop_loss_amount: string | null; target_profit_amount: string | null; trailing_stop_amount: string | null };
};

type PaperStrategyPreset = {
  id: string;
  name: string;
  strategy_key: string;
  entry_conditions: EntryConditionDraft[];
  created_at: string;
};

const ENTRY_RULE_METRICS: { key: RuleMetric; label: string; unit: string }[] = [
  { key: "put_call_ratio_oi", label: "Put/call ratio (OI)", unit: "ratio" },
  { key: "put_call_ratio_volume", label: "Put/call ratio (volume)", unit: "ratio" },
  { key: "atm_iv_skew_put_minus_call_points", label: "ATM IV skew (put − call)", unit: "IV points" },
  { key: "largest_call_oi_share_pct", label: "Largest call OI share", unit: "%" },
  { key: "largest_put_oi_share_pct", label: "Largest put OI share", unit: "%" },
  { key: "median_relative_spread_pct", label: "Median option spread", unit: "%" },
  { key: "underlying_spot", label: "NIFTY spot", unit: "points" },
];

function operatorLabel(operator: RuleOperator): string {
  return { GT: ">", GTE: "≥", LT: "<", LTE: "≤" }[operator];
}

function ruleMetricValue(
  metric: RuleMetric,
  features: OptionChainFeatures | null,
  chain: OptionChain | null,
): string | null {
  if (metric === "underlying_spot") return chain?.underlying_spot ?? null;
  if (!features) return null;
  return features[metric];
}

function ruleComparison(
  actual: string | null,
  operator: RuleOperator,
  threshold: string,
): boolean | null {
  if (actual === null || threshold.trim() === "") return null;
  const value = Number(actual);
  const target = Number(threshold);
  if (!Number.isFinite(value) || !Number.isFinite(target)) return null;
  if (operator === "GT") return value > target;
  if (operator === "GTE") return value >= target;
  if (operator === "LT") return value < target;
  return value <= target;
}

type PaperOrder = {
  id: string;
  trade_id: string;
  action: "OPEN" | "CLOSE";
  status: "EXECUTED";
  net_cash_flow: string;
  fill_snapshot: { paper_only: boolean; quality_status: string; method: string };
  created_at: string;
};

type PaperLedgerReconciliation = {
  status: "MATCHED" | "MISMATCH";
  recorded_cash_balance: string;
  expected_cash_balance: string;
  cash_difference: string;
  order_count: number;
  open_trade_count: number;
  closed_trade_count: number;
  issue_count: number;
  issues: string[];
};

type PaperExpirySettlementPreview = {
  trade_id: string;
  settlement_date: string;
  final_index_value: string;
  source_reference: string;
  settlement_cash_flow: string;
  realized_pnl: string;
  legs: { instrument_key: string; option_type: "CE" | "PE"; side: "BUY" | "SELL"; strike: string; quantity_units: number; intrinsic_value: string; cash_flow: string }[];
  warning: string;
};

type PaperExpiryIndexCloseReference = {
  status: "available" | "unavailable";
  trade_id: string;
  expiry: string;
  reason: string | null;
  provider: string | null;
  candle_date: string | null;
  daily_close: string | null;
  warning: string;
};

type PaperOfficialExpirySettlementPreview = {
  status: "available" | "unavailable";
  trade_id: string;
  expiry: string;
  reason: string | null;
  source_url: string | null;
  archive_sha256: string | null;
  retrieved_at: string | null;
  preview: PaperExpirySettlementPreview | null;
  warning: string;
};

type PaperDeployment = {
  id: string; strategy_key: string; expiry: string; reference_strike: string; lots: number;
  entry_conditions: EntryConditionDraft[];
  stop_loss_amount: string | null; target_profit_amount: string | null; trailing_stop_amount: string | null;
  status: "WAITING_FOR_ENTRY" | "FILLED" | "EXPIRED" | "CANCELLED" | "REJECTED";
  detail: string; trade_id: string | null; created_at: string; updated_at: string;
};

type PayoffEstimate = {
  maximum_loss: string | null;
  maximum_loss_unbounded: boolean;
  maximum_profit: string | null;
  maximum_profit_unbounded: boolean;
  transaction_cost_scenario?: {
    model_id: string; source_url: string; entry_charge: string;
    unchanged_premium_exit_charge: string; round_trip_scenario_charge: string;
    limitations: string; complete_broker_costs: boolean;
  } | null;
};

function paperTemplatePolicy(strategyKey: string, payoff: PayoffEstimate) {
  const maximumLoss = Number(payoff.maximum_loss);
  const maximumProfit = Number(payoff.maximum_profit);
  if (!Number.isFinite(maximumLoss) || maximumLoss <= 0 || payoff.maximum_loss_unbounded) return null;
  const stopLoss = Math.max(1, maximumLoss * 0.4);
  const targetProfit = Number.isFinite(maximumProfit) && maximumProfit > 0
    ? Math.max(1, Math.min(maximumProfit * 0.5, stopLoss * 1.75))
    : Math.max(1, stopLoss * 1.5);
  const trailingStop = Math.max(1, targetProfit * 0.35);
  const bullish = new Set(["bull_call_spread", "bull_put_spread", "long_call"]);
  const bearish = new Set(["bear_put_spread", "bear_call_spread", "long_put"]);
  const neutral = new Set(["iron_condor"]);
  const conditions: EntryConditionDraft[] = [
    ...(bullish.has(strategyKey) ? [{ metric: "put_call_ratio_oi" as RuleMetric, operator: "GTE" as RuleOperator, threshold: "1.00" }] : []),
    ...(bearish.has(strategyKey) ? [{ metric: "put_call_ratio_oi" as RuleMetric, operator: "LTE" as RuleOperator, threshold: "1.00" }] : []),
    ...(neutral.has(strategyKey) ? [
      { metric: "put_call_ratio_oi" as RuleMetric, operator: "GTE" as RuleOperator, threshold: "0.80" },
      { metric: "put_call_ratio_oi" as RuleMetric, operator: "LTE" as RuleOperator, threshold: "1.20" },
    ] : []),
    { metric: "median_relative_spread_pct", operator: "LTE", threshold: "10.00" },
  ];
  return { conditions, stopLoss: Number(stopLoss.toFixed(2)), targetProfit: Number(targetProfit.toFixed(2)), trailingStop: Number(trailingStop.toFixed(2)) };
}

type PaperMonitor = {
  checked_at: string;
  evaluated_count: number;
  closed_trade_ids: string[];
  unrealized_pnl: Record<string, string>;
  warnings: string[];
};

type UserNotification = {
  id: string;
  event_type: string;
  title: string;
  message: string;
  details: Record<string, unknown>;
  created_at: string;
  read_at: string | null;
};

type OptionsBacktestResult = {
  strategy_version_id?: string | null;
  strategy_definition_hash?: string | null;
  run_id: string;
  provider: string;
  mode: "historical_single_expiry_replay";
  strategy_key: string;
  strategy_name: string;
  expiry: string;
  entry_date: string;
  exit_date: string;
  entry_reference_spot: string;
  reference_strike: string;
  lot_size: number;
  lots: number;
  gross_pnl: string;
  fee_per_order: string | null;
  slippage_per_unit_per_side: string | null;
  estimated_cost: string | null;
  net_pnl: string | null;
  return_on_risk_net_pct: string | null;
  observed_input_digest: string | null;
  maximum_loss_at_entry: string;
  return_on_risk_pct: string;
  legs: {
    instrument_key: string;
    trading_symbol: string;
    option_type: "CE" | "PE";
    side: "BUY" | "SELL";
    strike: string;
    quantity_units: number;
    entry_timestamp: string;
    entry_price: string;
    exit_timestamp: string;
    exit_price: string;
    gross_pnl: string;
  }[];
  assumptions: string[];
  warnings: string[];
};

type ScheduledBacktestResult = {
  strategy_version_id?: string | null;
  strategy_definition_hash?: string | null;
  run_id: string;
  provider: string;
  mode: "historical_scheduled_expiry_replay";
  strategy_key: string;
  strategy_name: string;
  strategy_version: string;
  start_date: string;
  end_date: string;
  entry_sessions_before_expiry: number;
  lots: number;
  fee_per_order: string;
  slippage_per_unit_per_side: string;
  trade_count: number;
  gross_pnl: string;
  estimated_cost: string;
  net_pnl: string;
  max_closed_trade_drawdown: string;
  winning_trades: number;
  observed_input_digest: string;
  trades: {
    expiry: string;
    entry_date: string;
    exit_date: string;
    gross_pnl: string;
    estimated_cost: string;
    net_pnl: string;
    cumulative_net_pnl: string;
    maximum_loss_at_entry: string;
  }[];
  assumptions: string[];
  warnings: string[];
};

type ScheduledBacktestRunRecord = {
  created_at: string;
  result: ScheduledBacktestResult;
};

type OptionsBacktestRunRecord = {
  created_at: string;
  result: OptionsBacktestResult;
};

async function responseError(response: Response): Promise<string> {
  try {
    const body: { detail?: unknown } = await response.json();
    if (typeof body.detail === "string") return body.detail;
    if (body.detail) return JSON.stringify(body.detail);
  } catch {
    // Keep the HTTP status as a useful fallback for non-JSON responses.
  }
  return `Request failed (HTTP ${response.status})`;
}

async function readJson<T>(response: Response): Promise<T> {
  if (!response.ok) throw new Error(await responseError(response));
  return (await response.json()) as T;
}

function authRequestError(error: unknown): string {
  if (error instanceof TypeError || (error instanceof Error && error.message === "Failed to fetch")) {
    return `Cannot reach the API at ${API_URL}. Start the API, then try again.`;
  }
  return error instanceof Error ? error.message : "Authentication failed";
}

function userFacingError(error: unknown, fallback: string): string {
  const message = error instanceof Error ? error.message : fallback;
  if (message === "Invalid or expired credentials") {
    return "Your app session expired. Sign out and sign in again to reload market data.";
  }
  if (message === "Not Found") {
    return "The API is running an older version. Restart it from this project folder to load the latest paper-trading features.";
  }
  return message || fallback;
}

function formatPrice(value: string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const number = Number(value);
  return Number.isFinite(number)
    ? new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 }).format(number)
    : "—";
}

function formatCount(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 }).format(value);
}

function indiaCalendarDate(): string {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Kolkata", year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(new Date());
  const part = (type: string) => parts.find((item) => item.type === type)?.value || "";
  return `${part("year")}-${part("month")}-${part("day")}`;
}

function formatSignedCount(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${value > 0 ? "+" : ""}${formatCount(value)}`;
}

function formatSignedPrice(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  const formatted = formatPrice(String(Math.abs(number)));
  return number > 0 ? `+${formatted}` : number < 0 ? `−${formatted}` : "0.00";
}

function changeClass(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return "change-neutral";
  const number = Number(value);
  return number > 0 ? "change-positive" : number < 0 ? "change-negative" : "change-neutral";
}

function formatTime(value: string): string {
  const time = new Date(value);
  return Number.isNaN(time.getTime())
    ? "—"
    : new Intl.DateTimeFormat("en-IN", {
        dateStyle: "medium",
        timeStyle: "medium",
      }).format(time);
}

function greekSummary(contract: OptionContractQuote | undefined): string {
  if (!contract) return "";
  const fields = [
    ["Δ", contract.greeks.delta],
    ["Γ", contract.greeks.gamma],
    ["Θ", contract.greeks.theta],
    ["V", contract.greeks.vega],
  ] as const;
  return fields
    .filter(([, value]) => value !== null && value !== undefined)
    .map(([label, value]) => `${label} ${formatPrice(value)}`)
    .join(" · ");
}

export default function Home() {
  const [token, setToken] = useState<string | null>(null);
  const paperEntry = usePaperEntry(token, API_URL);
  const [authReady, setAuthReady] = useState(false);
  const [authMode, setAuthMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [authError, setAuthError] = useState("");
  const [authBusy, setAuthBusy] = useState(false);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [passwordChangeBusy, setPasswordChangeBusy] = useState(false);
  const [passwordChangeError, setPasswordChangeError] = useState("");
  const [securityActivity, setSecurityActivity] = useState<SecurityActivity[]>([]);
  const [securityActivityBusy, setSecurityActivityBusy] = useState(false);
  const [securityActivityError, setSecurityActivityError] = useState("");
  const [securityOpen, setSecurityOpen] = useState(false);
  const [optionIndexes, setOptionIndexes] = useState<{symbol: string; name: string; exchange: string}[]>([{symbol: "NIFTY", name: "Nifty 50", exchange: "NSE"}]);
  const [selectedOptionIndex, setSelectedOptionIndex] = useState("NIFTY");
  const [indexCatalogError, setIndexCatalogError] = useState("");
  const [contracts, setContracts] = useState<ProviderOptionContract[]>([]);
  const [selectedExpiry, setSelectedExpiry] = useState("");
  const [chain, setChain] = useState<OptionChain | null>(null);
  const [optionFeatures, setOptionFeatures] = useState<OptionChainFeatures | null>(null);
  const [marketFit, setMarketFit] = useState<MarketFitAssessment | null>(null);
  const [marketFitError, setMarketFitError] = useState("");
  const [featuresError, setFeaturesError] = useState("");
  const [contractsBusy, setContractsBusy] = useState(false);
  const [chainBusy, setChainBusy] = useState(false);
  const [marketError, setMarketError] = useState("");
  const [connectionStatus, setConnectionStatus] = useState<ProviderHealth | null>(null);
  const [connectionBusy, setConnectionBusy] = useState(false);
  const [connectionError, setConnectionError] = useState("");
  const [refreshCount, setRefreshCount] = useState(0);
  const [paperRefreshCount, setPaperRefreshCount] = useState(0);
  const [positionView, setPositionView] = useState<"OPEN" | "CLOSED" | "ALL">("OPEN");
  const [paperStrategies, setPaperStrategies] = useState<PaperStrategy[]>([]);
  useEffect(() => { setPaperStrategies([]); }, [selectedOptionIndex]);
  const [paperPresets, setPaperPresets] = useState<PaperStrategyPreset[]>([]);
  const [strategyVersions, setStrategyVersions] = useState<StrategyVersion[]>([]);
  const [strategyVersionName, setStrategyVersionName] = useState("");
  const [selectedVersionId, setSelectedVersionId] = useState("");
  const [compareVersionId, setCompareVersionId] = useState("");
  const [versionBusy, setVersionBusy] = useState(false);
  const [versionError, setVersionError] = useState("");
  const [versionMessage, setVersionMessage] = useState("");
  const [presetName, setPresetName] = useState("");
  const [selectedPresetId, setSelectedPresetId] = useState("");
  const [presetBusy, setPresetBusy] = useState(false);
  const [presetError, setPresetError] = useState("");
  const [presetMessage, setPresetMessage] = useState("");
  const [paperAccount, setPaperAccount] = useState<PaperAccount | null>(null);
  const [paperTrades, setPaperTrades] = useState<PaperTrade[]>([]);
  const [paperDeployments, setPaperDeployments] = useState<PaperDeployment[]>([]);
  const [paperOrders, setPaperOrders] = useState<PaperOrder[]>([]);
  const [paperLedger, setPaperLedger] = useState<PaperLedgerReconciliation | null>(null);
  const [paperLedgerCheckedAt, setPaperLedgerCheckedAt] = useState("");
  const [paperLedgerBusy, setPaperLedgerBusy] = useState(false);
  const [paperLedgerError, setPaperLedgerError] = useState("");
  const [walletFundingBusy, setWalletFundingBusy] = useState(false);
  const [walletFundingMessage, setWalletFundingMessage] = useState("");
  const [walletFundingError, setWalletFundingError] = useState("");
  const [paperWarnings, setPaperWarnings] = useState<string[]>([]);
  const [paperError, setPaperError] = useState("");
  const [pendingEntryError, setPendingEntryError] = useState("");
  const [paperBusy, setPaperBusy] = useState(false);
  const [settlementTradeId, setSettlementTradeId] = useState("");
  const [settlementValue, setSettlementValue] = useState("");
  const [settlementSource, setSettlementSource] = useState("");
  const [settlementPreview, setSettlementPreview] = useState<PaperExpirySettlementPreview | null>(null);
  const [indexCloseReference, setIndexCloseReference] = useState<PaperExpiryIndexCloseReference | null>(null);
  const [officialIndexCloseReference, setOfficialIndexCloseReference] = useState<PaperOfficialExpirySettlementPreview | null>(null);
  const [indexCloseBusy, setIndexCloseBusy] = useState(false);
  const [settlementBusy, setSettlementBusy] = useState(false);
  const [settlementError, setSettlementError] = useState("");
  const [notifications, setNotifications] = useState<UserNotification[]>([]);
  const [unreadNotificationCount, setUnreadNotificationCount] = useState(0);
  const [notificationsOpen, setNotificationsOpen] = useState(false);
  const [notificationsError, setNotificationsError] = useState("");
  const [notificationRefreshCount, setNotificationRefreshCount] = useState(0);
  const [markingNotificationId, setMarkingNotificationId] = useState<string | null>(null);
  const notificationWrapRef = useRef<HTMLDivElement | null>(null);
  const notificationButtonRef = useRef<HTMLButtonElement | null>(null);
  const [historicalExpiries, setHistoricalExpiries] = useState<string[]>([]);
  const [historicalExpiriesBusy, setHistoricalExpiriesBusy] = useState(false);
  const [historicalDataError, setHistoricalDataError] = useState("");
  const [historicalExpiry, setHistoricalExpiry] = useState("");
  const [historicalEntryDate, setHistoricalEntryDate] = useState("");
  const [historicalExitDate, setHistoricalExitDate] = useState("");
  const [historicalStrategyKey, setHistoricalStrategyKey] = useState("long_call");
  const [historicalVersionId, setHistoricalVersionId] = useState("");
  const [historicalLots, setHistoricalLots] = useState(1);
  const [historicalBacktest, setHistoricalBacktest] = useState<OptionsBacktestResult | null>(null);
  const [historicalBacktestRuns, setHistoricalBacktestRuns] = useState<OptionsBacktestRunRecord[]>([]);
  const [historicalBacktestHistoryError, setHistoricalBacktestHistoryError] = useState("");
  const [historicalBacktestError, setHistoricalBacktestError] = useState("");
  const [historicalBacktestBusy, setHistoricalBacktestBusy] = useState(false);
  const [scheduleStartExpiry, setScheduleStartExpiry] = useState("");
  const [scheduleEndExpiry, setScheduleEndExpiry] = useState("");
  const [scheduleEntrySessions, setScheduleEntrySessions] = useState(2);
  const [scheduleFee, setScheduleFee] = useState("20");
  const [scheduleSlippage, setScheduleSlippage] = useState("0.50");
  const [scheduledBacktest, setScheduledBacktest] = useState<ScheduledBacktestResult | null>(null);
  const [scheduledRuns, setScheduledRuns] = useState<ScheduledBacktestRunRecord[]>([]);
  const [scheduledError, setScheduledError] = useState("");
  const [scheduledHistoryError, setScheduledHistoryError] = useState("");
  const [scheduledBusy, setScheduledBusy] = useState(false);
  const [payoffEstimate, setPayoffEstimate] = useState<PayoffEstimate | null>(null);
  const [payoffEstimateError, setPayoffEstimateError] = useState("");
  const [payoffEstimateBusy, setPayoffEstimateBusy] = useState(false);
  const [strategyPayoffs, setStrategyPayoffs] = useState<Record<string, PayoffEstimate>>({});
  const [strategyPayoffKey, setStrategyPayoffKey] = useState("");
  const [batchComparison, setBatchComparison] = useState<{
    asOf: string; expiry: string; strike: string; lots: number;
    rows: { key: string; name: string; version: string; status: string; maximumProfit: string; maximumLoss: string; costScenario: string | null }[];
  } | null>(null);
  const [batchArmResults, setBatchArmResults] = useState<{ name: string; result: string }[]>([]);
  const [batchArmContext, setBatchArmContext] = useState("");
  const batchArmCommand = useRef<{ snapshot: string; key: string } | null>(null);
  const [batchPolicyReview, setBatchPolicyReview] = useState<{
    snapshot: string; asOf: string; expiry: string; strike: string; lots: number; quality: string;
    rows: { key: string; name: string; version: string; policy: ReturnType<typeof paperTemplatePolicy>; status: string; maximumLoss: string | null; costScenario: string | null }[];
  } | null>(null);
  const [batchPolicyBusy, setBatchPolicyBusy] = useState(false);
  const [batchPolicyResults, setBatchPolicyResults] = useState<{ name: string; result: string }[]>([]);
  const batchPolicySaveController = useRef<AbortController | null>(null);
  const [paperTicketMessage, setPaperTicketMessage] = useState("");
  const [showAllStrategies, setShowAllStrategies] = useState(false);
  const [strategyBiasFilter, setStrategyBiasFilter] = useState<StrategyMarketBias | "ALL">("ALL");
  const [catalogStatusFilter, setCatalogStatusFilter] = useState<StrategyCatalogStatus | "ALL">("ALL");
  const [catalogSearch, setCatalogSearch] = useState("");
  const [deploymentPreparedKey, setDeploymentPreparedKey] = useState("");
  const deploymentCommand = useRef<{ payload: string; key: string } | null>(null);
  const paperTicketRef = useRef<HTMLParagraphElement | null>(null);
  const [selectedStrategy, setSelectedStrategy] = useState("long_call");
  const [workspace, setWorkspace] = useState<"equities" | "derivatives">("derivatives");
  const [activeSection, setActiveSection] = useState<SidebarSection>("dashboard");
  const [selectedPaperStrike, setSelectedPaperStrike] = useState("");
  const [paperLots, setPaperLots] = useState(1);
  const [stopLossInput, setStopLossInput] = useState("");
  const [targetProfitInput, setTargetProfitInput] = useState("");
  const [trailingStopInput, setTrailingStopInput] = useState("");
  const [customLegDrafts, setCustomLegDrafts] = useState<CustomLegDraft[]>([]);
  const [entryConditions, setEntryConditions] = useState<EntryConditionDraft[]>([]);

  useEffect(() => {
    if (batchPolicyReview && (selectedOptionIndex !== "NIFTY" || selectedExpiry !== batchPolicyReview.expiry
      || batchPolicyReview.snapshot !== `${chain?.as_of}|${chain?.expiry}|${selectedPaperStrike}|${paperLots}`)) {
      batchPolicySaveController.current?.abort();
    }
  }, [batchPolicyReview, chain?.as_of, chain?.expiry, selectedExpiry, selectedOptionIndex, selectedPaperStrike, paperLots]);

  useEffect(() => {
    const savedToken = window.sessionStorage.getItem("market-data-session");
    if (!savedToken) {
      setAuthReady(true);
      return;
    }
    const controller = new AbortController();
    void (async () => {
      try {
        const response = await fetch(`${API_URL}/auth/renew`, {
          method: "POST",
          headers: { Authorization: `Bearer ${savedToken}` },
          cache: "no-store",
          signal: controller.signal,
        });
        if (response.status === 401) {
          window.sessionStorage.removeItem("market-data-session");
          setAuthError("Your session expired. Please sign in again.");
          return;
        }
        const renewed = await readJson<TokenResponse>(response);
        if (!controller.signal.aborted) {
          window.sessionStorage.setItem("market-data-session", renewed.access_token);
          setToken(renewed.access_token);
        }
      } catch {
        // A temporary API outage must not erase a locally saved, potentially valid session.
        if (!controller.signal.aborted) setToken(savedToken);
      } finally {
        if (!controller.signal.aborted) setAuthReady(true);
      }
    })();
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!token) return;
    let active = true;
    const renew = async () => {
      try {
        const response = await fetch(`${API_URL}/auth/renew`, {
          method: "POST",
          headers: { Authorization: `Bearer ${token}` },
          cache: "no-store",
        });
        if (!active) return;
        if (response.status === 401) {
          signOut();
          setAuthError("Your session ended. Please sign in again.");
          return;
        }
        if (!response.ok) return;
        const renewed = await readJson<TokenResponse>(response);
        if (!active) return;
        window.sessionStorage.setItem("market-data-session", renewed.access_token);
        setToken(renewed.access_token);
      } catch {
        // Retry on the next tick or when the tab becomes visible.
      }
    };
    const timer = window.setInterval(() => void renew(), 5 * 60 * 1000);
    const onVisible = () => { if (document.visibilityState === "visible") void renew(); };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      active = false;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [token]);

  useEffect(() => {
    if (!notificationsOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setNotificationsOpen(false);
        notificationButtonRef.current?.focus();
      }
    };
    const closeOnOutsideClick = (event: PointerEvent) => {
      if (event.target instanceof Node && !notificationWrapRef.current?.contains(event.target)) {
        setNotificationsOpen(false);
      }
    };
    window.addEventListener("keydown", closeOnEscape);
    document.addEventListener("pointerdown", closeOnOutsideClick);
    return () => {
      window.removeEventListener("keydown", closeOnEscape);
      document.removeEventListener("pointerdown", closeOnOutsideClick);
    };
  }, [notificationsOpen]);

  useEffect(() => {
    if (!token) return;
    const controller = new AbortController();
    fetch(`${API_URL}/market-data/option-indexes`, {
      headers: { Authorization: `Bearer ${token}` }, signal: controller.signal,
    }).then(readJson<{symbol: string; name: string; exchange: string}[]>).then((items) => {
      if (!controller.signal.aborted) { setOptionIndexes(items); setIndexCatalogError(""); }
    }).catch(() => { if (!controller.signal.aborted) setIndexCatalogError("Index list unavailable. NIFTY remains available."); });
    return () => controller.abort();
  }, [token, refreshCount]);

  useEffect(() => {
    if (!token) return;
    const controller = new AbortController();
    setContractsBusy(true);
    setMarketError("");
    fetch(`${API_URL}/market-data/option-contracts?underlying=${encodeURIComponent(selectedOptionIndex)}`, {
      headers: { Authorization: `Bearer ${token}` },
      signal: controller.signal,
    })
      .then(readJson<ProviderOptionContract[]>)
      .then((items) => {
        setContracts(items);
        const expiries = Array.from(new Set(items.map((item) => item.expiry))).sort();
        setSelectedExpiry((current) => (expiries.includes(current) ? current : expiries[0] || ""));
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setMarketError(userFacingError(error, "Could not load option contracts"));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setContractsBusy(false);
      });
    return () => controller.abort();
  }, [token, refreshCount, selectedOptionIndex]);

  useEffect(() => {
    if (!token) return;
    const controller = new AbortController();
    fetch(`${API_URL}/paper/strategy-presets`, {
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
      signal: controller.signal,
    })
      .then(readJson<PaperStrategyPreset[]>)
      .then((presets) => setPaperPresets(presets))
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setPresetError(userFacingError(error, "Could not load saved presets"));
        }
      });
    return () => controller.abort();
  }, [token]);

  useEffect(() => {
    if (!token) return;
    const controller = new AbortController();
    fetch(`${API_URL}/strategies/versions`, { headers: { Authorization: `Bearer ${token}` }, cache: "no-store", signal: controller.signal })
      .then(readJson<StrategyVersion[]>)
      .then((versions) => setStrategyVersions(versions))
      .catch((error: unknown) => { if (!controller.signal.aborted) setVersionError(userFacingError(error, "Could not load strategy versions")); });
    return () => controller.abort();
  }, [token]);

  useEffect(() => {
    if (!token) return;
    const controller = new AbortController();
    Promise.allSettled([
      fetch(`${API_URL}/paper/strategies?underlying=${encodeURIComponent(selectedOptionIndex)}`, {
        headers: { Authorization: `Bearer ${token}` },
        signal: controller.signal,
      }).then(readJson<PaperStrategy[]>),
      fetch(`${API_URL}/paper/account`, {
        headers: { Authorization: `Bearer ${token}` },
        signal: controller.signal,
      }).then(readJson<PaperAccount>),
      fetch(`${API_URL}/paper/trades`, {
        headers: { Authorization: `Bearer ${token}` },
        signal: controller.signal,
      }).then(readJson<{ trades: PaperTrade[]; warnings: string[] }>),
      fetch(`${API_URL}/paper/orders`, {
        headers: { Authorization: `Bearer ${token}` },
        signal: controller.signal,
      }).then(readJson<PaperOrder[]>),
      fetch(`${API_URL}/paper/deployments`, {
        headers: { Authorization: `Bearer ${token}` },
        cache: "no-store",
        signal: controller.signal,
      }).then(readJson<PaperDeployment[]>),
    ])
      .then(([strategies, account, portfolio, orders, deployments]) => {
        if (controller.signal.aborted) return;
        if (strategies.status === "fulfilled") setPaperStrategies(strategies.value);
        if (account.status === "fulfilled") setPaperAccount(account.value);
        if (portfolio.status === "fulfilled") {
          setPaperTrades(portfolio.value.trades); setPaperWarnings(portfolio.value.warnings);
        }
        if (orders.status === "fulfilled") setPaperOrders(orders.value);
        if (deployments.status === "fulfilled") setPaperDeployments(deployments.value);
        setPaperError([strategies, account, portfolio, orders, deployments].some(result => result.status === "rejected")
          ? "Some paper data could not refresh. Refresh quotes & paper status to retry." : "");
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setPaperError(userFacingError(error, "Could not load the paper workspace"));
        }
      });
    return () => controller.abort();
  }, [token, paperRefreshCount, selectedOptionIndex]);

  useEffect(() => {
    if (!token) {
      setNotifications([]);
      setUnreadNotificationCount(0);
      setNotificationsError("");
      return;
    }
    const controller = new AbortController();
    fetch(`${API_URL}/notifications?limit=25`, {
      headers: { Authorization: `Bearer ${token}` },
      signal: controller.signal,
    })
      .then(readJson<{ notifications: UserNotification[]; unread_count: number }>)
      .then((result) => {
        setNotifications(result.notifications);
        setUnreadNotificationCount(result.unread_count);
        setNotificationsError("");
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setNotificationsError(userFacingError(error, "Could not load notifications"));
        }
      });
    return () => controller.abort();
  }, [token, paperRefreshCount, notificationRefreshCount]);

  useEffect(() => {
    if (!token) {
      setHistoricalExpiries([]);
      setHistoricalExpiry("");
      setHistoricalDataError("");
      return;
    }
    const controller = new AbortController();
    setHistoricalExpiriesBusy(true);
    setHistoricalDataError("");
    fetch(`${API_URL}/market-data/expired-option-expiries`, {
      headers: { Authorization: `Bearer ${token}` },
      signal: controller.signal,
    })
      .then(readJson<string[]>)
      .then((expiries) => {
        if (controller.signal.aborted) return;
        setHistoricalExpiries(expiries);
        setHistoricalExpiry(expiries[0] || "");
        setHistoricalExitDate(expiries[0] || "");
        setHistoricalBacktest(null);
        const recent = expiries.slice(0, 4);
        setScheduleStartExpiry(recent[recent.length - 1] || "");
        setScheduleEndExpiry(recent[0] || "");
        setScheduledBacktest(null);
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setHistoricalDataError(userFacingError(error, "Could not load expired option history"));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setHistoricalExpiriesBusy(false);
      });
    return () => controller.abort();
  }, [token]);

  useEffect(() => {
    if (!token) {
      setHistoricalBacktestRuns([]);
      setHistoricalBacktestHistoryError("");
      return;
    }
    const controller = new AbortController();
    fetch(`${API_URL}/backtests/options/runs?limit=20`, {
      headers: { Authorization: `Bearer ${token}` },
      signal: controller.signal,
    })
      .then(readJson<OptionsBacktestRunRecord[]>)
      .then((runs) => {
        if (!controller.signal.aborted) {
          setHistoricalBacktestRuns(runs);
          setHistoricalBacktestHistoryError("");
        }
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setHistoricalBacktestHistoryError(userFacingError(error, "Could not load saved replays"));
        }
      });
    return () => controller.abort();
  }, [token]);

  useEffect(() => {
    if (!token) {
      setScheduledRuns([]);
      setScheduledHistoryError("");
      return;
    }
    const controller = new AbortController();
    fetch(API_URL + "/backtests/options/scheduled-runs?limit=20", {
      headers: { Authorization: "Bearer " + token },
      signal: controller.signal,
    })
      .then(readJson<ScheduledBacktestRunRecord[]>)
      .then((runs) => {
        if (!controller.signal.aborted) {
          setScheduledRuns(runs);
          setScheduledHistoryError("");
        }
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setScheduledHistoryError(userFacingError(error, "Could not load saved scheduled replays"));
        }
      });
    return () => controller.abort();
  }, [token]);

  useEffect(() => {
    if (!token || !selectedExpiry) {
      setChain(null);
      setOptionFeatures(null);
      return;
    }
    const controller = new AbortController();
    let chainReceived = false;
    setChainBusy(true);
    setMarketError("");
    setFeaturesError("");
    setChain(null);
    setOptionFeatures(null);
    const query = new URLSearchParams({ expiry_date: selectedExpiry, underlying: selectedOptionIndex });
    fetch(`${API_URL}/market-data/option-chain?${query.toString()}`, {
      headers: { Authorization: `Bearer ${token}` },
      signal: controller.signal,
    })
      .then(readJson<OptionChain>)
      .then(async (snapshot) => {
        chainReceived = true;
        if (controller.signal.aborted) return;
        setChain(snapshot);
        setConnectionStatus({
          provider: "upstox",
          configured: true,
          healthy: true,
          detail: "Live option-chain request succeeded",
        });
        setConnectionError("");
        const analysisResponse = await fetch(`${API_URL}/analytics/options/summary`, {
          method: "POST",
          headers: {
            Authorization: `Bearer ${token}`,
            "Content-Type": "application/json",
          },
          body: JSON.stringify(snapshot),
          signal: controller.signal,
        });
        const computed = await readJson<OptionChainFeatures>(analysisResponse);
        if (!controller.signal.aborted) setOptionFeatures(computed);
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          if (!chainReceived) {
            setMarketError(userFacingError(error, "Could not load the option chain"));
          } else {
            setFeaturesError(userFacingError(error, "Could not calculate option features"));
          }
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setChainBusy(false);
      });
    return () => controller.abort();
  }, [token, selectedExpiry, refreshCount, selectedOptionIndex]);

  const expiryOptions = useMemo(
    () => Array.from(new Set(contracts.map((contract) => contract.expiry))).sort(),
    [contracts],
  );

  const strikeRows = useMemo(() => {
    const rows = new Map<string, StrikeRow>();
    for (const contract of chain?.contracts || []) {
      const row = rows.get(contract.strike) || { strike: contract.strike };
      if (contract.option_type === "CE") row.call = contract;
      if (contract.option_type === "PE") row.put = contract;
      rows.set(contract.strike, row);
    }
    return Array.from(rows.values()).sort((left, right) => Number(left.strike) - Number(right.strike));
  }, [chain]);

  useEffect(() => {
    if (strikeRows.length === 0) return;
    if (strikeRows.some((row) => row.strike === selectedPaperStrike)) return;
    const spot = Number(chain?.underlying_spot);
    const nearest = [...strikeRows].sort((left, right) =>
      Math.abs(Number(left.strike) - spot) - Math.abs(Number(right.strike) - spot),
    )[0];
    setSelectedPaperStrike(nearest?.strike || strikeRows[0].strike);
  }, [chain, selectedPaperStrike, strikeRows]);

  useEffect(() => {
    if (!token || !chain || !selectedPaperStrike || selectedOptionIndex !== "NIFTY") {
      setMarketFit(null);
      return;
    }
    const controller = new AbortController();
    setMarketFit(null);
    setMarketFitError("");
    fetch(`${API_URL}/analytics/options/market-fit`, {
      method: "POST",
      signal: controller.signal,
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify({ chain, reference_strike: selectedPaperStrike }),
    }).then(readJson<MarketFitAssessment>)
      .then((result) => { if (!controller.signal.aborted) setMarketFit(result); })
      .catch(() => { if (!controller.signal.aborted) setMarketFitError("Market-fit comparison is unavailable."); });
    return () => controller.abort();
  }, [chain, selectedPaperStrike, selectedOptionIndex, token]);

  const historicalSelectedVersion = strategyVersions.find((version) => version.id === historicalVersionId);
  const historicalVersionUnsupported = !!historicalSelectedVersion && (
    historicalSelectedVersion.definition.entry_conditions.length > 0 ||
    historicalSelectedVersion.definition.stop_loss_amount !== null ||
    historicalSelectedVersion.definition.target_profit_amount !== null ||
    historicalSelectedVersion.definition.trailing_stop_amount !== null
  );
  const pinnedVersion = strategyVersions.find((version) => version.id === selectedVersionId);
  const comparisonVersion = strategyVersions.find((version) => version.id === compareVersionId);
  const versionComparisonRows = pinnedVersion && comparisonVersion ? [
    ["Template", `${comparisonVersion.definition.template_key} @ ${comparisonVersion.definition.template_version}`, `${pinnedVersion.definition.template_key} @ ${pinnedVersion.definition.template_version}`],
    ["Template legs", JSON.stringify(comparisonVersion.definition.template_legs), JSON.stringify(pinnedVersion.definition.template_legs)],
    ["Lots", String(comparisonVersion.definition.lots), String(pinnedVersion.definition.lots)],
    ["Entry conditions", JSON.stringify(comparisonVersion.definition.entry_conditions), JSON.stringify(pinnedVersion.definition.entry_conditions)],
    ["Stop loss", comparisonVersion.definition.stop_loss_amount ?? "Off", pinnedVersion.definition.stop_loss_amount ?? "Off"],
    ["Profit target", comparisonVersion.definition.target_profit_amount ?? "Off", pinnedVersion.definition.target_profit_amount ?? "Off"],
    ["Trailing stop", comparisonVersion.definition.trailing_stop_amount ?? "Off", pinnedVersion.definition.trailing_stop_amount ?? "Off"],
  ].filter(([, before, after]) => before !== after) : [];
  const selectedPaperStrategy = paperStrategies.find((strategy) => strategy.key === selectedStrategy);
  const expiredPaperTrades = paperTrades.filter((trade) =>
    trade.status === "OPEN" && trade.expiry < indiaCalendarDate(),
  );
  const settlementTrade = expiredPaperTrades.find((trade) => trade.id === settlementTradeId);
  const settlementSupported = !!settlementTrade &&
    ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"].includes(settlementTrade.underlying);
  const settlementCloseDifference = officialIndexCloseReference?.status === "available"
    && officialIndexCloseReference.trade_id === settlementTrade?.id
    && officialIndexCloseReference.preview?.final_index_value != null
    && indexCloseReference?.status === "available"
    && indexCloseReference.trade_id === settlementTrade?.id
    && indexCloseReference.daily_close != null
    ? Math.abs(Number(officialIndexCloseReference.preview.final_index_value) - Number(indexCloseReference.daily_close))
    : null;
  const isCustomStrategy = selectedStrategy === "custom";
  const matchingWaitingPaperDeployment = paperDeployments.some((deployment) =>
    deployment.status === "WAITING_FOR_ENTRY"
    && deployment.strategy_key === selectedStrategy
    && deployment.expiry === selectedExpiry
    && Number(deployment.reference_strike) === Number(selectedPaperStrike),
  );
  const hasDuplicateCustomContracts = customLegDrafts.some((leg, index) =>
    customLegDrafts.some((other, otherIndex) =>
      index !== otherIndex && leg.option_type === other.option_type && leg.strike === other.strike,
    ),
  );
  const entryRulePreviews = useMemo(() => entryConditions.map((condition) => {
    const actual = ruleMetricValue(condition.metric, optionFeatures, chain);
    return { ...condition, actual, passed: ruleComparison(actual, condition.operator, condition.threshold) };
  }), [chain, entryConditions, optionFeatures]);
  const entryRuleInputsValid = entryConditions.every(
    (condition) => condition.threshold.trim() !== "" && Number.isFinite(Number(condition.threshold)),
  );
  const entryConditionsSatisfied = entryRulePreviews.every((condition) => condition.passed === true);
  const strategyPreview = useMemo(() => {
    if (!chain || (!isCustomStrategy && (!selectedPaperStrategy || !selectedPaperStrike))) return null;
    const selectedIndex = strikeRows.findIndex((row) => row.strike === selectedPaperStrike);
    if (!isCustomStrategy && selectedIndex < 0) return null;
    const definitions: PreviewDefinition[] = isCustomStrategy
      ? customLegDrafts.map((leg) => ({ ...leg, strike_offset: null }))
      : (selectedPaperStrategy?.legs || []).map((leg) => ({
          option_type: leg.option_type,
          side: leg.side,
          strike: null,
          strike_offset: leg.strike_offset,
          ratio: 1,
        }));
    if (definitions.length === 0) return null;
    let estimatedDebit = 0;
    const legs: {
      side: "BUY" | "SELL";
      option_type: "CE" | "PE";
      strike: string;
      price: number;
      symbol: string;
      quantityLots: number;
    }[] = [];
    for (const leg of definitions) {
      const row = leg.strike
        ? strikeRows.find((item) => item.strike === leg.strike)
        : strikeRows[selectedIndex + (leg.strike_offset || 0)];
      if (!row) return null;
      const option = leg.option_type === "CE" ? row.call : row.put;
      const providerContract = contracts.find(
        (item) => item.expiry === selectedExpiry && item.strike === row.strike && item.option_type === leg.option_type,
      );
      if (!option || !providerContract) return null;
      const price = leg.side === "BUY" ? option.quote.ask : option.quote.bid;
      if (price === null || price === undefined || Number(price) <= 0) return null;
      const quantityLots = paperLots * leg.ratio;
      const units = providerContract.lot_size * quantityLots;
      estimatedDebit += Number(price) * units * (leg.side === "BUY" ? 1 : -1);
      legs.push({
        side: leg.side,
        option_type: leg.option_type,
        strike: row.strike,
        price: Number(price),
        symbol: option.trading_symbol || `${row.strike} ${leg.option_type}`,
        quantityLots,
      });
    }
    return { estimatedDebit, legs };
  }, [chain, contracts, customLegDrafts, isCustomStrategy, paperLots, selectedExpiry, selectedPaperStrategy, selectedPaperStrike, strikeRows]);

  const strategyTilePreviews = useMemo(() => {
    if (!chain || !selectedPaperStrike) return [];
    const selectedIndex = strikeRows.findIndex((row) => row.strike === selectedPaperStrike);
    if (selectedIndex < 0) return [];
    return paperStrategies.flatMap((strategy) => {
      const legs = strategy.legs.flatMap((leg) => {
        const row = strikeRows[selectedIndex + leg.strike_offset];
        const option = leg.option_type === "CE" ? row?.call : row?.put;
        const contract = row ? contracts.find((item) => item.expiry === selectedExpiry && item.strike === row.strike && item.option_type === leg.option_type) : undefined;
        const price = leg.side === "BUY" ? option?.quote.ask : option?.quote.bid;
        if (!row || !option || !contract || price === null || price === undefined || Number(price) <= 0) return [];
        return [{ option_type: leg.option_type, side: leg.side, strike: row.strike, price: Number(price), quantityLots: paperLots * leg.quantity_ratio, lotSize: contract.lot_size }];
      });
      return legs.length === strategy.legs.length ? [{ strategy, legs }] : [];
    });
  }, [chain, contracts, paperLots, paperStrategies, selectedExpiry, selectedPaperStrike, strikeRows]);

  useEffect(() => {
    if (!token || !chain || !strategyTilePreviews.length) { setStrategyPayoffs({}); setStrategyPayoffKey(""); return; }
    const controller = new AbortController();
    setStrategyPayoffs({});
    setStrategyPayoffKey("");
    const snapshotKey = `${chain.as_of}|${chain.expiry}|${selectedPaperStrike}|${paperLots}`;
    const spot = Number(chain.underlying_spot || 1);
    Promise.all(strategyTilePreviews.map(async ({ strategy, legs }) => {
      const prices = legs.map((leg) => Number(leg.strike));
      const response = await fetch(`${API_URL}/analytics/payoff`, {
        method: "POST", signal: controller.signal,
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify({
          underlying: "NIFTY", multiplier: legs[0]?.lotSize || 1, price_min: "0",
          price_max: String(2 * Math.max(spot, ...prices)), grid_points: 2,
          legs: legs.map((leg) => ({ option_type: leg.option_type, strike: leg.strike, quantity: leg.quantityLots * (leg.side === "BUY" ? 1 : -1), entry_premium: String(leg.price) })),
        }),
      });
      return [strategy.key, await readJson<PayoffEstimate>(response)] as const;
    })).then((items) => { if (!controller.signal.aborted) { setStrategyPayoffs(Object.fromEntries(items)); setStrategyPayoffKey(snapshotKey); } })
      .catch(() => { if (!controller.signal.aborted) { setStrategyPayoffs({}); setStrategyPayoffKey(snapshotKey); } });
    return () => controller.abort();
  }, [chain, paperLots, selectedPaperStrike, strategyTilePreviews, token]);

  const rankedStrategies = useMemo(() => {
    return (marketFit?.candidates || []).flatMap((candidate) => {
      const strategy = paperStrategies.find((item) => item.key === candidate.strategy_key && item.version === candidate.strategy_version);
      return strategy ? [{ strategy, candidate, payoff: strategyPayoffs[strategy.key] }] : [];
    });
  }, [marketFit, paperStrategies, strategyPayoffs]);

  const filteredRankedStrategies = useMemo(
    () => rankedStrategies.filter(({ strategy }) => strategyBiasFilter === "ALL" || STRATEGY_CATALOG_META[strategy.key]?.bias === strategyBiasFilter),
    [rankedStrategies, strategyBiasFilter],
  );
  const visibleCatalogEntries = useMemo(() => {
    const query = catalogSearch.trim().toLowerCase();
    const statusRank: Record<StrategyCatalogStatus, number> = { PAPER_SUPPORTED: 0, ENGINE_SUPPORT_PENDING: 1, RESEARCH_ONLY: 2 };
    return STRATEGY_CATALOG
      .filter((entry) => strategyBiasFilter === "ALL" || entry.bias === strategyBiasFilter)
      .filter((entry) => catalogStatusFilter === "ALL" || entry.status === catalogStatusFilter)
      .filter((entry) => !query || `${entry.name} ${entry.summary} ${entry.label}`.toLowerCase().includes(query))
      .sort((left, right) => statusRank[left.status] - statusRank[right.status] || left.name.localeCompare(right.name));
  }, [catalogSearch, catalogStatusFilter, strategyBiasFilter]);
  const catalogStatusCounts = useMemo(() => {
    const counts: Record<StrategyCatalogStatus, number> = { PAPER_SUPPORTED: 0, ENGINE_SUPPORT_PENDING: 0, RESEARCH_ONLY: 0 };
    STRATEGY_CATALOG.filter((entry) => strategyBiasFilter === "ALL" || entry.bias === strategyBiasFilter).forEach((entry) => { counts[entry.status] += 1; });
    return counts;
  }, [strategyBiasFilter]);

  const matchingBatchVersion = (strategy: PaperStrategy) => strategyVersions.filter((version) =>
    version.definition.template_key === strategy.key
    && version.definition.template_version === strategy.version
    && version.definition.lots === paperLots
    && (!version.definition.option_index || version.definition.option_index.symbol === "NIFTY")
    && version.definition.entry_conditions.length > 0
    && !!version.definition.stop_loss_amount
    && !!version.definition.trailing_stop_amount,
  ).sort((left, right) => right.created_at.localeCompare(left.created_at))[0];
  const savedBatchPolicyCount = paperStrategies.filter((strategy) => !!matchingBatchVersion(strategy)).length;
  const batchArmReady = savedBatchPolicyCount > 0 && !!token && selectedOptionIndex === "NIFTY" && paperEntry.ready
    && !paperEntry.pending && !!paperAccount && !paperAccount.kill_switch_enabled
    && !paperBusy && !batchPolicyBusy && !!chain && chain.quality?.status !== "FAIL"
    && !!selectedExpiry && chain.expiry === selectedExpiry
    && selectedExpiry >= indiaCalendarDate() && !!selectedPaperStrike
    && strategyPayoffKey === `${chain.as_of}|${chain.expiry}|${selectedPaperStrike}|${paperLots}`;

  function showPaperPolicyEditor() {
    const editor = document.getElementById("paper-strategy-builder") as HTMLDetailsElement | null;
    if (!editor) return;
    editor.open = true;
    editor.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function reviewNextBatchPolicy() {
    if (selectedOptionIndex !== "NIFTY" || !chain || !selectedPaperStrike
      || selectedExpiry !== chain.expiry || selectedExpiry < indiaCalendarDate()
      || strategyPayoffKey !== `${chain.as_of}|${chain.expiry}|${selectedPaperStrike}|${paperLots}`) {
      setPaperError("Wait for the current option-chain payoff preview before reviewing batch policies.");
      return;
    }
    const next = paperStrategies.find((strategy) => {
      const payoff = strategyPayoffs[strategy.key];
      return !matchingBatchVersion(strategy) && !!payoff && !!paperTemplatePolicy(strategy.key, payoff);
    });
    const payoff = next && strategyPayoffs[next.key];
    if (!next || !payoff) {
      setPaperError("No additional template has a finite quote-backed loss to prepare. Refresh the chain and review unavailable templates.");
      return;
    }
    showPaperPolicyEditor();
    applyStrategyToPaperTicket(next, payoff);
    setDeploymentPreparedKey("");
    setStrategyVersionName(`Paper batch study · ${next.name}`);
    setPaperTicketMessage(`Review the proposed ${next.name} entry filter, stop, target and trailing exit below, then save its immutable version. These are exploratory rules, not validated market-fit or profit predictions. Saving does not arm a trade.`);
    const versions = document.getElementById("strategy-version-panel") as HTMLDetailsElement | null;
    if (versions) versions.open = true;
    window.setTimeout(() => versions?.scrollIntoView({ behavior: "smooth", block: "center" }), 0);
  }

  function reviewAllBatchPolicies() {
    if (selectedOptionIndex !== "NIFTY" || !chain || !selectedPaperStrike
      || selectedExpiry !== chain.expiry || selectedExpiry < indiaCalendarDate()
      || strategyPayoffKey !== `${chain.as_of}|${chain.expiry}|${selectedPaperStrike}|${paperLots}`) {
      setPaperError("Wait for the current option-chain payoff preview before reviewing batch policies.");
      return;
    }
    const quoted = new Set(strategyTilePreviews.map(({ strategy }) => strategy.key));
    setBatchPolicyReview({
      snapshot: strategyPayoffKey, asOf: chain.as_of, expiry: chain.expiry,
      strike: selectedPaperStrike, lots: paperLots, quality: chain.quality?.status || "UNKNOWN",
      rows: paperStrategies.map((strategy) => {
        const payoff = strategyPayoffs[strategy.key];
        const saved = matchingBatchVersion(strategy);
        const policy = !saved && quoted.has(strategy.key) && chain.quality?.status !== "FAIL" && payoff
          ? paperTemplatePolicy(strategy.key, payoff) : null;
        return {
          key: strategy.key, name: strategy.name, version: strategy.version, policy,
          status: saved ? `Saved version ${saved.version_number} already matches`
            : !quoted.has(strategy.key) ? "Required listed leg or quote unavailable"
            : chain.quality?.status === "FAIL" ? "Failed quote quality; refresh before review"
            : !payoff ? "Payoff unavailable"
            : !policy ? "Finite maximum loss unavailable" : "Exploratory proposal; review before saving",
          maximumLoss: payoff?.maximum_loss ?? null,
          costScenario: payoff?.transaction_cost_scenario?.round_trip_scenario_charge ?? null,
        };
      }),
    });
    setBatchPolicyResults([]);
    setPaperError("");
  }

  async function saveReviewedBatchPolicies() {
    if (!token || !batchPolicyReview || batchPolicyBusy || paperBusy
      || selectedOptionIndex !== "NIFTY" || selectedExpiry !== batchPolicyReview.expiry
      || selectedExpiry < indiaCalendarDate()
      || batchPolicyReview.snapshot !== strategyPayoffKey
      || batchPolicyReview.snapshot !== `${chain?.as_of}|${chain?.expiry}|${selectedPaperStrike}|${paperLots}`) return;
    const proposals = batchPolicyReview.rows.filter((row) => row.policy);
    if (!proposals.length) return;
    const controller = new AbortController();
    batchPolicySaveController.current = controller;
    setBatchPolicyBusy(true);
    setBatchPolicyResults([]);
    const results: { name: string; result: string }[] = [];
    const approvedVersions: StrategyVersion[] = [];
    let saveComplete = true;
    try {
      const existing = await readJson<StrategyVersion[]>(await fetch(`${API_URL}/strategies/versions?limit=100`, {
        headers: { Authorization: `Bearer ${token}` }, cache: "no-store", signal: controller.signal,
      }));
      for (const row of proposals) {
        if (controller.signal.aborted) break;
        const policy = row.policy!;
        const name = `Batch study · ${row.name} · ${row.version} · ${batchPolicyReview.lots}L`;
        const prior = existing.find((version) => version.name === name);
        if (prior) {
          const unchanged = prior.definition.template_key === row.key
            && prior.definition.template_version === row.version
            && prior.definition.lots === batchPolicyReview.lots
            && JSON.stringify(prior.definition.entry_conditions) === JSON.stringify(policy.conditions)
            && Number(prior.definition.stop_loss_amount) === policy.stopLoss
            && Number(prior.definition.target_profit_amount) === policy.targetProfit
            && Number(prior.definition.trailing_stop_amount) === policy.trailingStop;
          results.push({ name: row.name, result: unchanged
            ? "Identical version already saved; no duplicate created"
            : "Stopped: a version with this name differs; review it individually before retrying" });
          setBatchPolicyResults([...results]);
          if (!unchanged) { saveComplete = false; break; }
          approvedVersions.push(prior);
          continue;
        }
        try {
          const version = await readJson<StrategyVersion>(await fetch(`${API_URL}/strategies/versions`, {
            method: "POST", signal: controller.signal,
            headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
            body: JSON.stringify({
              name, strategy_key: row.key, expected_template_version: row.version,
              lots: batchPolicyReview.lots,
              entry_conditions: policy.conditions, stop_loss_amount: policy.stopLoss,
              target_profit_amount: policy.targetProfit, trailing_stop_amount: policy.trailingStop,
            }),
          }));
          if (controller.signal.aborted) break;
          existing.unshift(version);
          approvedVersions.push(version);
          setStrategyVersions((current) => [version, ...current.filter((item) => item.id !== version.id)]);
          results.push({ name: row.name, result: `Saved immutable version ${version.version_number}` });
          setBatchPolicyResults([...results]);
        } catch (error) {
          if (!controller.signal.aborted) {
            saveComplete = false;
            results.push({ name: row.name, result: `Stopped: save status may be uncertain. Refresh versions before retrying. ${userFacingError(error, "Policy save failed")}` });
            setBatchPolicyResults([...results]);
          }
          break;
        }
      }
      if (!controller.signal.aborted && saveComplete && approvedVersions.length === proposals.length) {
        setBatchPolicyResults((current) => [...current, {
          name: "Batch", result: "All reviewed policies saved; applying eligible strategies in one batch action",
        }]);
        await armAllPaperTemplates(approvedVersions);
      }
    } catch (error) {
      if (!controller.signal.aborted) setPaperError(userFacingError(error, "Could not verify existing policy versions"));
    } finally {
      if (batchPolicySaveController.current === controller) batchPolicySaveController.current = null;
      setBatchPolicyBusy(false);
    }
  }

  function compareAllPaperTemplates() {
    if (!chain || !selectedPaperStrike || strategyPayoffKey !== `${chain.as_of}|${chain.expiry}|${selectedPaperStrike}|${paperLots}`) return;
    setBatchComparison({
      asOf: chain.as_of, expiry: chain.expiry, strike: selectedPaperStrike, lots: paperLots,
      rows: paperStrategies.map((strategy) => {
        const quoted = strategyTilePreviews.some((preview) => preview.strategy.key === strategy.key);
        const payoff = strategyPayoffs[strategy.key];
        return {
          key: strategy.key, name: strategy.name, version: strategy.version,
          status: !quoted ? "Required quote or listed leg unavailable" : !payoff ? "Payoff unavailable"
            : chain.quality?.status !== "PASS" ? "Snapshot quality unverified or failed"
            : payoff.maximum_loss_unbounded ? "Unbounded loss · blocked"
            : payoff.maximum_loss === null ? "Maximum loss unavailable" : "Gross expiry preview only",
          maximumProfit: payoff?.maximum_profit_unbounded ? "Unbounded" : payoff?.maximum_profit ?? "Unavailable",
          maximumLoss: payoff?.maximum_loss_unbounded ? "Unbounded" : payoff?.maximum_loss ?? "Unavailable",
          costScenario: payoff?.transaction_cost_scenario?.round_trip_scenario_charge ?? null,
        };
      }),
    });
  }

  async function armAllPaperTemplates(approvedVersions?: StrategyVersion[]) {
    const proposalBatchReady = !!token && selectedOptionIndex === "NIFTY" && paperEntry.ready
      && !paperEntry.pending && !!paperAccount && !paperAccount.kill_switch_enabled
      && !paperBusy && !!chain && chain.quality?.status !== "FAIL"
      && !!selectedExpiry && chain.expiry === selectedExpiry
      && selectedExpiry >= indiaCalendarDate() && !!selectedPaperStrike
      && strategyPayoffKey === `${chain.as_of}|${chain.expiry}|${selectedPaperStrike}|${paperLots}`;
    if ((!approvedVersions && !batchArmReady) || (approvedVersions && !proposalBatchReady)
      || !token || !chain || !selectedExpiry || !selectedPaperStrike) return;
    setPaperBusy(true);
    setPaperError("");
    setBatchArmResults([]);
    const snapshot = `${chain.as_of}|${selectedExpiry}|${selectedPaperStrike}|${paperLots}`;
    setBatchArmContext(`${new Date(chain.as_of).toLocaleString("en-IN")} · expiry ${selectedExpiry} · strike ${selectedPaperStrike} · ${paperLots} lot(s)`);
    if (batchArmCommand.current?.snapshot !== snapshot) {
      let previous: { snapshot: string; key: string } | null = null;
      try { previous = JSON.parse(window.sessionStorage.getItem("paper-batch-arm") || "null"); } catch { /* Start a new command if the saved value is invalid. */ }
      batchArmCommand.current = previous?.snapshot === snapshot && /^[a-f0-9]{32}$/.test(previous.key) ? previous : { snapshot, key: crypto.randomUUID().replaceAll("-", "") };
      window.sessionStorage.setItem("paper-batch-arm", JSON.stringify(batchArmCommand.current));
    }
    const results: { name: string; result: string }[] = [];
    const approvedByKey = new Map((approvedVersions || []).map((version) => [version.definition.template_key, version]));
    const quoted = new Set(strategyTilePreviews.map(({ strategy }) => strategy.key));
    const waiting = new Set(paperDeployments.filter(item => item.status === "WAITING_FOR_ENTRY" && item.expiry === selectedExpiry && item.reference_strike === selectedPaperStrike).map(item => item.strategy_key));
    let slots = Math.max(0, MAX_WAITING_PAPER_STRATEGIES - paperDeployments.filter(item => item.status === "WAITING_FOR_ENTRY").length);
    try {
      for (const strategy of paperStrategies) {
        const payoff = strategyPayoffs[strategy.key];
        const hasFiniteLoss = payoff && Number(payoff.maximum_loss) > 0 && Number.isFinite(Number(payoff.maximum_loss)) && !payoff.maximum_loss_unbounded;
        const saved = approvedByKey.get(strategy.key) || matchingBatchVersion(strategy);
        let result: string;
        if (waiting.has(strategy.key)) result = "Already waiting for entry";
        else if (!quoted.has(strategy.key)) result = "Skipped: required listed leg or quote unavailable";
        else if (!hasFiniteLoss) result = "Skipped: finite maximum loss unavailable";
        else if (!saved) result = "Skipped: save a reviewed matching entry and exit policy first";
        else if (!slots) result = "Skipped: 26 waiting-strategy limit reached";
        else {
          try {
            const armed = await readJson<PaperDeployment>(await fetch(`${API_URL}/paper/deployments`, {
              method: "POST",
              headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json", "Idempotency-Key": `batch_${batchArmCommand.current!.key}_${strategy.key}` },
              body: JSON.stringify({
                strategy_key: strategy.key, strategy_version_id: saved.id, expected_template_version: strategy.version,
                expiry: selectedExpiry, reference_strike: selectedPaperStrike, lots: paperLots,
                entry_conditions: saved.definition.entry_conditions,
                stop_loss_amount: saved.definition.stop_loss_amount,
                target_profit_amount: saved.definition.target_profit_amount,
                trailing_stop_amount: saved.definition.trailing_stop_amount,
              }),
            }));
            setPaperDeployments(current => [armed, ...current.filter(item => item.id !== armed.id)]);
            if (armed.status === "WAITING_FOR_ENTRY") slots -= 1;
            result = armed.status === "WAITING_FOR_ENTRY" ? "Armed; waiting for a later qualifying paper snapshot" : `Already submitted · ${armed.status.replaceAll("_", " ")}`;
          } catch (error) {
            result = `Not armed: ${userFacingError(error, "Paper safety check failed")}`;
          }
        }
        results.push({ name: strategy.name, result });
        setBatchArmResults([...results]);
      }
      await refreshPaperWorkspace();
    } finally {
      setPaperBusy(false);
    }
  }

  function applyStrategyToPaperTicket(strategy: PaperStrategy, payoff: PayoffEstimate) {
    const policy = paperTemplatePolicy(strategy.key, payoff);
    if (!policy) {
      setPaperTicketMessage("This strategy cannot be prepared because a finite maximum loss is unavailable.");
      return;
    }
    setSelectedStrategy(strategy.key);
    if (optionFeatures?.atm_strike) setSelectedPaperStrike(optionFeatures.atm_strike);
    setEntryConditions(policy.conditions);
    setStopLossInput(policy.stopLoss.toFixed(2));
    setTargetProfitInput(policy.targetProfit.toFixed(2));
    setTrailingStopInput(policy.trailingStop.toFixed(2));
    setDeploymentPreparedKey(strategy.key);
    setSelectedVersionId("");
    setCompareVersionId("");
    setVersionError("");
    setVersionMessage("");
    setPaperError("");
    setPaperTicketMessage(`${strategy.name} is ready to arm in paper mode. Entry filters, a ₹${policy.stopLoss.toFixed(2)} protective stop, ₹${policy.targetProfit.toFixed(2)} target and ₹${policy.trailingStop.toFixed(2)} trailing drawdown were generated from the current modeled risk. Review them before arming.`);
    window.setTimeout(() => paperTicketRef.current?.scrollIntoView({ behavior: "smooth", block: "center" }), 0);
  }

  useEffect(() => {
    if (!token || !chain || !strategyPreview) {
      setPayoffEstimate(null);
      setPayoffEstimateError("");
      setPayoffEstimateBusy(false);
      return;
    }
    const controller = new AbortController();
    const prices = strategyPreview.legs.map((leg) => Number(leg.strike));
    const spot = Number(chain.underlying_spot || 1);
    setPayoffEstimate(null);
    setPayoffEstimateError("");
    setPayoffEstimateBusy(true);
    fetch(`${API_URL}/analytics/payoff`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      signal: controller.signal,
      body: JSON.stringify({
        underlying: "NIFTY",
        multiplier: contracts.find(
          (contract) => contract.expiry === selectedExpiry
            && contract.strike === strategyPreview.legs[0].strike
            && contract.option_type === strategyPreview.legs[0].option_type,
        )?.lot_size || 1,
        price_min: "0",
        price_max: String(2 * Math.max(spot, ...prices)),
        grid_points: 2,
        legs: strategyPreview.legs.map((leg) => ({
          option_type: leg.option_type,
          strike: leg.strike,
          quantity: leg.quantityLots * (leg.side === "BUY" ? 1 : -1),
          entry_premium: String(leg.price),
        })),
      }),
    })
      .then(readJson<PayoffEstimate>)
      .then((estimate) => {
        if (!controller.signal.aborted) setPayoffEstimate(estimate);
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setPayoffEstimateError(userFacingError(error, "Could not estimate expiry risk"));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setPayoffEstimateBusy(false);
      });
    return () => controller.abort();
  }, [chain, contracts, paperLots, selectedExpiry, strategyPreview, token]);

  async function checkConnection() {
    if (!token) return;
    setConnectionBusy(true);
    setConnectionError("");
    try {
      const response = await fetch(`${API_URL}/market-data/status`, {
        headers: { Authorization: `Bearer ${token}` },
        cache: "no-store",
      });
      setConnectionStatus(await readJson<ProviderHealth>(response));
    } catch (error) {
      setConnectionStatus(null);
      setConnectionError(userFacingError(error, "Could not check Upstox connection"));
    } finally {
      setConnectionBusy(false);
    }
  }

  async function submitAuth(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setAuthError("");
    setAuthBusy(true);
    try {
      const response = await fetch(`${API_URL}/auth/${authMode}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      const result = await readJson<TokenResponse>(response);
      window.sessionStorage.setItem("market-data-session", result.access_token);
      setToken(result.access_token);
      setPendingEntryError("");
      setPassword("");
    } catch (error) {
      setAuthError(authRequestError(error));
    } finally {
      setAuthBusy(false);
    }
  }

  async function loadSecurityActivity() {
    if (!token) return;
    setSecurityActivityBusy(true);
    setSecurityActivityError("");
    try {
      const response = await fetch(`${API_URL}/auth/activity`, {
        headers: { Authorization: `Bearer ${token}` }, cache: "no-store",
      });
      setSecurityActivity(await readJson<SecurityActivity[]>(response));
    } catch (error) {
      setSecurityActivityError(userFacingError(error, "Could not load security activity"));
    } finally {
      setSecurityActivityBusy(false);
    }
  }

  async function submitPasswordChange(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token) return;
    setPasswordChangeError("");
    setPasswordChangeBusy(true);
    try {
      const response = await fetch(`${API_URL}/auth/change-password`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
      });
      if (!response.ok) throw new Error(await responseError(response));
      signOut();
      setAuthMode("login");
      setAuthError("Password changed. Sign in again with your new password.");
    } catch (error) {
      setPasswordChangeError(userFacingError(error, "Could not change password"));
    } finally {
      setPasswordChangeBusy(false);
    }
  }

  async function revokeAndSignOut() {
    const activeToken = token;
    signOut();
    if (!activeToken) return;
    try {
      const response = await fetch(`${API_URL}/auth/logout`, {
        method: "POST",
        headers: { Authorization: `Bearer ${activeToken}` },
        keepalive: true,
      });
      if (!response.ok) throw new Error("Session revocation failed");
    } catch {
      if (!window.sessionStorage.getItem("market-data-session")) {
        setAuthError("Signed out locally. The server could not revoke this session; its access token expires within 30 minutes.");
      }
    }
  }

  function signOut() {
    batchPolicySaveController.current?.abort();
    batchPolicySaveController.current = null;
    setBatchPolicyReview(null);
    setBatchPolicyResults([]);
    setAuthError("");
    setCurrentPassword("");
    setNewPassword("");
    setPasswordChangeError("");
    setSecurityActivity([]);
    setSecurityActivityError("");
    setSecurityOpen(false);
    window.sessionStorage.removeItem("market-data-session");
    window.sessionStorage.removeItem("paper-batch-arm");
    setToken(null);
    setContracts([]);
    setSelectedExpiry("");
    setChain(null);
    setOptionFeatures(null);
    setFeaturesError("");
    setMarketError("");
    setConnectionStatus(null);
    setConnectionError("");
    setPaperAccount(null);
    setPaperLedger(null);
    setPaperLedgerCheckedAt("");
    setPaperLedgerError("");
    setPaperTrades([]);
    setPaperDeployments([]);
    setSettlementTradeId("");
    setSettlementValue("");
    setSettlementSource("");
    setSettlementPreview(null);
    setSettlementError("");
    deploymentCommand.current = null;
    batchArmCommand.current = null;
    setBatchArmResults([]);
    setBatchArmContext("");
    setPaperOrders([]);
    setPaperStrategies([]);
    setPaperPresets([]);
    setStrategyVersions([]);
    setSelectedVersionId("");
    setCompareVersionId("");
    setStrategyVersionName("");
    setVersionError("");
    setVersionMessage("");
    setPresetName("");
    setSelectedPresetId("");
    setPresetError("");
    setPresetMessage("");
    setPaperError("");
    setPendingEntryError("");
    setNotifications([]);
    setUnreadNotificationCount(0);
    setNotificationsError("");
    setNotificationsOpen(false);
    setHistoricalExpiries([]);
    setHistoricalExpiry("");
    setHistoricalVersionId("");
    setHistoricalBacktest(null);
    setHistoricalBacktestRuns([]);
    setHistoricalBacktestHistoryError("");
    setHistoricalDataError("");
    setHistoricalBacktestError("");
  }

  async function markNotificationRead(notification: UserNotification) {
    if (!token || notification.read_at || markingNotificationId) return;
    setMarkingNotificationId(notification.id);
    setNotificationsError("");
    try {
      const updated = await readJson<UserNotification>(await fetch(
        `${API_URL}/notifications/${notification.id}/read`,
        { method: "POST", headers: { Authorization: `Bearer ${token}` } },
      ));
      setNotifications((current) => current.map((item) => item.id === updated.id ? updated : item));
      setUnreadNotificationCount((count) => Math.max(0, count - 1));
    } catch (error) {
      setNotificationsError(userFacingError(error, "Could not mark notification as read"));
    } finally {
      setMarkingNotificationId(null);
    }
  }

  async function saveStrategyVersion() {
    if (!token || selectedStrategy === "custom" || !strategyVersionName.trim() || !entryRuleInputsValid) return;
    const selectedVersion = strategyVersions.find((item) => item.id === selectedVersionId);
    setVersionBusy(true); setVersionError(""); setVersionMessage("");
    try {
      const version = await readJson<StrategyVersion>(await fetch(`${API_URL}/strategies/versions`, {
        method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify({
          name: strategyVersionName.trim(), strategy_key: selectedStrategy,
          expected_template_version: paperStrategies.find((strategy) => strategy.key === selectedStrategy)?.version,
          lots: paperLots,
          ...(selectedOptionIndex !== "NIFTY" ? {underlying: selectedOptionIndex} : {}),
          entry_conditions: entryConditions, stop_loss_amount: stopLossInput || null,
          target_profit_amount: targetProfitInput || null, trailing_stop_amount: trailingStopInput || null,
          base_version_id: selectedVersion?.name === strategyVersionName.trim() ? selectedVersion.id : null,
        }),
      }));
      setStrategyVersions((current) => [version, ...current]);
      setSelectedVersionId(version.id);
      setVersionMessage(`Saved “${version.name}” version ${version.version_number}. Pinned to the next paper entry.`);
    } catch (error) {
      setVersionError(userFacingError(error, "Could not save strategy version"));
    } finally { setVersionBusy(false); }
  }

  function loadStrategyVersion() {
    const version = strategyVersions.find((item) => item.id === selectedVersionId);
    if (!version) return;
    const index = version.definition.option_index?.symbol ?? "NIFTY";
    if (!optionIndexes.some((item) => item.symbol === index)) {
      setVersionError("This version's index is unavailable in the current catalog."); return;
    }
    if (!paperStrategies.some((strategy) => strategy.key === version.definition.template_key)) {
      setVersionError("This version uses a strategy template that is no longer available."); return;
    }
    setSelectedStrategy(version.definition.template_key);
    if (index !== selectedOptionIndex) {
      setSelectedOptionIndex(index); setContracts([]); setSelectedExpiry("");
      setChain(null); setOptionFeatures(null); setSelectedPaperStrike("");
      setDeploymentPreparedKey("");
    }
    setPaperLots(version.definition.lots);
    setEntryConditions(version.definition.entry_conditions.map((condition) => ({ ...condition })));
    setStopLossInput(version.definition.stop_loss_amount ?? "");
    setTargetProfitInput(version.definition.target_profit_amount ?? "");
    setTrailingStopInput(version.definition.trailing_stop_amount ?? "");
    setStrategyVersionName(version.name); setVersionError("");
    setVersionMessage(`Loaded “${version.name}” version ${version.version_number}. Choose current expiry and strike.`);
  }

  function downloadJson(filename: string, payload: unknown) {
    const objectUrl = URL.createObjectURL(new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" }));
    const link = document.createElement("a");
    link.href = objectUrl;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
  }

  function exportReplayRun(run: OptionsBacktestRunRecord | ScheduledBacktestRunRecord) {
    downloadJson(`nifty-replay-${run.result.run_id}.json`, {
      export_schema_version: 1,
      record_type: run.result.mode,
      saved_at: run.created_at,
      result: run.result,
    });
  }

  function exportStrategyVersion() {
    const version = strategyVersions.find((item) => item.id === selectedVersionId);
    if (!version) return;
    const payload = {
      export_schema_version: 1,
      name: version.name,
      version_number: version.version_number,
      definition_hash: version.definition_hash,
      definition: version.definition,
    };
    downloadJson(`${version.name.replace(/[^A-Za-z0-9_-]+/g, "-")}-v${version.version_number}.json`, payload);
  }

  async function importStrategyDraft(file: File) {
    setVersionError(""); setVersionMessage("");
    try {
      if (file.size > 64 * 1024) throw new Error("Strategy JSON must be 64 KB or smaller.");
      const raw: unknown = JSON.parse(await file.text());
      if (!raw || typeof raw !== "object") throw new Error("Invalid strategy JSON.");
      const exportData = raw as Record<string, unknown>;
      if (exportData.export_schema_version !== 1 || !exportData.definition || typeof exportData.definition !== "object") {
        throw new Error("Unsupported strategy export format.");
      }
      const definition = exportData.definition as Record<string, unknown>;
      const savedIndex = definition.option_index;
      const index = savedIndex == null ? "NIFTY" : (
        typeof savedIndex === "object" ? (savedIndex as Record<string, unknown>).symbol : null
      );
      if (typeof index !== "string" || !optionIndexes.some((item) => item.symbol === index)) {
        throw new Error("The exported index is unavailable in the current catalog.");
      }
      const template = paperStrategies.find((item) => item.key === definition.template_key);
      if (!template || definition.schema_version !== 1 || definition.template_version !== template.version ||
          JSON.stringify(definition.template_legs) !== JSON.stringify(template.legs)) {
        throw new Error("The exported template is not available at the same version.");
      }
      const lots = definition.lots;
      const conditions = definition.entry_conditions;
      if (typeof lots !== "number" || !Number.isInteger(lots) || lots < 1 || lots > 10 ||
          !Array.isArray(conditions) || conditions.length > 5 ||
          !conditions.every((item: unknown) => {
            if (!item || typeof item !== "object") return false;
            const condition = item as Record<string, unknown>;
            return ENTRY_RULE_METRICS.some((metric) => metric.key === condition.metric) &&
              ["GT", "GTE", "LT", "LTE"].includes(String(condition.operator)) &&
              typeof condition.threshold === "string" && condition.threshold.trim() !== "" &&
              Number.isFinite(Number(condition.threshold));
          })) {
        throw new Error("The exported size or entry conditions are invalid.");
      }
      const amount = (field: string): string => {
        const value = definition[field];
        if (value === null) return "";
        if (typeof value !== "string" && typeof value !== "number") throw new Error(`Invalid ${field}.`);
        if (!Number.isFinite(Number(value)) || Number(value) <= 0) throw new Error(`Invalid ${field}.`);
        return String(value);
      };
      const stop = amount("stop_loss_amount");
      const target = amount("target_profit_amount");
      const trailing = amount("trailing_stop_amount");
      const sourceName = typeof exportData.name === "string" ? exportData.name : "Imported strategy";
      setSelectedStrategy(template.key);
      if (index !== selectedOptionIndex) {
        setSelectedOptionIndex(index); setContracts([]); setSelectedExpiry("");
        setChain(null); setOptionFeatures(null); setSelectedPaperStrike("");
        setDeploymentPreparedKey("");
      }
      setPaperLots(lots);
      setEntryConditions(conditions.map((item) => ({ ...(item as EntryConditionDraft) })));
      setStopLossInput(stop); setTargetProfitInput(target); setTrailingStopInput(trailing);
      setStrategyVersionName(`${sourceName} copy`.slice(0, 64));
      setSelectedVersionId(""); setCompareVersionId("");
      setVersionMessage("Imported as an editable draft. Review the settings and save a new version.");
    } catch (error) {
      setVersionError(userFacingError(error, "Could not import strategy draft"));
    }
  }

  async function savePaperPreset() {
    if (!token || selectedStrategy === "custom" || !presetName.trim() || !entryRuleInputsValid) return;
    setPresetBusy(true);
    setPresetError("");
    setPresetMessage("");
    try {
      const preset = await readJson<PaperStrategyPreset>(await fetch(
        `${API_URL}/paper/strategy-presets`,
        {
          method: "POST",
          headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
          body: JSON.stringify({
            name: presetName.trim(),
            strategy_key: selectedStrategy,
            entry_conditions: entryConditions,
          }),
        },
      ));
      setPaperPresets((current) => [...current, preset].sort((a, b) => a.name.localeCompare(b.name)));
      setSelectedPresetId(preset.id);
      setPresetName("");
      setPresetMessage(`Saved “${preset.name}”.`);
    } catch (error) {
      setPresetError(userFacingError(error, "Could not save preset"));
    } finally {
      setPresetBusy(false);
    }
  }

  function loadPaperPreset() {
    const preset = paperPresets.find((item) => item.id === selectedPresetId);
    if (!preset) return;
    if (!paperStrategies.some((strategy) => strategy.key === preset.strategy_key)) {
      setPresetError("This preset uses a strategy template that is no longer available.");
      return;
    }
    setSelectedStrategy(preset.strategy_key);
    setEntryConditions(preset.entry_conditions.map((condition) => ({ ...condition })));
    setPresetError("");
    setPresetMessage(`Loaded “${preset.name}”. Review current expiry, strike, size, and exits before trading.`);
  }

  async function deletePaperPreset() {
    const preset = paperPresets.find((item) => item.id === selectedPresetId);
    if (!token || !preset || !window.confirm(`Delete saved preset “${preset.name}”?`)) return;
    setPresetBusy(true);
    setPresetError("");
    setPresetMessage("");
    try {
      const response = await fetch(`${API_URL}/paper/strategy-presets/${preset.id}`, {
        method: "DELETE",
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!response.ok) throw new Error(await responseError(response));
      setPaperPresets((current) => current.filter((item) => item.id !== preset.id));
      setSelectedPresetId("");
      setPresetMessage(`Deleted “${preset.name}”.`);
    } catch (error) {
      setPresetError(userFacingError(error, "Could not delete preset"));
    } finally {
      setPresetBusy(false);
    }
  }

  async function refreshPaperWorkspace() {
    setPaperLedger(null);
    setPaperLedgerCheckedAt("");
    setPaperRefreshCount((value) => value + 1);
    setRefreshCount((value) => value + 1);
  }

  async function runHistoricalBacktest(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token || !historicalExpiry || !historicalEntryDate || !historicalExitDate) return;
    setHistoricalBacktestError("");
    setHistoricalBacktestBusy(true);
    setHistoricalBacktest(null);
    try {
      const result = await readJson<OptionsBacktestResult>(await fetch(
        `${API_URL}/backtests/options/run`,
        {
          method: "POST",
          headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
          body: JSON.stringify({
            strategy_key: historicalStrategyKey,
            strategy_version_id: historicalVersionId || null,
            expiry: historicalExpiry,
            entry_date: historicalEntryDate,
            exit_date: historicalExitDate,
            lots: historicalLots,
            fee_per_order: scheduleFee,
            slippage_per_unit_per_side: scheduleSlippage,
          }),
        },
      ));
      setHistoricalBacktest(result);
      setHistoricalBacktestRuns((runs) => [
        { created_at: new Date().toISOString(), result },
        ...runs.filter((run) => run.result.run_id !== result.run_id),
      ].slice(0, 20));
      setHistoricalBacktestHistoryError("");
    } catch (error) {
      setHistoricalBacktestError(userFacingError(error, "Could not run historical replay"));
    } finally {
      setHistoricalBacktestBusy(false);
    }
  }

  async function runScheduledBacktest(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token || !scheduleStartExpiry || !scheduleEndExpiry) return;
    setScheduledError("");
    setScheduledBacktest(null);
    setScheduledBusy(true);
    try {
      const result = await readJson<ScheduledBacktestResult>(await fetch(
        API_URL + "/backtests/options/scheduled-run",
        {
          method: "POST",
          headers: { Authorization: "Bearer " + token, "Content-Type": "application/json" },
          body: JSON.stringify({
            strategy_key: historicalStrategyKey,
            strategy_version_id: historicalVersionId || null,
            start_date: scheduleStartExpiry,
            end_date: scheduleEndExpiry,
            entry_sessions_before_expiry: scheduleEntrySessions,
            lots: historicalLots,
            fee_per_order: scheduleFee,
            slippage_per_unit_per_side: scheduleSlippage,
          }),
        },
      ));
      setScheduledBacktest(result);
      setScheduledRuns((runs) => [
        { created_at: new Date().toISOString(), result },
        ...runs.filter((run) => run.result.run_id !== result.run_id),
      ].slice(0, 20));
      setScheduledHistoryError("");
    } catch (error) {
      setScheduledError(userFacingError(error, "Could not run scheduled replay"));
    } finally {
      setScheduledBusy(false);
    }
  }

  async function openPaperTrade() {
    if (selectedOptionIndex !== "NIFTY" || !token || !selectedExpiry || !selectedPaperStrike || paperBusy || !paperEntry.ready || paperEntry.pending) return;
    setPaperError("");
    setPendingEntryError("");
    setPaperBusy(true);
    try {
      await paperEntry.submit(JSON.stringify({
          strategy_key: selectedStrategy,
          strategy_version_id: selectedVersionId || null,
          expected_template_version: selectedPaperStrategy?.version,
          expiry: selectedExpiry,
          reference_strike: isCustomStrategy
            ? customLegDrafts[0]?.strike || selectedPaperStrike
            : selectedPaperStrike,
          lots: paperLots,
          custom_legs: isCustomStrategy ? customLegDrafts : null,
          entry_conditions: entryConditions.map((condition) => ({
            ...condition,
          })),
          stop_loss_amount: stopLossInput ? Number(stopLossInput) : null,
          target_profit_amount: targetProfitInput ? Number(targetProfitInput) : null,
          trailing_stop_amount: trailingStopInput ? Number(trailingStopInput) : null,
        }));
      await refreshPaperWorkspace();
    } catch (error) {
      const message = userFacingError(error, "Could not open the paper trade");
      setPaperError(message);
      setPendingEntryError(message);
    } finally {
      setPaperBusy(false);
    }
  }

  async function armPaperStrategy() {
    if (selectedOptionIndex !== "NIFTY" || !token || !selectedExpiry || !selectedPaperStrike || paperBusy || !paperEntry.ready || paperEntry.pending || isCustomStrategy || matchingWaitingPaperDeployment) return;
    setPaperError("");
    setPaperBusy(true);
    const payload = JSON.stringify({
      strategy_key: selectedStrategy,
      strategy_version_id: selectedVersionId || null,
      expected_template_version: selectedPaperStrategy?.version,
      expiry: selectedExpiry,
      reference_strike: selectedPaperStrike,
      lots: paperLots,
      entry_conditions: entryConditions,
      stop_loss_amount: stopLossInput ? Number(stopLossInput) : null,
      target_profit_amount: targetProfitInput ? Number(targetProfitInput) : null,
      trailing_stop_amount: trailingStopInput ? Number(trailingStopInput) : null,
    });
    if (deploymentCommand.current?.payload !== payload) {
      deploymentCommand.current = { payload, key: `arm_${crypto.randomUUID().replaceAll("-", "")}` };
    }
    try {
      const armed = await readJson<PaperDeployment>(await fetch(`${API_URL}/paper/deployments`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`, "Content-Type": "application/json",
          "Idempotency-Key": deploymentCommand.current.key,
        },
        body: payload,
      }));
      setPaperDeployments((current) => [armed, ...current.filter((item) => item.id !== armed.id)]);
      deploymentCommand.current = null;
      setPaperTicketMessage(`${paperStrategies.find(item => item.key === armed.strategy_key)?.name || armed.strategy_key} is armed. The worker will wait for a later market snapshot, passing entry rules and all paper risk checks before opening any trade.`);
      await refreshPaperWorkspace();
    } catch (error) {
      setPaperError(userFacingError(error, "Could not arm this paper strategy"));
    } finally {
      setPaperBusy(false);
    }
  }

  async function cancelPaperDeployment(id: string) {
    if (!token || paperBusy) return;
    setPaperError("");
    setPaperBusy(true);
    try {
      const cancelled = await readJson<PaperDeployment>(await fetch(`${API_URL}/paper/deployments/${id}/cancel`, {
        method: "POST", headers: { Authorization: `Bearer ${token}` },
      }));
      setPaperDeployments((current) => current.map((item) => item.id === id ? cancelled : item));
      deploymentCommand.current = null;
      await refreshPaperWorkspace();
    } catch (error) {
      setPaperError(userFacingError(error, "Could not cancel the waiting strategy"));
    } finally {
      setPaperBusy(false);
    }
  }

  async function checkIndexCloseReference() {
    if (!token || !settlementTrade || indexCloseBusy) return;
    setIndexCloseBusy(true); setSettlementError(""); setIndexCloseReference(null);
    try {
      const result = await readJson<PaperExpiryIndexCloseReference>(await fetch(
        `${API_URL}/paper/trades/${settlementTrade.id}/expiry-settlement/index-close-reference`, {
          headers: { Authorization: `Bearer ${token}` }, cache: "no-store",
        },
      ));
      if (result.trade_id === settlementTrade.id) setIndexCloseReference(result);
    } catch (error) {
      setSettlementError(userFacingError(error, "Could not check the broker's daily index close"));
    } finally { setIndexCloseBusy(false); }
  }

  async function checkOfficialIndexCloseReference() {
    if (!token || !settlementTrade || indexCloseBusy) return;
    setIndexCloseBusy(true); setSettlementError(""); setOfficialIndexCloseReference(null);
    try {
      const result = await readJson<PaperOfficialExpirySettlementPreview>(await fetch(
        `${API_URL}/paper/trades/${settlementTrade.id}/expiry-settlement/official-preview`, {
          headers: { Authorization: `Bearer ${token}` }, cache: "no-store",
        },
      ));
      setOfficialIndexCloseReference(result);
    } catch (error) {
      setSettlementError(userFacingError(error, "Could not check the NSE index archive"));
    } finally { setIndexCloseBusy(false); }
  }

  async function previewExpirySettlement(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token || !settlementTrade || settlementBusy) return;
    setSettlementBusy(true);
    setSettlementError("");
    setSettlementPreview(null);
    try {
      const result = await readJson<PaperExpirySettlementPreview>(await fetch(
        `${API_URL}/paper/trades/${settlementTrade.id}/expiry-settlement/preview`, {
          method: "POST",
          headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
          body: JSON.stringify({
            settlement_date: settlementTrade.expiry,
            final_index_value: settlementValue,
            source_reference: settlementSource,
          }),
        },
      ));
      setSettlementPreview(result);
    } catch (error) {
      setSettlementError(userFacingError(error, "Could not preview expiry settlement"));
    } finally {
      setSettlementBusy(false);
    }
  }

  async function recordExpirySettlement() {
    if (!token || !settlementTrade || !settlementPreview || settlementBusy
      || settlementPreview.trade_id !== settlementTrade.id
      || Number(settlementPreview.final_index_value) !== Number(settlementValue)
      || settlementPreview.source_reference !== settlementSource.trim()) return;
    const archiveSha256 = officialIndexCloseReference?.status === "available"
      && officialIndexCloseReference.trade_id === settlementTrade.id
      && officialIndexCloseReference.preview?.final_index_value === settlementPreview.final_index_value
      && officialIndexCloseReference.source_url === settlementPreview.source_reference
      ? officialIndexCloseReference.archive_sha256 : null;
    setSettlementBusy(true);
    setSettlementError("");
    try {
      await readJson<PaperTrade>(await fetch(
        `${API_URL}/paper/trades/${settlementTrade.id}/expiry-settlement`, {
          method: "POST",
          headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
          body: JSON.stringify({
            settlement_date: settlementTrade.expiry,
            final_index_value: settlementValue,
            source_reference: settlementSource,
            ...(archiveSha256 ? { archive_sha256: archiveSha256 } : {}),
            confirmed_final: true,
          }),
        },
      ));
      setSettlementTradeId("");
      setSettlementValue("");
      setSettlementSource("");
      setSettlementPreview(null);
      setIndexCloseReference(null);
      await refreshPaperWorkspace();
    } catch (error) {
      setSettlementError(userFacingError(error, "Could not record expiry settlement"));
    } finally {
      setSettlementBusy(false);
    }
  }

  async function closePaperTrade(tradeId: string) {
    if (!token) return;
    setPaperError("");
    setPaperBusy(true);
    try {
      await readJson<PaperTrade>(await fetch(`${API_URL}/paper/trades/${tradeId}/close`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
      }));
      await refreshPaperWorkspace();
    } catch (error) {
      setPaperError(userFacingError(error, "Could not close the paper trade"));
    } finally {
      setPaperBusy(false);
    }
  }

  async function togglePaperKillSwitch() {
    if (!token || !paperAccount) return;
    setPaperError("");
    setPaperBusy(true);
    try {
      await readJson(await fetch(`${API_URL}/paper/account/kill-switch`, {
        method: "PUT",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: !paperAccount.kill_switch_enabled }),
      }));
      setPaperRefreshCount((value) => value + 1);
    } catch (error) {
      setPaperError(userFacingError(error, "Could not update the paper kill switch"));
    } finally {
      setPaperBusy(false);
    }
  }

  async function fundPaperWalletToFiftyLakh() {
    if (!token || walletFundingBusy || !paperAccount || Number(paperAccount.cash_balance) >= 5_000_000) return;
    setWalletFundingBusy(true); setWalletFundingMessage(""); setWalletFundingError("");
    try {
      const receipt = await readJson<{ cash_before: string; cash_delta: string; cash_after: string; paper_only: true }>(await fetch(
        `${API_URL}/paper/account/capital-adjustments`, {
          method: "POST",
          headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json", "Idempotency-Key": crypto.randomUUID() },
          body: JSON.stringify({ target_balance: "5000000.00" }),
        },
      ));
      setWalletFundingMessage(`Paper-only cash adjustment recorded: ₹${formatPrice(receipt.cash_before)} + ₹${formatPrice(receipt.cash_delta)} = ₹${formatPrice(receipt.cash_after)}. Starting capital and saved positions were preserved.`);
      await refreshPaperWorkspace();
      void checkPaperLedger();
    } catch (error) {
      setWalletFundingError(userFacingError(error, "Could not adjust the NIFTY F&O paper wallet"));
    } finally { setWalletFundingBusy(false); }
  }

  async function checkPaperLedger() {
    if (!token || paperLedgerBusy) return;
    setPaperLedgerBusy(true); setPaperLedgerError(""); setPaperLedger(null);
    try {
      const report = await readJson<PaperLedgerReconciliation>(await fetch(
        `${API_URL}/paper/account/ledger-reconciliation`, {
          headers: { Authorization: `Bearer ${token}` }, cache: "no-store",
        },
      ));
      setPaperLedger(report);
      setPaperLedgerCheckedAt(new Date().toISOString());
    } catch (error) {
      setPaperLedgerError(userFacingError(error, "Could not check the paper ledger"));
    } finally { setPaperLedgerBusy(false); }
  }

  useEffect(() => {
    if (!token) return;
    const timer = window.setInterval(() => setPaperRefreshCount(value => value + 1), 15_000);
    return () => window.clearInterval(timer);
  }, [token]);

  async function retryPendingEntry() {
    if (paperBusy || !paperEntry.ready || !paperEntry.pending) return;
    setPaperBusy(true); setPaperError(""); setPendingEntryError("");
    try { await paperEntry.submit(); await refreshPaperWorkspace(); }
    catch (error) {
      const message = userFacingError(error, "Pending entry remains unresolved");
      setPaperError(message);
      setPendingEntryError(message);
    }
    finally { setPaperBusy(false); }
  }

  return (
    <main className={token ? "shell dashboard-shell" : "shell"}>
      <BuildUpdateNotice />
      {token && <WorkerStatus token={token} apiUrl={API_URL} />}
      {token && paperEntry.identityError && <section className="worker-status worker-status--warning" role="alert">
        <strong>Paper entry is unavailable</strong>
        <p>{paperEntry.identityError}</p>
        <button className="refresh-button" onClick={paperEntry.retryIdentity} type="button">Verify account again</button>
      </section>}
      {token && paperEntry.pending && <section className="worker-status worker-status--warning" role="status">
        <strong>A paper entry needs confirmation</strong>
        <p>Review the trade list, then retry the saved request if needed. The same key returns an existing trade if the first request succeeded; otherwise, retrying may open the trade now.</p>
        {pendingEntryError && <p className="error-banner" role="alert">{pendingEntryError}</p>}
        <button className="refresh-button" disabled={paperBusy || !paperEntry.ready} onClick={() => void retryPendingEntry()} type="button">
          Retry saved paper entry
        </button>
      </section>}
      <header className="topbar">
        <a className="brand" href="/" aria-label="TradingPilot home">
          <span className="brand-mark">TP</span>
          <span className="brand-copy">
            <strong>TradingPilot</strong>
            <small>PAPER TRADING CONSOLE</small>
          </span>
        </a>
        <div className="topbar-right">
          <span className="pill"><span className="status-dot" />PAPER MODE</span>
          {token ? <details className="mobile-account-menu">
            <summary>Account</summary>
            <div className="mobile-account-menu-panel">
              <strong>{email || "Signed in"}</strong>
              <button onClick={() => { setSecurityOpen(true); void loadSecurityActivity(); }} type="button">Account security</button>
              <button onClick={() => void revokeAndSignOut()} type="button">Sign out</button>
            </div>
          </details> : null}
        </div>
      </header>

      {!token && <section className="intro">
        <div className="eyebrow"><span className="eyebrow-mark" />{token && workspace === "equities" ? "EQUITIES & ETFs" : "NIFTY OPTIONS"} <span>·</span> MARKET RESEARCH</div>
        <h1>{token && workspace === "equities" ? <>Research stocks<br /><em>before you trade.</em></> : <>See the market<br /><em>as it moves.</em></>}</h1>
        <p>{token && workspace === "equities" ? "Search equities and ETFs, build a watchlist, inspect live snapshots, and study price history from your starting workspace." : "Build a defined-risk NIFTY options position, simulate it from current quotes, and track it in a paper portfolio. Live broker orders remain disabled."}</p>
      </section>}
      {token && activeSection === "dashboard" ? <TradingDashboard token={token} apiUrl={API_URL} workspace={workspace} onOpenStrategies={() => { setActiveSection("algo"); window.scrollTo({ top: 0, behavior: "smooth" }); }} onExploreSymbols={() => {
        if (workspace === "derivatives") { setActiveSection("charts"); window.scrollTo({ top: 0, behavior: "smooth" }); return; }
        const explorer = document.querySelector<HTMLDetailsElement>("#equity-instruments > details");
        if (explorer) { explorer.open = true; explorer.scrollIntoView({ behavior: "smooth", block: "start" }); }
      }} /> : null}

      {!authReady ? (
        <section className="panel loading-panel" aria-live="polite">Restoring your session…</section>
      ) : !token ? (
        <section className="auth-layout">
          <div className="panel auth-panel">
            <div className="panel-kicker">YOUR WORKSPACE</div>
            <h2>{authMode === "login" ? "Welcome back" : "Create your account"}</h2>
            <p className="muted">{authMode === "login" ? "Sign in to retrieve your connected NIFTY market data." : "Create a local app account. Your Upstox token stays on the API server."}</p>
            <form className="auth-form" onSubmit={submitAuth}>
              <label htmlFor="email">Email</label>
              <input
                autoComplete="email"
                id="email"
                onChange={(event) => setEmail(event.target.value)}
                required
                type="email"
                value={email}
              />
              <label htmlFor="password">Password</label>
              <input
                autoComplete={authMode === "login" ? "current-password" : "new-password"}
                id="password"
                minLength={authMode === "register" ? 12 : undefined}
                onChange={(event) => setPassword(event.target.value)}
                required
                type="password"
                value={password}
              />
              {authMode === "register" ? <p className="auth-hint">Use at least 12 characters.</p> : null}
              {authError ? <p className="error-banner" role="alert">{authError}</p> : null}
              <button className="primary-button auth-submit" disabled={authBusy} type="submit">
                {authBusy ? "Please wait…" : authMode === "login" ? "Sign in" : "Create account"}
              </button>
            </form>
            <p className="auth-switch">
              {authMode === "login" ? "New to this workspace?" : "Already have an account?"}{" "}
              <button
                className="inline-button"
                onClick={() => {
                  setAuthError("");
                  setAuthMode(authMode === "login" ? "register" : "login");
                }}
                type="button"
              >
                {authMode === "login" ? "Create account" : "Sign in"}
              </button>
            </p>
          </div>
          <aside className="auth-aside">
            <div className="aside-icon">↗</div>
            <h3>Read-only market access</h3>
            <p>Your app sign-in is separate from the Upstox Analytics Token. The provider token stays on the API server.</p>
            <a href={`${API_URL}/docs`} rel="noreferrer" target="_blank">Open API documentation <span>↗</span></a>
          </aside>
        </section>
      ) : (
        <>
          <aside className="dashboard-sidebar" aria-label="Main navigation">
            <div className="dashboard-sidebar-brand">
              <span className="brand-mark">TP</span>
              <strong>TradingPilot</strong>
            </div>
            <label className="sidebar-market-switch">
              <span>Market</span>
              <select
                value={workspace}
                onChange={(event) => {
                  setWorkspace(event.target.value as "equities" | "derivatives");
                  setActiveSection("dashboard");
                  window.scrollTo({ top: 0, behavior: "smooth" });
                }}
              >
                <option value="equities">Equity &amp; ETF</option>
                <option value="derivatives">Future &amp; Options</option>
              </select>
            </label>
            <button aria-current={activeSection === "dashboard" ? "page" : undefined} className={`dashboard-sidebar-item ${activeSection === "dashboard" ? "active" : ""}`} onClick={() => { setActiveSection("dashboard"); window.scrollTo({ top: 0, behavior: "smooth" }); }} type="button"><span aria-hidden="true">⌂</span> Dashboard</button>
            <button aria-current={activeSection === "charts" ? "page" : undefined} className={`dashboard-sidebar-item ${activeSection === "charts" ? "active" : ""}`} onClick={() => { setActiveSection("charts"); window.scrollTo({ top: 0, behavior: "smooth" }); }} type="button"><span aria-hidden="true">⌁</span> Charts</button>
            <button aria-current={activeSection === "algo" ? "page" : undefined} className={`dashboard-sidebar-item ${activeSection === "algo" ? "active" : ""}`} onClick={() => { setActiveSection("algo"); window.scrollTo({ top: 0, behavior: "smooth" }); }} type="button"><span aria-hidden="true">↗</span> Strategies</button>
            <button aria-current={activeSection === "broker" ? "page" : undefined} className={`dashboard-sidebar-item ${activeSection === "broker" ? "active" : ""}`} onClick={() => { setActiveSection("broker"); window.scrollTo({ top: 0, behavior: "smooth" }); }} type="button"><span aria-hidden="true">▣</span> Broker Panel</button>
            <button aria-current={activeSection === "backtest" ? "page" : undefined} className={`dashboard-sidebar-item ${activeSection === "backtest" ? "active" : ""}`} onClick={() => { setActiveSection("backtest"); window.scrollTo({ top: 0, behavior: "smooth" }); }} type="button"><span aria-hidden="true">◷</span> Backtest</button>
            <button aria-current={activeSection === "bridge" ? "page" : undefined} className={`dashboard-sidebar-item ${activeSection === "bridge" ? "active" : ""}`} onClick={() => { setActiveSection("bridge"); window.scrollTo({ top: 0, behavior: "smooth" }); }} type="button"><span aria-hidden="true">◇</span> Bridge Tool</button>
            <button aria-current={activeSection === "research" ? "page" : undefined} className={`dashboard-sidebar-item ${activeSection === "research" ? "active" : ""}`} onClick={() => { setActiveSection("research"); window.scrollTo({ top: 0, behavior: "smooth" }); }} type="button"><span aria-hidden="true">⌕</span> Research</button>
            <a className="dashboard-sidebar-item" href={`${API_URL}/docs`} rel="noreferrer" target="_blank"><span aria-hidden="true">?</span> Help Center</a>
            <div className="dashboard-sidebar-footer">
              <div className="sidebar-account-card">
                <span className="sidebar-account-label">Account</span>
                <strong>{email || "Signed in"}</strong>
                <small><span className="status-dot" /> Paper mode · Live orders disabled</small>
              </div>
              <div className="sidebar-account-actions">
                <div className="notification-wrap sidebar-notification-wrap" ref={notificationWrapRef}>
                  <button
                    aria-expanded={notificationsOpen}
                    aria-haspopup="dialog"
                    aria-controls="notification-panel"
                    className="notification-button sidebar-action-button"
                    ref={notificationButtonRef}
                    onClick={() => {
                      const opening = !notificationsOpen;
                      setNotificationsOpen(opening);
                      if (opening) setNotificationRefreshCount((count) => count + 1);
                    }}
                    type="button"
                  >
                    <svg aria-hidden="true" viewBox="0 0 24 24"><path d="M18 9a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4" /></svg>
                    <span className="notification-button-label">Updates</span>
                    {unreadNotificationCount > 0 ? <span className="notification-count">{unreadNotificationCount > 99 ? "99+" : unreadNotificationCount}</span> : null}
                  </button>
                  {notificationsOpen ? (
                    <section aria-label="Notifications" className="notification-panel sidebar-notification-panel" id="notification-panel" role="dialog">
                      <div className="notification-panel-heading">
                        <div><strong>Updates</strong><small>{unreadNotificationCount} unread</small></div>
                        <div className="notification-panel-actions">
                          <button
                            aria-label="Refresh notifications"
                            className="notification-refresh"
                            onClick={() => setNotificationRefreshCount((count) => count + 1)}
                            type="button"
                          >↻</button>
                          <button
                            aria-label="Close updates"
                            className="notification-close"
                            onClick={() => { setNotificationsOpen(false); notificationButtonRef.current?.focus(); }}
                            type="button"
                          >×</button>
                        </div>
                      </div>
                      {notificationsError ? <p className="notification-error">{notificationsError}</p> : null}
                      {notifications.length ? (
                        <ul className="notification-list">
                          {notifications.map((notification) => (
                            <li className={notification.read_at ? "notification-item" : "notification-item unread"} key={notification.id}>
                              <button
                                disabled={notification.read_at !== null || markingNotificationId === notification.id}
                                onClick={() => void markNotificationRead(notification)}
                                type="button"
                              >
                                <strong>{notification.title}</strong>
                                <span>{notification.message}</span>
                                <small>{formatTime(notification.created_at)}{notification.read_at ? " · Read" : " · Mark read"}</small>
                              </button>
                            </li>
                          ))}
                        </ul>
                      ) : notificationsError ? null : <p className="notification-empty">No updates yet. Paper position events will appear here.</p>}
                    </section>
                  ) : null}
                </div>
                <button
                  aria-expanded={securityOpen}
                  className="sidebar-action-button"
                  onClick={() => {
                    const opening = !securityOpen;
                    setSecurityOpen(opening);
                    if (opening) void loadSecurityActivity();
                  }}
                  type="button"
                >Account security</button>
                <button className="sidebar-action-button danger" onClick={() => void revokeAndSignOut()} type="button">Sign out</button>
              </div>
            </div>
          </aside>
          {securityOpen ? <section aria-label="Account security" className="panel account-security">
            <div className="account-security-heading"><h2>Account security</h2><button className="inline-button" onClick={() => setSecurityOpen(false)} type="button">Close</button></div>
            <p className="muted">Changing your app password ends every active session, including this one.</p>
            <form className="auth-form" onSubmit={submitPasswordChange}>
              <label htmlFor="current-password">Current password</label>
              <input autoComplete="current-password" id="current-password" onChange={(event) => setCurrentPassword(event.target.value)} required type="password" value={currentPassword} />
              <label htmlFor="new-password">New password</label>
              <input autoComplete="new-password" id="new-password" minLength={12} onChange={(event) => setNewPassword(event.target.value)} required type="password" value={newPassword} />
              {passwordChangeError ? <p className="error-banner" role="alert">{passwordChangeError}</p> : null}
              <button className="primary-button auth-submit" disabled={passwordChangeBusy} type="submit">{passwordChangeBusy ? "Updating…" : "Change password"}</button>
            </form>
            <div className="security-activity">
              <div className="security-activity-heading"><strong>Recent security activity</strong><button className="inline-button" disabled={securityActivityBusy} onClick={() => void loadSecurityActivity()} type="button">{securityActivityBusy ? "Loading…" : "Refresh"}</button></div>
              {securityActivityError ? <p className="error-banner" role="alert">{securityActivityError}</p> : null}
              {securityActivity.length ? <ul>{securityActivity.map((event) => <li key={event.id}><span>{SECURITY_ACTIVITY_LABELS[event.event_type]}</span><time dateTime={event.created_at}>{formatTime(event.created_at)}</time></li>)}</ul> : !securityActivityBusy && !securityActivityError ? <p className="muted">No activity available yet.</p> : null}
            </div>
          </section> : null}
          {activeSection === "research" ? <ResearchWorkspace key={token} token={token} apiUrl={API_URL} /> : workspace === "equities" ? (
            <section className={`equities-workspace app-section-page${activeSection === "dashboard" ? " dashboard-detail-section" : ""}`} aria-label="Equities workspace">
              {activeSection !== "dashboard" ? <div className="equities-heading"><div><div className="panel-kicker">{activeSection.toUpperCase()}</div><h2>{activeSection === "charts" ? "Equity & ETF charts" : activeSection === "algo" ? "Equity algo strategies" : activeSection === "broker" ? "Equity broker panel" : activeSection === "backtest" ? "Equity backtest" : "Equity bridge tool"}</h2></div><span className="paper-mode-badge">PAPER TRADING</span></div> : null}
              {activeSection === "dashboard" ? <><MarketOutlook token={token} apiUrl={API_URL} /><div id="equity-instruments"><InstrumentCatalog token={token} apiUrl={API_URL} compact /></div></> : null}
              {activeSection === "charts" ? <ChartWorkspace token={token} apiUrl={API_URL} /> : null}
              {activeSection === "algo" ? <EquityStrategies token={token} apiUrl={API_URL} /> : null}
              {activeSection === "broker" ? <section className="empty-section-card"><h3>Paper account only</h3><p className="muted">This page will hold broker connection, account, and order-routing controls for equities. For now, live broker orders remain disabled and equity paper trading uses the local wallet.</p></section> : null}
              {activeSection === "backtest" ? <section className="empty-section-card"><h3>Replay not available yet</h3><p className="muted">Equity strategy replay will use saved equity candles and paper-trade history. The current build has the F&O replay engine first; equity replay is planned next.</p></section> : null}
              {activeSection === "bridge" ? <section className="empty-section-card"><h3>Bridge routing disabled</h3><p className="muted">This page will show paper/live bridge controls when live equity broker routing is enabled. It stays read-only in this milestone.</p></section> : null}
            </section>
          ) : <>
          <label className="index-picker">Index
            <select aria-label="Option index" value={selectedOptionIndex} onChange={(event) => {
              setSelectedOptionIndex(event.target.value); setContracts([]); setSelectedExpiry("");
              setChain(null); setOptionFeatures(null); setSelectedVersionId("");
              setSelectedPaperStrike(""); setDeploymentPreparedKey("");
            }}>
              {optionIndexes.map((item) => <option key={item.symbol} value={item.symbol}>{item.name} · {item.exchange}</option>)}
            </select>
          </label>
          {indexCatalogError ? <p role="alert">{indexCatalogError}</p> : null}
          {selectedOptionIndex !== "NIFTY" ? <p role="status">Market-data preview. Paper entry for this index is pending venue and settlement validation.</p> : null}
          {activeSection === "broker" ? <section className="paper-workspace" aria-label="Broker connection">
            <div className="paper-heading"><div><span className="panel-kicker">BROKER PANEL</span><h2>Market-data connection</h2><p className="muted">Upstox supplies data. Your paper account is simulated.</p></div><span className="paper-mode-badge">LIVE ORDERS DISABLED</span></div>
            <div className="connection-row" aria-live="polite"><span className="connection-state">{connectionBusy ? "Checking…" : connectionStatus?.healthy ? "Connection available" : "Connection not verified"}</span><span className="connection-detail">{connectionError || connectionStatus?.detail || "Check access to read-only market data."}</span><button className="refresh-button" disabled={connectionBusy} onClick={() => void checkConnection()} type="button">Check connection</button></div>
            <p className="features-note">Connection availability does not verify bid/ask book timestamps. Quote quality and session checks still apply to every paper entry.</p>
            <button className="inline-button" onClick={() => setActiveSection("charts")} type="button">View market data</button>
          </section> : null}
          {activeSection === "bridge" ? <section className="paper-workspace" aria-label="Bridge availability">
            <div className="paper-heading"><div><span className="panel-kicker">BRIDGE TOOL</span><h2>External routing disabled</h2><p className="muted">No broker execution or external signal bridge is enabled.</p></div><span className="paper-mode-badge">PAPER MODE</span></div>
            <p className="features-note">Manage simulated entries, positions and exit rules in Strategies. This screen cannot route orders.</p>
            <button className="refresh-button" onClick={() => setActiveSection("algo")} type="button">View paper strategies</button>
          </section> : null}
          {activeSection === "charts" ? <ChartWorkspace token={token} apiUrl={API_URL} /> : null}
          {activeSection === "charts" ? <>
          <section className="market-toolbar">
            <div>
              <div className="panel-kicker">LIVE MARKET DATA</div>
              <h2>{selectedOptionIndex} option chain</h2>
            </div>
            <div className="toolbar-controls">
              <label className="select-label" htmlFor="expiry">Expiry</label>
              <select
                disabled={contractsBusy || expiryOptions.length === 0}
                id="expiry"
                onChange={(event) => setSelectedExpiry(event.target.value)}
                value={selectedExpiry}
              >
                {expiryOptions.length === 0 ? <option value="">No expiries found</option> : null}
                {expiryOptions.map((expiry) => <option key={expiry} value={expiry}>{expiry}</option>)}
              </select>
              <button
                className="refresh-button"
                disabled={chainBusy || !selectedExpiry}
                onClick={() => setRefreshCount((value) => value + 1)}
                type="button"
              >
                <span className={chainBusy ? "refresh-icon spinning" : "refresh-icon"}>↻</span>
                {chainBusy ? "Loading" : "Refresh"}
              </button>
            </div>
          </section>

          <div className="connection-row" aria-live="polite">
            <span className={`connection-state ${connectionStatus ? (connectionStatus.healthy ? "connected" : "unavailable") : "unchecked"}`}>
              Upstox: {connectionBusy ? "Checking…" : connectionStatus ? (connectionStatus.healthy ? "Connected" : connectionStatus.configured ? "Unavailable" : "Token missing") : "Not checked"}
            </span>
            <span className="connection-detail">{connectionError || connectionStatus?.detail || "Check the read-only market-data connection."}</span>
            <button className="refresh-button connection-check" disabled={connectionBusy} onClick={() => void checkConnection()} type="button">
              {connectionBusy ? "Checking…" : "Check connection"}
            </button>
          </div>
          {marketError ? <div className="error-banner market-error" role="alert">{marketError}</div> : null}

          <section className="stats-grid" aria-label="Market summary">
            <article className="stat-card">
              <span className="stat-label">UNDERLYING</span>
              <strong>{selectedOptionIndex}</strong>
              <small>National Stock Exchange</small>
            </article>
            <article className="stat-card">
              <span className="stat-label">SPOT PRICE</span>
              <strong>{chain?.underlying_spot ? `₹${formatPrice(chain.underlying_spot)}` : "—"}</strong>
              <small>{chain ? `Expiry ${chain.expiry}` : "Waiting for market data"}</small>
            </article>
            <article className="stat-card">
              <span className="stat-label">STRIKES LOADED</span>
              <strong>{chain ? formatCount(strikeRows.length) : "—"}</strong>
              <small>{chain ? `${formatCount(chain.contracts.length)} call and put contracts` : "Current expiry"}</small>
            </article>
            <article className="stat-card">
              <span className="stat-label">SNAPSHOT RECEIVED</span>
              <strong className="snapshot-value">{chain ? formatTime(chain.as_of) : "—"}</strong>
              <small>Refresh to request a new snapshot</small>
            </article>
          </section>

          </> : null}
          {activeSection === "algo" ? <>
          <section className="paper-workspace" aria-label="Paper trading workspace">
            <div className="paper-heading">
              <div>
                <div className="panel-kicker">PAPER TRADING</div>
                <h2>Your strategies</h2>
                <p className="muted">Plan. Monitor. Review. Paper orders only.</p>
              </div>
              <div className="paper-actions">
                <button className="refresh-button" type="button" disabled={chainBusy || paperBusy} onClick={() => void refreshPaperWorkspace()}>↻ Refresh</button>
                <button aria-describedby="batch-paper-safety" className="primary-button" disabled={!batchArmReady} onClick={() => void armAllPaperTemplates()} type="button">{paperBusy ? "Applying paper strategies…" : "Apply all eligible paper strategies"}</button>
                {paperAccount ? (
                  <span className={`paper-mode-badge ${paperAccount.kill_switch_enabled ? "paused" : "active"}`}>
                    {paperAccount.kill_switch_enabled ? "ENTRIES PAUSED" : "PAPER ACTIVE"}
                  </span>
                ) : null}
                <button className={paperAccount?.kill_switch_enabled ? "primary-button" : "refresh-button"}
                  disabled={paperBusy || !paperAccount} onClick={togglePaperKillSwitch} type="button">
                  {paperAccount?.kill_switch_enabled ? "Resume entries" : "Pause entries"}
                </button>
              </div>
            </div>
            <p className="features-note" id="batch-paper-safety">One click checks the paper-supported NIFTY option templates for the selected expiry, strike and lot size. {savedBatchPolicyCount} matching saved {savedBatchPolicyCount === 1 ? "policy" : "policies"} for {paperLots} {paperLots === 1 ? "lot" : "lots"}. Up to 26 saved policies may wait in the shared paper account; the worker checks five per cycle. Actual entries remain subject to cash, daily-loss, per-trade, portfolio-risk and open-position limits. Futures strategies are not yet paper-enabled. Arming opens no position immediately; live orders remain disabled.</p>
            {savedBatchPolicyCount < paperStrategies.length && <p className="features-note">The batch cannot arm templates without saved entry and exit policies. Proposed rules are exploratory and need your review; saving does not arm a strategy. <button className="inline-button" onClick={reviewNextBatchPolicy} type="button">Review next missing policy</button> <button className="inline-button" disabled={selectedOptionIndex !== "NIFTY" || !chain || !selectedPaperStrike || selectedExpiry !== chain.expiry || selectedExpiry < indiaCalendarDate() || batchPolicyBusy || strategyPayoffKey !== `${chain.as_of}|${chain.expiry}|${selectedPaperStrike}|${paperLots}`} onClick={reviewAllBatchPolicies} type="button">Review all missing policies</button></p>}
            {batchPolicyReview && <details className="workspace-disclosure" open><summary>Review batch policy proposals · {batchPolicyReview.rows.filter((row) => row.policy).length} reviewable</summary>
              <p>Snapshot {new Date(batchPolicyReview.asOf).toLocaleString("en-IN")} · expiry {batchPolicyReview.expiry} · strike {formatPrice(batchPolicyReview.strike)} · {batchPolicyReview.lots} lot(s) · quote quality {batchPolicyReview.quality}. The entry filters and exits below are hypotheses derived from an indicative gross payoff, not validated trading rules or profit predictions. <a href="https://upstox.com/brokerage-charges/" rel="noreferrer" target="_blank">Upstox public-plan charges</a> are a separate scenario, not deducted from these gross risk values.</p>
              <div className="paper-table-scroll"><table className="paper-table"><thead><tr><th>Strategy</th><th>Review status</th><th>Entry and exit proposal</th><th>Gross max loss</th><th>Round-trip cost scenario</th></tr></thead><tbody>
                {batchPolicyReview.rows.map((row) => <tr key={row.key}><td>{row.name} · {row.version}</td><td>{row.status}</td><td>{row.policy ? <>{row.policy.conditions.map((condition) => `${ENTRY_RULE_METRICS.find((metric) => metric.key === condition.metric)?.label || condition.metric} ${operatorLabel(condition.operator)} ${condition.threshold}`).join(" · ")}<br />Stop ₹{formatPrice(String(row.policy.stopLoss))} · target ₹{formatPrice(String(row.policy.targetProfit))} · trail ₹{formatPrice(String(row.policy.trailingStop))}</> : "—"}</td><td>{row.maximumLoss === null ? "Unavailable" : `₹${formatPrice(row.maximumLoss)}`}</td><td>{row.costScenario === null ? "Unavailable" : `₹${formatPrice(row.costScenario)}`}</td></tr>)}
              </tbody></table></div>
              <p>Review these exploratory entry/exit rules before applying. The button saves immutable account-scoped versions and queues every eligible strategy in one action. It opens no position immediately; later entries still require a qualifying paper snapshot and pass the cash, daily-loss, per-trade, portfolio-risk and open-position checks. Strategies without a finite quote-backed loss or with failed quote quality are excluded. These rules are not validated trading rules or profit predictions.</p>
              <div><button className="refresh-button" disabled={!token || batchPolicyBusy || paperBusy || selectedOptionIndex !== "NIFTY" || selectedExpiry !== batchPolicyReview.expiry || selectedExpiry < indiaCalendarDate() || !batchPolicyReview.rows.some((row) => row.policy) || batchPolicyReview.snapshot !== strategyPayoffKey || batchPolicyReview.snapshot !== `${chain?.as_of}|${chain?.expiry}|${selectedPaperStrike}|${paperLots}`} onClick={() => void saveReviewedBatchPolicies()} type="button">{batchPolicyBusy ? "Saving and applying reviewed strategies…" : `Save and apply ${batchPolicyReview.rows.filter((row) => row.policy).length} reviewed strategies`}</button></div>
              {batchPolicyReview.snapshot !== `${chain?.as_of}|${chain?.expiry}|${selectedPaperStrike}|${paperLots}` && <p role="status">The selected snapshot changed. Review the policies again before saving.</p>}
              {batchPolicyResults.length > 0 && <ul role="status">{batchPolicyResults.map((item) => <li key={item.name}><strong>{item.name}:</strong> {item.result}</li>)}</ul>}
            </details>}
            {batchArmResults.length > 0 && <details className="workspace-disclosure" open><summary>Batch paper application · {batchArmResults.filter(item => item.result.startsWith("Armed")).length} armed, {batchArmResults.length} checked</summary><p>{batchArmContext}</p><ul>{batchArmResults.map(item => <li key={item.name}><strong>{item.name}:</strong> {item.result}</li>)}</ul></details>}

            <section className="paper-readiness" aria-label="Paper trading readiness">
              <div className="paper-readiness-grid">
                <span className={paperAccount ? "ready" : "waiting"}><b>{paperAccount ? "Ready" : "Waiting"}</b> paper account</span>
                <span className={paperEntry.ready ? "ready" : "waiting"}><b>{paperEntry.ready ? "Ready" : "Waiting"}</b> account verification</span>
                <span className={chain?.quality?.status === "PASS" ? "ready" : chain?.quality?.status === "FAIL" ? "blocked" : "waiting"}><b>{chain?.quality?.status || "Waiting"}</b> quote quality</span>
                <span className={paperAccount?.live_trading_enabled === false ? "ready" : "blocked"}><b>{paperAccount?.live_trading_enabled === false ? "Disabled" : "Check"}</b> live broker orders</span>
              </div>
              <details className="workspace-disclosure"><summary>Entry checks &amp; fill policy</summary><p className="features-note">A strategy stays armed until a fresh qualifying snapshot passes its entry rules, risk limits, and quote checks. The server rechecks every condition before filling. Simulated buys use ask and sells use bid; no order is sent to a broker.</p></details>
            </section>

            {paperAccount ? (
              <>
              <div className="paper-stats" aria-label="Paper account summary">
                <div><span>Cash balance</span><strong>₹{formatPrice(paperAccount.cash_balance)}</strong></div>
                <div><span>Open positions</span><strong>{formatCount(paperAccount.open_trade_count)}</strong></div>
                <div><span>Today’s realized P&amp;L</span><strong className={changeClass(paperAccount.daily_realized_pnl)}>{formatSignedPrice(paperAccount.daily_realized_pnl)}</strong></div>
                <div><span>Remaining new-trade risk</span><strong>₹{formatPrice(paperAccount.daily_risk_remaining)}</strong></div>
              </div>
              {Number(paperAccount.cash_balance) < 5_000_000 ? <div className="paper-cash-adjustment">
                <p className="features-note">NIFTY F&amp;O simulated wallet only. This does not add broker funds or change the original starting-capital risk base.</p>
                <button className="refresh-button" disabled={!token || walletFundingBusy || paperLedger?.status === "MISMATCH"} onClick={() => void fundPaperWalletToFiftyLakh()} type="button">{walletFundingBusy ? "Recording paper cash…" : "Increase NIFTY F&O paper wallet to ₹50 lakh"}</button>
                {walletFundingMessage ? <p role="status">{walletFundingMessage}</p> : null}
                {walletFundingError ? <p className="error-banner" role="alert">{walletFundingError}</p> : null}
              </div> : null}
              </>
            ) : <p className="features-note">Loading paper account…</p>}
            <details className="workspace-disclosure paper-risk-details"><summary>Allocation &amp; risk details</summary>
              {paperAccount ? <div className="paper-stats">
                <div><span>Starting capital</span><strong>₹{formatPrice(paperAccount.initial_cash)}</strong></div>
                <div><span>Open maximum loss</span><strong>₹{formatPrice(paperAccount.open_max_loss)}</strong></div>
                <div><span>Daily loss budget</span><strong>₹{formatPrice(paperAccount.daily_loss_limit)}</strong></div>
              </div> : null}
              <p className="features-note">The daily budget uses realized losses and open positions’ theoretical maximum loss. It does not include live mark-to-market or transaction costs.</p>
            </details>

            <details className="paper-ledger-review">
              <summary>Paper ledger check</summary>
              <p className="features-note">Compare saved F&amp;O paper cash with simulated orders and trade records. A mismatch pauses new paper cash actions until reviewed; you can still pause entries with the kill switch. This checks internal arithmetic only, not exchange prices or expired-position settlement.</p>
              <button className="inline-button" disabled={paperLedgerBusy} onClick={() => void checkPaperLedger()} type="button">{paperLedgerBusy ? "Checking…" : "Check ledger now"}</button>
              {paperLedgerError ? <p className="error-banner" role="alert">{paperLedgerError}</p> : null}
              {paperLedger ? <div className={`paper-ledger-result ${paperLedger.status === "MISMATCH" ? "paper-ledger-result--mismatch" : ""}`} role="status">
                <strong>{paperLedger.status === "MATCHED" ? "Saved ledger matches" : `${paperLedger.issue_count} ledger issue${paperLedger.issue_count === 1 ? "" : "s"} found`}</strong>
                <p>Recorded cash ₹{formatPrice(paperLedger.recorded_cash_balance)} · Expected cash ₹{formatPrice(paperLedger.expected_cash_balance)} · Difference {formatSignedPrice(paperLedger.cash_difference)}</p>
                <p>{paperLedger.order_count} orders · {paperLedger.open_trade_count} open trades · {paperLedger.closed_trade_count} closed trades{paperLedgerCheckedAt ? ` · Checked ${formatTime(paperLedgerCheckedAt)}` : ""}</p>
                {paperLedger.issues.length > 0 ? <ul>{paperLedger.issues.map((issue, index) => <li key={`${index}-${issue}`}>{issue}</li>)}</ul> : null}
              </div> : null}
            </details>

            {token && selectedOptionIndex === "NIFTY" ? (
              <StrategyRanking
                apiUrl={API_URL}
                chain={chain}
                onUse={(key) => {
                  setSelectedStrategy(key); setDeploymentPreparedKey(""); setPaperTicketMessage("");
                  const builder = document.getElementById("paper-strategy-builder");
                  if (builder instanceof HTMLDetailsElement) { builder.open = true; builder.scrollIntoView({ behavior: "smooth", block: "start" }); }
                }}
                referenceStrike={selectedPaperStrike}
                token={token}
              />
            ) : null}

            <details className="strategy-builder strategy-composer" id="paper-strategy-builder"><summary>Add a paper strategy</summary>
              <div className={`strategy-controls ${isCustomStrategy ? "custom-strategy-controls" : ""}`}>
                <label>
                  Strategy
                  <select disabled={paperBusy || paperStrategies.length === 0} onChange={(event) => { setSelectedStrategy(event.target.value); setDeploymentPreparedKey(""); setPaperTicketMessage(""); }} value={selectedStrategy}>
                    {paperStrategies.map((strategy) => <option key={strategy.key} value={strategy.key}>{strategy.name}</option>)}
                    {STRATEGY_CATALOG.filter((entry) => !paperStrategies.some((strategy) => strategy.key === entry.key)).map((entry) => <option disabled key={`catalog-${entry.key}`} value={entry.key}>{entry.name} · {entry.status === "ENGINE_SUPPORT_PENDING" ? "engine support pending" : "research only"}</option>)}
                    <option value="custom">Custom multi-leg</option>
                  </select>
                </label>
                {!isCustomStrategy ? (
                  <label>
                    Reference strike
                    <select disabled={paperBusy || strikeRows.length === 0} onChange={(event) => { setSelectedPaperStrike(event.target.value); setDeploymentPreparedKey(""); setPaperTicketMessage(""); }} value={selectedPaperStrike}>
                      {strikeRows.map((row) => <option key={row.strike} value={row.strike}>{formatPrice(row.strike)}</option>)}
                    </select>
                  </label>
                ) : null}
                <label>
                  Lots
                  <input disabled={paperBusy} max={10} min={1} onChange={(event) => { setPaperLots(Math.max(1, Math.min(10, Math.trunc(Number(event.target.value) || 1)))); setDeploymentPreparedKey(""); setPaperTicketMessage(""); }} type="number" value={paperLots} />
                </label>
              </div>
              <div className="strategy-ranking-heading">
                <div><strong>NIFTY market-fit research preview</strong><small>Frozen vertical/range exposure recipes · no automatic entry</small></div>
                <span>{marketFit?.regime || "UNKNOWN"} · NO TRADE</span>
              </div>
              <div className="strategy-color-legend" aria-label="Strategy evidence legend"><span className="strategy-color-legend--green">Green · exposure matches observed regime</span><span className="strategy-color-legend--amber">Amber · regime fit unknown</span><span className="strategy-color-legend--red">Red · quote or risk gate blocked</span></div>
              <div className="strategy-bias-filter" aria-label="Strategy market bias filter">
                <span>Market view</span>
                {(["ALL", "BULLISH", "BEARISH", "SIDEWAYS", "VOLATILITY", "INCOME", "SPECIAL"] as const).map((bias) => (
                  <button className={`strategy-bias-chip strategy-bias-chip--${bias.toLowerCase()} ${strategyBiasFilter === bias ? "selected" : ""}`} key={bias} onClick={() => { setStrategyBiasFilter(bias); setShowAllStrategies(false); }} type="button">
                    {bias === "ALL" ? "All" : bias[0] + bias.slice(1).toLowerCase()}
                  </button>
                ))}
              </div>
              <p className="strategy-library-note">Public taxonomy reference: OptionStrat’s strategy list. TradingPilot uses original summaries and only enables the defined-risk templates supported by its paper payoff engine; this classification is descriptive, not a profit forecast.</p>
              <p className="strategy-library-note" role="status">{marketFitError || (marketFit ? `Decision: no trade. ${marketFit.reasons.join(" ")}` : "Checking current NIFTY market-fit inputs; no trade is recommended.")}</p>
              <button className="inline-button" disabled={!chain || !selectedPaperStrike || paperBusy || strategyPayoffKey !== `${chain.as_of}|${chain.expiry}|${selectedPaperStrike}|${paperLots}`} onClick={compareAllPaperTemplates} type="button">Compare all paper templates at this snapshot</button>
              {batchComparison ? <details className="workspace-disclosure" open><summary>All-template quote and payoff comparison · {batchComparison.rows.length} templates</summary>
                <p>Snapshot {new Date(batchComparison.asOf).toLocaleString("en-IN")} · expiry {batchComparison.expiry} · reference strike {formatPrice(batchComparison.strike)} · {batchComparison.lots} lot(s). Gross expiry payoff and a separate <a href="https://upstox.com/brokerage-charges/" rel="noreferrer" target="_blank">public Upstox standard-plan charge scenario</a> are shown. The scenario assumes one order per leg and unchanged premiums at exit; your actual plan, exit prices, settlement, impact and discounts may differ. It creates no orders, positions, research outcomes, backtest, or probability estimate.</p>
                <div className="paper-table-scroll"><table className="paper-table"><thead><tr><th>Strategy</th><th>Availability</th><th>Max expiry profit</th><th>Max expiry loss</th><th>Round-trip cost scenario</th></tr></thead><tbody>
                  {batchComparison.rows.map((row) => <tr key={row.key}><td>{row.name} · {row.version}</td><td>{row.status}</td><td>{row.maximumProfit === "Unavailable" || row.maximumProfit === "Unbounded" ? row.maximumProfit : `₹${formatPrice(row.maximumProfit)}`}</td><td>{row.maximumLoss === "Unavailable" || row.maximumLoss === "Unbounded" ? row.maximumLoss : `₹${formatPrice(row.maximumLoss)}`}</td><td>{row.costScenario === null ? "Unavailable" : `₹${formatPrice(row.costScenario)}`}</td></tr>)}
                </tbody></table></div>
                <p>Catalog-only and engine-pending structures are excluded. The cost scenario is not deducted from gross payoff or paper cash and is not an after-cost ranking. Required entry and exit policies, account-specific costs, capital, quote timing, and market fit need separate validation before any paper application. An unavailable row remains unavailable; it is never filled with an estimated price.</p>
              </details> : null}
              <details className="workspace-disclosure probability-evidence"><summary>Win / loss probability · Insufficient evidence</summary>
                <p>No validated forecast is available. Historical paper win frequency is not a prediction for the next trade.</p>
                <dl className="probability-inputs">
                  <div><dt>Price snapshot</dt><dd>{chain?.underlying_spot ? `₹${formatPrice(chain.underlying_spot)}` : "Unavailable"}</dd></div>
                  <div><dt>Volume put/call ratio</dt><dd>{optionFeatures?.put_call_ratio_volume == null ? "Unavailable" : formatPrice(optionFeatures.put_call_ratio_volume)}</dd></div>
                  <div><dt>OI put/call ratio</dt><dd>{optionFeatures?.put_call_ratio_oi == null ? "Unavailable" : formatPrice(optionFeatures.put_call_ratio_oi)}</dd></div>
                  <div><dt>ATM call / put IV</dt><dd>{optionFeatures?.atm_call_iv_pct == null ? "—" : `${formatPrice(optionFeatures.atm_call_iv_pct)}%`} / {optionFeatures?.atm_put_iv_pct == null ? "—" : `${formatPrice(optionFeatures.atm_put_iv_pct)}%`}</dd></div>
                  <div><dt>Median option spread</dt><dd>{optionFeatures?.median_relative_spread_pct == null ? "Unavailable" : `${formatPrice(optionFeatures.median_relative_spread_pct)}%`}</dd></div>
                  <div><dt>News / price trend model</dt><dd>Not integrated</dd></div>
                </dl>
                <p>{optionFeatures ? `Option features observed ${new Date(optionFeatures.as_of).toLocaleString("en-IN")}. ` : "Option features unavailable. "}These snapshots are candidate inputs only; missing or stale data cannot support a probability.</p>
                <p>Target outcome: positive P&amp;L after declared costs under the saved entry and exit rules. Win, loss and break-even will be separate outcomes. No validation period, calibrated model or after-cost validation sample exists yet.</p>
              </details>
              <section className="strategy-scorecards" aria-label="Strategy performance comparison">
                {filteredRankedStrategies.slice(0, showAllStrategies ? undefined : 3).map(({ strategy, candidate, payoff }) => (
                  <article
                    className={`strategy-scorecard ${selectedStrategy === strategy.key ? "selected" : ""} strategy-scorecard--${candidate.fit === "MATCH" ? "green" : candidate.fit === "UNKNOWN" ? "amber" : "red"} strategy-bias--${(STRATEGY_CATALOG_META[strategy.key]?.bias || "SIDEWAYS").toLowerCase()}`}
                    key={strategy.key}
                  >
                    <em>{candidate.fit === "MATCH" ? "Exposure match · research only" : candidate.fit === "BLOCKED" ? "Blocked" : "Fit unknown"}</em>
                    <strong>{strategy.name}</strong>
                    <span className="strategy-bias-tag">{STRATEGY_CATALOG_META[strategy.key]?.label || "Research"} · {STRATEGY_CATALOG_META[strategy.key]?.note || "Paper template"}</span>
                    <small className="strategy-fit">Regime: {marketFit?.regime || "UNKNOWN"} · POP unavailable</small>
                    <div className="strategy-card-metrics">
                      <span>Max modeled profit<b>{payoff?.maximum_profit_unbounded ? "Unbounded*" : payoff?.maximum_profit !== null && payoff?.maximum_profit !== undefined ? `₹${formatPrice(payoff.maximum_profit)}` : "Calculating…"}</b></span>
                      <span>Max modeled loss<b>{payoff?.maximum_loss_unbounded ? "Unbounded" : payoff?.maximum_loss !== null && payoff?.maximum_loss !== undefined ? `₹${formatPrice(payoff.maximum_loss)}` : "Calculating…"}</b></span>
                      <span>Expiry breakeven / unit<b>{candidate.breakevens.length ? candidate.breakevens.map(formatPrice).join(" / ") : "Unavailable"}</b></span>
                      <span>Policy stop / target / unit<b>{candidate.provisional_stop && candidate.provisional_target ? `${formatPrice(candidate.provisional_stop)} / ${formatPrice(candidate.provisional_target)}` : "Unavailable"}</b></span>
                      <span>Round-trip cost scenario<b>{payoff?.transaction_cost_scenario ? `₹${formatPrice(payoff.transaction_cost_scenario.round_trip_scenario_charge)}` : "Unavailable"}</b></span>
                    </div>
                    <small>{candidate.legs.length ? candidate.legs.map((leg) => `${leg.side} ${leg.quantity} ${formatPrice(leg.strike)} ${leg.option_type} @ ${formatPrice(leg.entry_quote)}`).join(" · ") : "Complete quote-backed basket unavailable"}</small>
                    <small>{candidate.reasons.join(" ") || "Quote-backed exposure matches the descriptive regime; entry remains unqualified."}</small>
                    <details><summary>Entry and exit research recipe</summary><p>{candidate.entry_policy}</p><p>{candidate.exit_policy}</p><p>Expiry maximum profit/loss above is before costs and assumes intact legs. Stops are triggers, not maximum losses.</p></details>
                    <button className="strategy-apply" disabled={paperBusy || !chain || chain.quality?.status === "FAIL" || !payoff || payoff.maximum_loss_unbounded || payoff.maximum_loss === null} onClick={() => applyStrategyToPaperTicket(strategy, payoff)} type="button">
                      {deploymentPreparedKey === strategy.key ? "Paper ticket prepared" : "Inspect paper ticket"}
                    </button>
                  </article>
                ))}
              </section>
              <button className="inline-button" aria-expanded={showAllStrategies} onClick={() => setShowAllStrategies(value => !value)} type="button">{showAllStrategies ? "Show fewer strategies" : `＋ All ${filteredRankedStrategies.length} ${strategyBiasFilter === "ALL" ? "strategies" : `${strategyBiasFilter.toLowerCase()} strategies`}`}</button>
              <details className="workspace-disclosure"><summary>Ranking &amp; evidence details</summary><p className="features-note">This backend-owned, versioned comparison orders exposure match for a directional vertical family (bull or bear) and a range condor. The input chain is submitted by this browser and quality-rechecked, not independently signed or stored by the selector. It uses complete 5-minute index bars when available and selected-leg bid/ask for theoretical expiry payoff per option unit, before costs. Missing bar, quote, timing, cost or capital evidence keeps the decision at no trade. The displayed stop/target are research-policy hypotheses, not active settings. A calibrated after-cost probability and demonstrated competitive ranking advantage require future independent validation.</p></details>
              <section className="strategy-catalog" aria-label="Full public strategy catalog">
                <div className="strategy-catalog-heading"><div><strong>Full public strategy catalog</strong><small aria-live="polite">{visibleCatalogEntries.length} of {STRATEGY_CATALOG.length} public templates · paper-ready entries first</small></div><span>Execution status is explicit</span></div>
                <div className="strategy-catalog-status-summary" aria-label="Strategy catalog availability summary">
                  <span className="strategy-catalog-status-chip strategy-catalog-status-chip--paper"><b>{catalogStatusCounts.PAPER_SUPPORTED}</b> paper supported</span>
                  <span className="strategy-catalog-status-chip strategy-catalog-status-chip--pending"><b>{catalogStatusCounts.ENGINE_SUPPORT_PENDING}</b> engine pending</span>
                  <span className="strategy-catalog-status-chip strategy-catalog-status-chip--research"><b>{catalogStatusCounts.RESEARCH_ONLY}</b> research only</span>
                </div>
                <div className="strategy-catalog-controls">
                  <label>Search strategies<input aria-label="Search strategy catalog" onChange={(event) => setCatalogSearch(event.target.value)} placeholder="Name, bias or description" type="search" value={catalogSearch} /></label>
                  <label>Availability<select aria-label="Filter strategy availability" onChange={(event) => setCatalogStatusFilter(event.target.value as StrategyCatalogStatus | "ALL")} value={catalogStatusFilter}>
                    <option value="ALL">All statuses</option>
                    <option value="PAPER_SUPPORTED">Paper supported</option>
                    <option value="ENGINE_SUPPORT_PENDING">Engine support pending</option>
                    <option value="RESEARCH_ONLY">Research only</option>
                  </select></label>
                  {(catalogSearch || catalogStatusFilter !== "ALL") ? <button className="inline-button" onClick={() => { setCatalogSearch(""); setCatalogStatusFilter("ALL"); }} type="button">Clear catalog filters</button> : null}
                </div>
                <div className="strategy-catalog-grid">
                  {visibleCatalogEntries.map((entry) => (
                    <article className={`strategy-catalog-card strategy-catalog-card--${entry.bias.toLowerCase()} strategy-catalog-card--${entry.status.toLowerCase()}`} key={entry.key}>
                      <div className="strategy-catalog-card-top"><strong>{entry.name}</strong><span>{entry.proficiency}</span></div>
                      <span className="strategy-bias-tag">{entry.label}</span>
                      <p>{entry.summary}</p>
                      <small>{entry.status === "PAPER_SUPPORTED" ? "Available in paper selector" : entry.status === "ENGINE_SUPPORT_PENDING" ? "Cataloged · engine support pending" : "Cataloged · research only"}</small>
                    </article>
                  ))}
                </div>
                {!visibleCatalogEntries.length ? <p className="catalog-empty muted">No catalog entries match these filters. Clear the filters to view the complete taxonomy.</p> : null}
              </section>
              {isCustomStrategy ? (
                <div className="custom-leg-builder">
                  <div className="custom-leg-heading">
                    <span>Custom legs <small>1–4 legs · quantity ratios multiply the Lots setting</small></span>
                    <button className="inline-button" disabled={paperBusy || customLegDrafts.length >= 4 || strikeRows.length === 0} onClick={() => setCustomLegDrafts((legs) => [...legs, { option_type: "CE", side: "BUY", strike: selectedPaperStrike || strikeRows[0]?.strike || "", ratio: 1 }])} type="button">Add leg</button>
                  </div>
                  {customLegDrafts.map((leg, index) => (
                    <div className="custom-leg-row" key={`custom-leg-${index}`}>
                      <label>
                        Option
                        <select disabled={paperBusy} onChange={(event) => setCustomLegDrafts((legs) => legs.map((item, itemIndex) => itemIndex === index ? { ...item, option_type: event.target.value as "CE" | "PE" } : item))} value={leg.option_type}>
                          <option value="CE">Call (CE)</option><option value="PE">Put (PE)</option>
                        </select>
                      </label>
                      <label>
                        Action
                        <select disabled={paperBusy} onChange={(event) => setCustomLegDrafts((legs) => legs.map((item, itemIndex) => itemIndex === index ? { ...item, side: event.target.value as "BUY" | "SELL" } : item))} value={leg.side}>
                          <option value="BUY">Buy</option><option value="SELL">Sell</option>
                        </select>
                      </label>
                      <label>
                        Strike
                        <select disabled={paperBusy || strikeRows.length === 0} onChange={(event) => setCustomLegDrafts((legs) => legs.map((item, itemIndex) => itemIndex === index ? { ...item, strike: event.target.value } : item))} value={leg.strike}>
                          {strikeRows.map((row) => <option key={row.strike} value={row.strike}>{formatPrice(row.strike)}</option>)}
                        </select>
                      </label>
                      <label>
                        Ratio
                        <input disabled={paperBusy} max={10} min={1} onChange={(event) => setCustomLegDrafts((legs) => legs.map((item, itemIndex) => itemIndex === index ? { ...item, ratio: Math.max(1, Math.min(10, Math.trunc(Number(event.target.value) || 1))) } : item))} type="number" value={leg.ratio} />
                      </label>
                      <button aria-label={`Remove leg ${index + 1}`} className="inline-button custom-leg-remove" disabled={paperBusy} onClick={() => setCustomLegDrafts((legs) => legs.filter((_, itemIndex) => itemIndex !== index))} type="button">Remove</button>
                    </div>
                  ))}
                  {!customLegDrafts.length ? <p className="features-note">Add option legs to build your position. Unbounded-risk combinations will be rejected.</p> : null}
                  {hasDuplicateCustomContracts ? <p className="error-banner" role="alert">Remove duplicate option contracts from this structure.</p> : null}
                </div>
              ) : null}
              <div className="entry-rule-builder">
                <div className="entry-rule-heading">
                  <span>Entry conditions <small>Optional · all conditions must pass</small></span>
                  <button className="inline-button" disabled={paperBusy || entryConditions.length >= 5} onClick={() => setEntryConditions((conditions) => [...conditions, { metric: "put_call_ratio_oi", operator: "GTE", threshold: "1" }])} type="button">Add condition</button>
                </div>
                <p className="exit-rule-note">Filters use option-chain metrics. Values below are indicative; the API rechecks every condition against a fresh snapshot before paper entry.</p>
                {entryConditions.map((condition, index) => {
                  const preview = entryRulePreviews[index];
                  const metricInfo = ENTRY_RULE_METRICS.find((metric) => metric.key === condition.metric);
                  return (
                    <div className="entry-rule-row" key={`entry-rule-${index}`}>
                      <label>
                        Market metric
                        <select disabled={paperBusy} onChange={(event) => setEntryConditions((conditions) => conditions.map((item, itemIndex) => itemIndex === index ? { ...item, metric: event.target.value as RuleMetric } : item))} value={condition.metric}>
                          {ENTRY_RULE_METRICS.map((metric) => <option key={metric.key} value={metric.key}>{metric.label}</option>)}
                        </select>
                      </label>
                      <label>
                        Compare
                        <select disabled={paperBusy} onChange={(event) => setEntryConditions((conditions) => conditions.map((item, itemIndex) => itemIndex === index ? { ...item, operator: event.target.value as RuleOperator } : item))} value={condition.operator}>
                          <option value="GT">Greater than</option><option value="GTE">At least</option><option value="LT">Less than</option><option value="LTE">At most</option>
                        </select>
                      </label>
                      <label>
                        Threshold ({metricInfo?.unit})
                        <input disabled={paperBusy} onChange={(event) => setEntryConditions((conditions) => conditions.map((item, itemIndex) => itemIndex === index ? { ...item, threshold: event.target.value } : item))} step="any" type="number" value={condition.threshold} />
                      </label>
                      <span className={`entry-rule-result ${preview?.passed === true ? "passed" : preview?.passed === false ? "failed" : "unknown"}`}>
                        {preview?.passed === true ? "Pass" : preview?.passed === false ? "Not met" : "Unavailable"}
                        {preview?.actual !== null && preview?.actual !== undefined ? ` · ${formatPrice(preview.actual)}` : ""}
                      </span>
                      <button aria-label={`Remove condition ${index + 1}`} className="inline-button" disabled={paperBusy} onClick={() => setEntryConditions((conditions) => conditions.filter((_, itemIndex) => itemIndex !== index))} type="button">Remove</button>
                    </div>
                  );
                })}
                {!entryConditions.length ? <p className="features-note">No entry filters. The selected position will still pass the existing quote and risk checks.</p> : null}
              </div>
              <details className="preset-panel" aria-label="Saved strategy presets"><summary>Saved presets</summary>
                <div className="preset-heading">
                  <strong>Saved presets</strong>
                  <small>Save a template and its entry conditions. Expiry, strike, lots, custom legs, and exit rules are not saved.</small>
                </div>
                <div className="preset-controls">
                  <label htmlFor="preset-name">Preset name</label>
                  <input id="preset-name" maxLength={64} onChange={(event) => setPresetName(event.target.value)} placeholder="e.g. NIFTY call filter" type="text" value={presetName} />
                  <button className="refresh-button" disabled={presetBusy || isCustomStrategy || !presetName.trim() || !entryRuleInputsValid} onClick={() => void savePaperPreset()} type="button">Save current</button>
                </div>
                {isCustomStrategy ? <p className="features-note">Custom multi-leg structures cannot be saved as presets yet.</p> : null}
                <div className="preset-controls">
                  <label htmlFor="saved-preset">Your presets</label>
                  <select id="saved-preset" onChange={(event) => setSelectedPresetId(event.target.value)} value={selectedPresetId}>
                    <option value="">{paperPresets.length ? "Choose a saved preset" : "No saved presets"}</option>
                    {paperPresets.map((preset) => <option key={preset.id} value={preset.id}>{preset.name}</option>)}
                  </select>
                  <button className="refresh-button" disabled={presetBusy || !selectedPresetId} onClick={loadPaperPreset} type="button">Load</button>
                  <button className="inline-button preset-delete" disabled={presetBusy || !selectedPresetId} onClick={() => void deletePaperPreset()} type="button">Delete</button>
                </div>
                {presetError ? <p className="error-banner" role="alert">{presetError}</p> : null}
                {presetMessage ? <p className="preset-message" role="status">{presetMessage}</p> : null}
              </details>
              <div className="exit-rule-controls" aria-label="Optional paper exit rules">
                <label>
                  Stop loss (₹ P&amp;L)
                  <input aria-label="Stop loss amount in rupees" min="0.01" onChange={(event) => setStopLossInput(event.target.value)} placeholder="Off" step="0.01" type="number" value={stopLossInput} />
                </label>
                <label>
                  Profit target (₹ P&amp;L)
                  <input aria-label="Profit target amount in rupees" min="0.01" onChange={(event) => setTargetProfitInput(event.target.value)} placeholder="Off" step="0.01" type="number" value={targetProfitInput} />
                </label>
                <label>
                  Trailing stop (₹ drawdown)
                  <input aria-label="Trailing stop amount in rupees" min="0.01" onChange={(event) => setTrailingStopInput(event.target.value)} placeholder="Off" step="0.01" type="number" value={trailingStopInput} />
                </label>
              </div>
              <details className="preset-panel" id="strategy-version-panel" aria-label="Immutable strategy versions"><summary>Strategy versions & audit</summary>
                <div className="preset-heading">
                  <strong>Strategy versions</strong>
                  <small>Save the template, size, entry conditions, and exits. Each revision remains available for audit.</small>
                </div>
                <div className="preset-controls">
                  <label htmlFor="strategy-version-name">Version name</label>
                  <input id="strategy-version-name" maxLength={64} onChange={(event) => setStrategyVersionName(event.target.value)} placeholder="e.g. NIFTY call plan" type="text" value={strategyVersionName} />
                  <button className="refresh-button" disabled={versionBusy || isCustomStrategy || !strategyVersionName.trim() || !entryRuleInputsValid} onClick={() => void saveStrategyVersion()} type="button">Save version</button>
                </div>
                <div className="preset-controls">
                  <label htmlFor="strategy-version">Saved versions</label>
                  <select id="strategy-version" onChange={(event) => { setSelectedVersionId(event.target.value); setCompareVersionId(""); }} value={selectedVersionId}>
                    <option value="">No version pinned</option>
                    {strategyVersions.map((version) => <option key={version.id} value={version.id}>{version.name} · v{version.version_number} · {version.definition.option_index?.symbol ?? "NIFTY"}</option>)}
                  </select>
                  <button className="refresh-button" disabled={versionBusy || !selectedVersionId} onClick={loadStrategyVersion} type="button">Load</button>
                  <button className="inline-button" disabled={!selectedVersionId} onClick={() => { setSelectedVersionId(""); setVersionMessage("Version unpinned. Current form remains editable."); }} type="button">Unpin</button>
                </div>
                <div className="preset-controls">
                  <label htmlFor="compare-strategy-version">Compare with</label>
                  <select id="compare-strategy-version" disabled={!selectedVersionId} onChange={(event) => setCompareVersionId(event.target.value)} value={compareVersionId}>
                    <option value="">Choose another version</option>
                    {strategyVersions.filter((version) => version.id !== selectedVersionId).map((version) => <option key={version.id} value={version.id}>{version.name} · v{version.version_number}</option>)}
                  </select>
                  <button className="inline-button" disabled={!selectedVersionId} onClick={exportStrategyVersion} type="button">Export JSON</button>
                </div>
                <div className="preset-controls">
                  <label htmlFor="strategy-import">Import JSON as draft</label>
                  <input accept=".json,application/json" id="strategy-import" onChange={(event) => {
                    const file = event.currentTarget.files?.[0];
                    if (file) void importStrategyDraft(file);
                    event.currentTarget.value = "";
                  }} type="file" />
                </div>
                <p className="features-note">Imported settings are editable and unpinned. Save them as a new version after review; the server validates the current template.</p>
                {pinnedVersion && comparisonVersion ? (
                  <div className="paper-table-scroll">
                    <p className="features-note">Changes from {comparisonVersion.name} v{comparisonVersion.version_number} to {pinnedVersion.name} v{pinnedVersion.version_number}</p>
                    {versionComparisonRows.length ? (
                      <table className="paper-table">
                        <thead><tr><th>Setting</th><th>Compared version</th><th>Selected version</th></tr></thead>
                        <tbody>{versionComparisonRows.map(([field, before, after]) => <tr key={field}><td>{field}</td><td>{before}</td><td>{after}</td></tr>)}</tbody>
                      </table>
                    ) : <p className="features-note">The definitions are identical. Their saved version records remain distinct.</p>}
                  </div>
                ) : null}
                {selectedVersionId ? <p className="features-note">Pinned version must match the current form exactly. Save a new revision after edits.</p> : null}
                {versionError ? <p className="error-banner" role="alert">{versionError}</p> : null}
                {versionMessage ? <p className="preset-message" role="status">{versionMessage}</p> : null}
              </details>
              <p className="exit-rule-note" ref={paperTicketRef}>Rules apply to total position P&amp;L. The trailing stop starts after unrealized P&amp;L becomes positive. Checks run every 15 seconds while the independent worker is running.</p>
              {paperTicketMessage ? <p className="paper-ticket-message" role="status">{paperTicketMessage}</p> : null}
              {deploymentPreparedKey === selectedStrategy ? <div className="deployment-ready"><strong>Ready to arm in paper mode</strong><span>The worker will wait for a later qualifying market snapshot. Fresh entry rules, quotes, session and risk are checked before any paper fill. It then monitors the moving stop and exits.</span></div> : null}
              {selectedPaperStrategy ? <p className="strategy-description">{selectedPaperStrategy.description}</p> : isCustomStrategy ? <p className="strategy-description">Compose a same-expiry multi-leg structure. The payoff engine checks expiry loss before paper entry; unbounded loss is blocked.</p> : null}
              {strategyPreview ? (
                <div className="strategy-preview">
                  <div>
                    <span className="stat-label">CURRENT QUOTE ESTIMATE</span>
                    <strong>
                      {strategyPreview.estimatedDebit > 0
                        ? `Debit ₹${formatPrice(String(strategyPreview.estimatedDebit))}`
                        : strategyPreview.estimatedDebit < 0
                          ? `Credit ₹${formatPrice(String(Math.abs(strategyPreview.estimatedDebit)))}`
                          : "Near-zero net premium"}
                    </strong>
                    <small>Indicative net premium for {paperLots} lot(s), using this chain snapshot.</small>
                    <strong className="preview-max-loss">
                      {payoffEstimate?.maximum_loss !== null && payoffEstimate?.maximum_loss !== undefined
                        ? `Max expiry loss ₹${formatPrice(payoffEstimate.maximum_loss)}`
                        : payoffEstimate?.maximum_loss_unbounded
                          ? "Unbounded maximum loss"
                          : payoffEstimateBusy
                            ? "Calculating maximum loss…"
                            : "Maximum loss unavailable"}
                    </strong>
                    <strong className="preview-max-loss">
                      {payoffEstimate?.maximum_profit !== null && payoffEstimate?.maximum_profit !== undefined
                        ? `Max expiry profit ₹${formatPrice(payoffEstimate.maximum_profit)}`
                        : payoffEstimate?.maximum_profit_unbounded
                          ? "Maximum profit is unbounded"
                          : payoffEstimateBusy
                            ? "Calculating maximum profit…"
                            : "Maximum profit unavailable"}
                    </strong>
                    {payoffEstimateError ? <small className="table-warning">{payoffEstimateError}</small> : null}
                  </div>
                  <div className="preview-legs">
                    {strategyPreview.legs.map((leg, index) => (
                      <span key={`${leg.symbol}-${index}`} className={leg.side === "BUY" ? "preview-buy" : "preview-sell"}>
                        {leg.side} {leg.symbol} · ₹{formatPrice(String(leg.price))}
                      </span>
                    ))}
                  </div>
                </div>
              ) : (
                <p className="features-note">{isCustomStrategy && !customLegDrafts.length ? "Add option legs to see the current quote and expiry risk." : "Select contracts with current two-sided quotes to preview this strategy."}</p>
              )}
              {chain?.quality?.status === "WARN" ? (
                <p className="paper-warning">This snapshot has WARN quality. Check the report above; timestamps may not be exchange-verified.</p>
              ) : null}
              {paperError ? <p className="error-banner paper-error" role="alert">{paperError}</p> : null}
              <button className="primary-button paper-submit" disabled={selectedOptionIndex !== "NIFTY" || paperBusy || !paperEntry.ready || !!paperEntry.pending || payoffEstimateBusy || !payoffEstimate || payoffEstimate.maximum_loss_unbounded || Number(payoffEstimate.maximum_loss) <= 0 || !entryRuleInputsValid || (isCustomStrategy && !entryConditionsSatisfied) || (!isCustomStrategy && (matchingWaitingPaperDeployment || deploymentPreparedKey !== selectedStrategy || !entryConditions.length || !stopLossInput || !trailingStopInput)) || (isCustomStrategy && hasDuplicateCustomContracts) || !paperAccount || paperAccount.kill_switch_enabled || !strategyPreview || !chain || chain.quality?.status === "FAIL"} onClick={() => void (isCustomStrategy ? openPaperTrade() : armPaperStrategy())} type="button">
                {paperBusy ? "Processing paper strategy…" : isCustomStrategy ? "Open custom paper trade" : matchingWaitingPaperDeployment ? "Strategy waiting for entry" : deploymentPreparedKey === selectedStrategy ? "Arm automatic paper entry" : "Apply a strategy above"}
              </button>
              <p className="paper-footnote">Arming a template does not reserve funds or enter now. At entry, risk caps are rechecked: up to 2% of starting capital per position and 10% across open positions. Simulated fills do not model queue priority, partial fills, charges, or slippage.</p>
            </details>

            {paperDeployments.length > 0 && <details className="equity-history" open={paperDeployments.some(item => item.status === "WAITING_FOR_ENTRY")}><summary>Armed F&amp;O strategies ({paperDeployments.filter(item => item.status === "WAITING_FOR_ENTRY").length} waiting)</summary><div className="equity-deployments">{paperDeployments.slice(0, 10).map(item => <article key={item.id}><div className="equity-deployment-heading"><strong>{paperStrategies.find(strategy => strategy.key === item.strategy_key)?.name || item.strategy_key} · {item.expiry}</strong><span>{item.status.replaceAll("_", " ")}</span></div><p>{item.detail}</p><small>Reference strike {formatPrice(item.reference_strike)} · {item.lots} lot(s) · Armed {new Date(item.created_at).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" })}{item.updated_at !== item.created_at ? ` · Last update ${new Date(item.updated_at).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" })}` : ""}</small>
              <details><summary>Saved entry &amp; exit rules</summary><div className="equity-deployment-rules">
                <p><strong>Entry:</strong> A later open-market option-chain snapshot must satisfy every saved condition: {item.entry_conditions.map(condition => `${ENTRY_RULE_METRICS.find(metric => metric.key === condition.metric)?.label || condition.metric} ${operatorLabel(condition.operator)} ${condition.threshold}`).join(" · ") || "No saved conditions"}. The selected option legs, quote quality, expiry window and paper risk checks must also pass.</p>
                <p><strong>Exit:</strong> Net paper P&amp;L stop at −{formatPrice(item.stop_loss_amount)}; {item.target_profit_amount ? `target at +${formatPrice(item.target_profit_amount)}` : "no profit target saved"}; moving stop trails {formatPrice(item.trailing_stop_amount)} below peak net P&amp;L after it turns positive. The expiry policy may close earlier.</p>
              </div></details>
              <OptionDeploymentDecisions deploymentId={item.id} apiUrl={API_URL} token={token} />{item.status === "WAITING_FOR_ENTRY" && <button className="inline-button" disabled={paperBusy} onClick={() => void cancelPaperDeployment(item.id)} type="button">Cancel waiting strategy</button>}</article>)}</div></details>}
            {paperWarnings.length ? <ul className="paper-warning-list">{paperWarnings.map((warning) => <li key={warning}>{warning}</li>)}</ul> : null}
            <section className="position-overview" aria-label="Paper positions">
              <div className="workspace-section-heading"><h3>Positions &amp; results</h3><span>{paperTrades.filter(trade => trade.status === "OPEN").length} unsettled · {expiredPaperTrades.length} expired awaiting review · {paperTrades.filter(trade => trade.status === "CLOSED").length} closed</span></div>
              <div className="position-view-tabs" aria-label="Position view">{(["OPEN", "CLOSED", "ALL"] as const).map(view => <button key={view} type="button" aria-pressed={positionView === view} onClick={() => setPositionView(view)}>{view === "OPEN" ? "Open / unsettled" : view === "CLOSED" ? "Closed trades" : "All positions"} <span>{paperTrades.filter(trade => view === "ALL" || trade.status === view).length}</span></button>)}</div>
              <div className="position-card-grid">{paperTrades.filter(trade => positionView === "ALL" || trade.status === positionView).map(trade => <article className={`position-card position-card--${trade.status.toLowerCase()}`} key={trade.id}>
                <div className="workspace-section-heading"><strong>{trade.strategy_name}</strong><span className={`trade-state trade-${trade.status === "OPEN" && trade.expiry < indiaCalendarDate() ? "expired" : trade.status.toLowerCase()}`}>{trade.status === "OPEN" && trade.expiry < indiaCalendarDate() ? "EXPIRED · SETTLEMENT PENDING" : trade.status}</span></div>
                <small>{trade.underlying} · Expiry {trade.expiry} · {trade.lots} lot(s)</small>
                <div className="position-contracts" aria-label="Position contracts">{trade.legs.map(leg => <span key={`${leg.side}-${leg.strike}-${leg.option_type}`}>{leg.side} {formatPrice(leg.strike)} {leg.option_type}</span>)}</div>
                <div className="position-card-metrics"><div><span>{Number(trade.entry_cash_flow) < 0 ? "Entry debit" : "Entry credit"}</span><strong>₹{formatPrice(String(Math.abs(Number(trade.entry_cash_flow))))}</strong></div><div><span>{trade.status === "OPEN" ? trade.expiry < indiaCalendarDate() ? "Settlement P&L pending" : "Marked P&L" : "Realized P&L"}</span><strong className={changeClass(trade.status === "OPEN" ? trade.unrealized_pnl : trade.realized_pnl)}>{formatSignedPrice(trade.status === "OPEN" ? trade.unrealized_pnl : trade.realized_pnl)}</strong></div><div className="position-metric--risk"><span>Max modeled loss</span><strong>₹{formatPrice(trade.max_loss)}</strong></div><PositionMaximumProfit trade={trade} token={token} /></div><small className="position-model-note">Expiry model · entry fills · before costs; not a profit forecast.</small><div className="position-probability"><span>Entry win / loss probability</span><strong>Not recorded</strong><small>{trade.status === "CLOSED" ? "No forecast was saved at entry; closed trades are not scored retrospectively." : "No validated forecast was saved at entry. Current estimates are withheld: insufficient evidence."}</small></div>
                {trade.valuation_warning ? <p className="table-warning">{trade.valuation_warning}</p> : null}
                {trade.execution_warnings.length ? <p className="table-warning">{trade.execution_warnings.join(" · ")}</p> : null}
                {trade.exit_reason ? <small>{trade.exit_reason.replaceAll("_", " ")}</small> : null}
                <details><summary>Legs, stops &amp; evidence</summary><p>{trade.legs.map(leg => `${leg.side} ${formatPrice(leg.strike)} ${leg.option_type} ×${formatCount(leg.quantity_units / trade.lot_size)} lots`).join(" / ")}</p><p>Stop {trade.stop_loss_amount ? `₹${formatPrice(trade.stop_loss_amount)}` : "Off"} · Target {trade.target_profit_amount ? `₹${formatPrice(trade.target_profit_amount)}` : "Off"} · Trail {trade.trailing_stop_amount ? `₹${formatPrice(trade.trailing_stop_amount)}` : "Off"}</p>
                  {trade.entry_rule_snapshot.entry_market_context ? <EntryMarketEvidence context={trade.entry_rule_snapshot.entry_market_context} /> : <p>Entry market context was not saved for this older trade.</p>}
                  <OptionTradeObservations tradeId={trade.id} apiUrl={API_URL} token={token} /></details>
                {trade.status === "OPEN" ? <button className="inline-button" disabled={paperBusy || (trade.expiry < indiaCalendarDate() && settlementBusy)} onClick={() => { if (trade.expiry < indiaCalendarDate()) { setSettlementTradeId(trade.id); setSettlementValue(""); setSettlementSource(""); setSettlementPreview(null); setIndexCloseReference(null); setOfficialIndexCloseReference(null); setSettlementError(""); } else { void closePaperTrade(trade.id); } }} type="button">{trade.expiry < indiaCalendarDate() ? "Review expiry settlement" : "Close paper position"}</button> : null}
              </article>)}</div>
              {!paperTrades.length ? <p className="workspace-empty">No positions yet. Add a strategy to begin monitoring.</p> : null}
              {paperTrades.length > 0 && !paperTrades.some(trade => positionView === "ALL" || trade.status === positionView) ? <p className="workspace-empty">No {positionView === "OPEN" ? "open positions" : "closed trades"}. Use the tabs to review your other results.</p> : null}
            </section>
            <details className="workspace-disclosure"><summary>All positions · detailed table</summary>
            {paperTrades.length ? (
              <div className="paper-table-scroll">
                <table className="paper-table">
                  <thead><tr><th>Strategy / expiry</th><th>Position</th><th>Lots</th><th>Max loss</th><th>Open P&amp;L</th><th>Status</th><th /></tr></thead>
                  <tbody>
                    {paperTrades.map((trade) => (
                      <tr key={trade.id}>
                        <td><strong>{trade.strategy_name}</strong><small>{trade.expiry} · ref {formatPrice(trade.reference_strike)}</small><small>{[
                          trade.stop_loss_amount ? `SL ₹${formatPrice(trade.stop_loss_amount)}` : null,
                          trade.target_profit_amount ? `Target ₹${formatPrice(trade.target_profit_amount)}` : null,
                          trade.trailing_stop_amount ? `Trail ₹${formatPrice(trade.trailing_stop_amount)}` : null,
                        ].filter(Boolean).join(" · ") || "No automatic exit rule"}</small>{trade.entry_rule_snapshot?.strategy_version_id ? <small>Version {strategyVersions.find((version) => version.id === trade.entry_rule_snapshot.strategy_version_id)?.name || trade.entry_rule_snapshot.strategy_version_id} · hash {String(trade.entry_rule_snapshot.strategy_definition_hash || "").slice(0, 8)}</small> : null}{trade.entry_rule_snapshot?.conditions?.length ? <small>Entry: {trade.entry_rule_snapshot.conditions.map((condition) => `${ENTRY_RULE_METRICS.find((metric) => metric.key === condition.metric)?.label || condition.metric} ${operatorLabel(condition.operator)} ${condition.threshold}`).join(" · ")}</small> : null}</td>
                        <td>{trade.legs.map((leg) => `${leg.side} ${formatPrice(leg.strike)} ${leg.option_type} ×${formatCount(leg.quantity_units / trade.lot_size)} lots`).join(" / ")}</td>
                        <td>{trade.lots}</td>
                        <td>₹{formatPrice(trade.max_loss)}</td>
                        <td className={changeClass(trade.status === "OPEN" ? trade.unrealized_pnl : trade.realized_pnl)}>
                          {trade.status === "OPEN" ? formatSignedPrice(trade.unrealized_pnl) : formatSignedPrice(trade.realized_pnl)}
                          {trade.status === "OPEN" && trade.trailing_stop_amount ? <small className="moving-stop-status">{Number(trade.peak_unrealized_pnl) > 0 ? `Moving stop trigger ${formatSignedPrice(Number(trade.peak_unrealized_pnl) - Number(trade.trailing_stop_amount))} · peak ${formatSignedPrice(trade.peak_unrealized_pnl)}` : "Moving stop armed · activates after positive P&L"}</small> : null}
                          {trade.valuation_warning ? <small className="table-warning">{trade.valuation_warning}</small> : null}
                        </td>
                        <td><span className={`trade-state trade-${trade.status === "OPEN" && trade.expiry < indiaCalendarDate() ? "expired" : trade.status.toLowerCase()}`}>{trade.status === "OPEN" && trade.expiry < indiaCalendarDate() ? "EXPIRED · SETTLEMENT PENDING" : trade.status}</span>{trade.exit_reason ? <small>{trade.exit_reason.replaceAll("_", " ")}</small> : null}</td>
                        <td>{trade.status === "OPEN" ? (trade.expiry < indiaCalendarDate() ? <button className="inline-button" disabled={settlementBusy} onClick={() => { setSettlementTradeId(trade.id); setSettlementValue(""); setSettlementSource(""); setSettlementPreview(null); setIndexCloseReference(null); setOfficialIndexCloseReference(null); setSettlementError(""); }} type="button">Review expiry settlement</button> : <button className="inline-button" disabled={paperBusy} onClick={() => closePaperTrade(trade.id)} type="button">Close at market</button>) : null}<OptionTradeObservations tradeId={trade.id} apiUrl={API_URL} token={token} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : <p className="features-note">No paper trades yet. Choose a strategy above to start your simulated portfolio.</p>}

            </details>

            {selectedOptionIndex === "NIFTY" && token && <ExpiryTestControl key={token} token={token} apiUrl={API_URL} expiry={selectedExpiry} strike={selectedPaperStrike} disabled={paperBusy || !paperEntry.ready || !!paperAccount?.kill_switch_enabled} onApplied={() => setPaperRefreshCount(value => value + 1)} />}
            {expiredPaperTrades.length > 0 ? <details className="equity-history expiry-settlement" open>
              <summary>Expired paper positions needing settlement ({expiredPaperTrades.length})</summary>
              <p className="paper-warning">Expired options need settlement evidence instead of a current quote. Review the selected index’s official close and corrections. Fetching a reference never closes the position.</p>
              {settlementTrade && !settlementSupported ? <p className="paper-warning">Settlement for {settlementTrade.underlying} is not supported yet. This position stays open until its official settlement route is available.</p> : null}
              <div className="settlement-reference-actions">
                <button className="inline-button" disabled={!settlementSupported || indexCloseBusy} onClick={() => void checkOfficialIndexCloseReference()} type="button">{indexCloseBusy ? "Checking…" : "Check NSE daily close"}</button>
                <button className="inline-button" disabled={settlementTrade?.underlying !== "NIFTY" || indexCloseBusy} onClick={() => void checkIndexCloseReference()} type="button">Check broker candle (NIFTY only)</button>
              </div>
              {officialIndexCloseReference && officialIndexCloseReference.trade_id === settlementTrade?.id && officialIndexCloseReference.status === "available" ? <div className="settlement-reference-result" role="status">
                <p>NSE daily {settlementTrade?.underlying} close for {officialIndexCloseReference.expiry}: <strong>{formatPrice(officialIndexCloseReference.preview?.final_index_value)}</strong>. <a href={officialIndexCloseReference.source_url ?? undefined} rel="noreferrer" target="_blank">Open archive ↗</a></p>
                <p className="features-note">{officialIndexCloseReference.warning}</p>
                <p className="features-note">If you record this NSE-based candidate, the server checks the archive fingerprint and close again. A changed archive stops the record so you can review it.</p>
                <details className="features-note"><summary>Archive fingerprint</summary><code>{officialIndexCloseReference.archive_sha256}</code></details>
                <button className="inline-button" disabled={settlementBusy || !officialIndexCloseReference.preview} onClick={() => { const preview = officialIndexCloseReference.preview; if (!preview || preview.trade_id !== settlementTrade?.id) return; setSettlementValue(preview.final_index_value); setSettlementSource(preview.source_reference); setSettlementPreview(preview); }} type="button">Preview using NSE close</button>
              </div> : null}
              {officialIndexCloseReference && officialIndexCloseReference.trade_id === settlementTrade?.id && officialIndexCloseReference.status === "unavailable" ? <p className="features-note" role="status">NSE archive unavailable: {officialIndexCloseReference.reason}</p> : null}
              {indexCloseReference && indexCloseReference.trade_id === settlementTrade?.id && indexCloseReference.status === "available" ? <p className="features-note" role="status">{indexCloseReference.provider} daily candle for {indexCloseReference.candle_date}: {formatPrice(indexCloseReference.daily_close)}. {indexCloseReference.warning}</p> : null}
              {settlementCloseDifference !== null && Number.isFinite(settlementCloseDifference) ? <p className={settlementCloseDifference > 0.01 ? "paper-warning" : "features-note"} role="status">{settlementCloseDifference > 0.01 ? `NSE archive and broker daily candle differ by ${formatPrice(String(settlementCloseDifference))}. Review exchange corrections before recording.` : "NSE archive and broker daily candle agree on the index close. Check for later corrections before recording."}</p> : null}
              {indexCloseReference && indexCloseReference.trade_id === settlementTrade?.id && indexCloseReference.status === "unavailable" ? <p className="features-note" role="status">Broker candle cross-check unavailable: {indexCloseReference.reason}</p> : null}
              <form onSubmit={(event) => void previewExpirySettlement(event)}>
                <div className="exit-rule-controls expiry-settlement-controls">
                  <label>Expired position
                    <select disabled={settlementBusy} onChange={(event) => { setSettlementTradeId(event.target.value); setSettlementValue(""); setSettlementSource(""); setSettlementPreview(null); setIndexCloseReference(null); setOfficialIndexCloseReference(null); setSettlementError(""); }} value={settlementTradeId}>
                      <option value="">Choose a position</option>
                      {expiredPaperTrades.map((trade) => <option key={trade.id} value={trade.id}>{trade.underlying} · {trade.strategy_name} · {trade.expiry} · {trade.id.slice(0, 8)}</option>)}
                    </select>
                  </label>
                  <label>Final {settlementTrade?.underlying ?? "selected index"} value
                    <input disabled={settlementBusy} min="0.01" onChange={(event) => { setSettlementValue(event.target.value); setSettlementPreview(null); }} placeholder="e.g. 25000.00" required step="0.01" type="number" value={settlementValue} />
                  </label>
                  <label>Official source reference
                    <input disabled={settlementBusy} maxLength={500} minLength={8} onChange={(event) => { setSettlementSource(event.target.value); setSettlementPreview(null); }} placeholder="NSE report URL or report identifier" required type="text" value={settlementSource} />
                  </label>
                </div>
                <button className="refresh-button" disabled={settlementBusy || !settlementSupported} type="submit">{settlementBusy ? "Checking…" : "Preview paper settlement"}</button>
              </form>
              {settlementError ? <p className="error-banner paper-error" role="alert">{settlementError}</p> : null}
              {settlementPreview ? <div className="strategy-preview expiry-settlement-preview">
                <div><small>{settlementPreview.settlement_date} · final index value {formatPrice(settlementPreview.final_index_value)}</small><strong>Paper P&amp;L {formatSignedPrice(settlementPreview.realized_pnl)}</strong><small>Expiry cash flow {formatSignedPrice(settlementPreview.settlement_cash_flow)} · source: {settlementPreview.source_reference}</small></div>
                <div><ul>{settlementPreview.legs.map((leg) => <li key={leg.instrument_key}>{leg.side} {leg.option_type} {formatPrice(leg.strike)} ×{leg.quantity_units}: intrinsic {formatPrice(leg.intrinsic_value)} · cash {formatSignedPrice(leg.cash_flow)}</li>)}</ul></div>
                {officialIndexCloseReference?.status === "available"
                  && officialIndexCloseReference.trade_id === settlementTrade?.id
                  && officialIndexCloseReference.preview?.final_index_value === settlementPreview.final_index_value
                  && officialIndexCloseReference.source_url === settlementPreview.source_reference
                  ? <p className="features-note">NSE archive preview: the server rechecks this archive fingerprint before recording paper cash. Review any later exchange correction yourself.</p>
                  : <p className="paper-warning">Manual source: the app cannot independently verify this final value or source. Check the published settlement and corrections before recording.</p>}
                <p className="paper-warning">{settlementPreview.warning}</p>
                <button className="primary-button" disabled={settlementBusy} onClick={() => void recordExpirySettlement()} type="button">Record reviewed paper settlement</button>
              </div> : null}
            </details> : null}

            <details className="paper-orders">
              <summary>Order and fill log <span>{paperOrders.length} records</span></summary>
              {paperOrders.length ? (
                <div className="paper-table-scroll">
                  <table className="paper-table">
                    <thead><tr><th>Time</th><th>Action</th><th>Fill quality</th><th>Net cash flow</th><th>Simulation method</th></tr></thead>
                    <tbody>{paperOrders.map((order) => (
                      <tr key={order.id}>
                        <td>{formatTime(order.created_at)}</td><td>{order.action}</td><td>{order.fill_snapshot.quality_status}</td>
                        <td className={changeClass(order.net_cash_flow)}>{formatSignedPrice(order.net_cash_flow)}</td><td>{order.fill_snapshot.method}</td>
                      </tr>
                    ))}</tbody>
                  </table>
                </div>
              ) : <p className="features-note">Simulated fills will appear here.</p>}
            </details>
          </section>

          </> : null}
          {activeSection === "backtest" ? <>
          <section aria-label="Historical options backtest" className="historical-backtest">
            <div className="features-heading backtest-heading">
              <div>
                <div className="panel-kicker">HISTORICAL TESTING</div>
                <h3>Replay one NIFTY options position</h3>
              </div>
              <span>Expired-contract OHLC data</span>
            </div>
            <p className="muted backtest-intro">
              Select a strategy and expired contract period. The replay anchors the strike to the prior
              NIFTY close, enters at the option candle open, and exits at its close on your chosen date.
              This is a historical position replay, not repeated signal testing. Fee and slippage inputs below are illustrative estimates.
            </p>
            <div className="preset-controls">
              <label htmlFor="historical-version">Saved version</label>
              <select id="historical-version" disabled={historicalBacktestBusy || scheduledBusy} onChange={(event) => {
                const version = strategyVersions.find((item) => item.id === event.target.value);
                setHistoricalVersionId(event.target.value);
                if (version) { setHistoricalStrategyKey(version.definition.template_key); setHistoricalLots(version.definition.lots); }
                setHistoricalBacktest(null); setScheduledBacktest(null);
              }} value={historicalVersionId}>
                <option value="">Use loose template inputs</option>
                {strategyVersions.map((version) => <option key={version.id} value={version.id}>{version.name} · v{version.version_number}</option>)}
              </select>
            </div>
            {historicalVersionUnsupported ? <p className="paper-warning">This saved version has entry filters or automatic exits that daily-candle replay cannot evaluate. Choose a simple version or unpin it.</p> : null}
            {historicalExpiriesBusy ? <p className="features-note" aria-live="polite">Loading expired NIFTY option history…</p> : null}
            {historicalDataError ? <p className="error-banner backtest-error" role="alert">{historicalDataError}</p> : null}
            <form className="backtest-form" onSubmit={(event) => void runHistoricalBacktest(event)}>
              <div className="backtest-controls">
                <label>
                  Strategy
                  <select
                    disabled={historicalBacktestBusy || paperStrategies.length === 0}
                    onChange={(event) => {
                      setHistoricalStrategyKey(event.target.value);
                      setHistoricalVersionId("");
                      setHistoricalBacktest(null);
                    }}
                    value={historicalStrategyKey}
                  >
                    {paperStrategies.map((strategy) => <option key={strategy.key} value={strategy.key}>{strategy.name}</option>)}
                  </select>
                </label>
                <label>
                  Expired expiry
                  <select
                    disabled={historicalExpiriesBusy || !historicalExpiries.length || historicalBacktestBusy}
                    onChange={(event) => {
                      setHistoricalExpiry(event.target.value);
                      setHistoricalExitDate(event.target.value);
                      setHistoricalBacktest(null);
                      setHistoricalBacktestError("");
                    }}
                    value={historicalExpiry}
                  >
                    {historicalExpiries.map((expiry) => <option key={expiry} value={expiry}>{expiry}</option>)}
                  </select>
                </label>
                <label>
                  Entry date
                  <input
                    disabled={!historicalExpiry || historicalBacktestBusy}
                    max={historicalExpiry}
                    onChange={(event) => {
                      setHistoricalEntryDate(event.target.value);
                      setHistoricalBacktest(null);
                    }}
                    required
                    type="date"
                    value={historicalEntryDate}
                  />
                </label>
                <label>
                  Exit date
                  <input
                    disabled={!historicalExpiry || historicalBacktestBusy}
                    max={historicalExpiry}
                    min={historicalEntryDate || undefined}
                    onChange={(event) => {
                      setHistoricalExitDate(event.target.value);
                      setHistoricalBacktest(null);
                    }}
                    required
                    type="date"
                    value={historicalExitDate}
                  />
                </label>
                <label>
                  Lots
                  <input
                    disabled={historicalBacktestBusy}
                    max={100}
                    min={1}
                    onChange={(event) => { setHistoricalLots(Math.max(1, Math.min(100, Math.trunc(Number(event.target.value) || 1)))); setHistoricalVersionId(""); }}
                    type="number"
                    value={historicalLots}
                  />
                </label>
              </div>
              <div className="backtest-controls">
                <label>
                  Estimated fee per leg order (₹)
                  <input disabled={historicalBacktestBusy} max={1000} min={0} onChange={(event) => setScheduleFee(event.target.value)} step="0.01" type="number" value={scheduleFee} />
                </label>
                <label>
                  Estimated slippage per unit per side (₹)
                  <input disabled={historicalBacktestBusy} max={100} min={0} onChange={(event) => setScheduleSlippage(event.target.value)} step="0.01" type="number" value={scheduleSlippage} />
                </label>
              </div>
              <button
                className="primary-button backtest-submit"
                disabled={historicalBacktestBusy || historicalVersionUnsupported || !historicalExpiry || !historicalEntryDate || !historicalExitDate}
                type="submit"
              >
                {historicalBacktestBusy ? "Loading historical option candles…" : "Run historical replay"}
              </button>
            </form>
            {historicalBacktestError ? <p className="error-banner backtest-error" role="alert">{historicalBacktestError}</p> : null}
            {historicalBacktest ? (
              <div className="backtest-result" aria-live="polite">
                <div className="backtest-summary">
                  <div><span>GROSS P&amp;L</span><strong className={changeClass(historicalBacktest.gross_pnl)}>{formatSignedPrice(historicalBacktest.gross_pnl)}</strong></div>
                  <div><span>ESTIMATED COSTS</span><strong>{historicalBacktest.estimated_cost === null ? "Not modeled" : `₹${formatPrice(historicalBacktest.estimated_cost)}`}</strong></div>
                  <div><span>NET P&amp;L ESTIMATE</span><strong className={historicalBacktest.net_pnl === null ? "" : changeClass(historicalBacktest.net_pnl)}>{historicalBacktest.net_pnl === null ? "Not modeled" : formatSignedPrice(historicalBacktest.net_pnl)}</strong></div>
                  <div><span>NET RETURN ON RISK</span><strong>{historicalBacktest.return_on_risk_net_pct === null ? "Not modeled" : `${formatPrice(historicalBacktest.return_on_risk_net_pct)}%`}</strong></div>
                  <div><span>MAX LOSS AT ENTRY</span><strong>₹{formatPrice(historicalBacktest.maximum_loss_at_entry)}</strong></div>
                  <div><span>RETURN ON MAX RISK</span><strong>{formatPrice(historicalBacktest.return_on_risk_pct)}%</strong></div>
                  <div><span>REFERENCE STRIKE</span><strong>{formatPrice(historicalBacktest.reference_strike)}</strong></div>
                </div>
                <div className="backtest-result-title">
                  <strong>{historicalBacktest.strategy_name} · {historicalBacktest.expiry}</strong>
                  {historicalBacktest.strategy_version_id ? <span>Saved version {strategyVersions.find((version) => version.id === historicalBacktest.strategy_version_id)?.name || historicalBacktest.strategy_version_id} · hash {historicalBacktest.strategy_definition_hash?.slice(0, 12)}…</span> : null}
                  <span>{historicalBacktest.lots} lot(s) × {formatCount(historicalBacktest.lot_size)} units · prior NIFTY close {formatPrice(historicalBacktest.entry_reference_spot)}</span>
                  {historicalBacktest.observed_input_digest ? <span>Selected input fingerprint {historicalBacktest.observed_input_digest.slice(0, 12)}…</span> : null}
                </div>
                <div className="paper-table-scroll">
                  <table className="paper-table backtest-table">
                    <thead><tr><th>Leg</th><th>Entry open</th><th>Exit close</th><th>Quantity</th><th>Gross P&amp;L</th></tr></thead>
                    <tbody>{historicalBacktest.legs.map((leg) => (
                      <tr key={leg.instrument_key}>
                        <td><strong>{leg.side} {formatPrice(leg.strike)} {leg.option_type}</strong><small>{leg.trading_symbol}</small></td>
                        <td>{formatPrice(leg.entry_price)}<small>{formatTime(leg.entry_timestamp)}</small></td>
                        <td>{formatPrice(leg.exit_price)}<small>{formatTime(leg.exit_timestamp)}</small></td>
                        <td>{formatCount(leg.quantity_units)}</td>
                        <td className={changeClass(leg.gross_pnl)}>{formatSignedPrice(leg.gross_pnl)}</td>
                      </tr>
                    ))}</tbody>
                  </table>
                </div>
                <ul className="backtest-assumptions">
                  {historicalBacktest.assumptions.map((assumption) => <li key={assumption}>{assumption}</li>)}
                  {historicalBacktest.warnings.map((warning) => <li className="backtest-warning" key={warning}>{warning}</li>)}
                </ul>
              </div>
            ) : !historicalExpiriesBusy && !historicalDataError && historicalExpiries.length === 0 ? (
              <p className="features-note">No expired NIFTY option expiries were returned by the provider.</p>
            ) : null}
            {historicalBacktestHistoryError ? (
              <p className="features-note error-banner" role="alert">{historicalBacktestHistoryError}</p>
            ) : null}
            {historicalBacktestRuns.length ? (
              <div className="backtest-history">
                <h4>Saved replay history <span>Most recent 20</span></h4>
                <div className="paper-table-scroll">
                  <table className="paper-table backtest-history-table">
                    <thead><tr><th>Saved</th><th>Strategy</th><th>Expiry</th><th>Entry → Exit</th><th>Gross P&amp;L</th><th /></tr></thead>
                    <tbody>{historicalBacktestRuns.map((run) => (
                      <tr key={run.result.run_id}>
                        <td>{new Date(run.created_at).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" })}</td>
                        <td>{run.result.strategy_name}</td>
                        <td>{run.result.expiry}</td>
                        <td>{run.result.entry_date} → {run.result.exit_date}</td>
                        <td className={changeClass(run.result.gross_pnl)}>{formatSignedPrice(run.result.gross_pnl)}</td>
                        <td><button
                          aria-label={`View ${run.result.strategy_name} replay for ${run.result.expiry}`}
                          className="table-action"
                          onClick={() => setHistoricalBacktest(run.result)}
                          type="button"
                        >View</button> <button aria-label={`Export ${run.result.strategy_name} replay for ${run.result.expiry}`} className="table-action" onClick={() => exportReplayRun(run)} type="button">Export JSON</button></td>
                      </tr>
                    ))}</tbody>
                  </table>
                </div>
              </div>
            ) : null}
          </section>

          <section aria-label="Scheduled historical replay" className="historical-backtest">
            <div className="features-heading backtest-heading">
              <div>
                <div className="panel-kicker">REPEATED HISTORICAL TEST</div>
                <h3>Replay a template across expiries</h3>
              </div>
              <span>Up to four past expiries</span>
            </div>
            <p className="muted backtest-intro">
              Uses the strategy and lots selected above. Enter a fixed number of NIFTY trading sessions
              before each expiry, then exit on expiry. Estimated costs are illustrative; daily candles
              cannot prove executable fills or capture intraday risk.
            </p>
            <form className="backtest-form" onSubmit={(event) => void runScheduledBacktest(event)}>
              <div className="backtest-controls">
                <label>
                  From expiry
                  <select disabled={historicalExpiriesBusy || scheduledBusy || !historicalExpiries.length} onChange={(event) => setScheduleStartExpiry(event.target.value)} value={scheduleStartExpiry}>
                    {historicalExpiries.map((expiry) => <option key={expiry} value={expiry}>{expiry}</option>)}
                  </select>
                </label>
                <label>
                  Through expiry
                  <select disabled={historicalExpiriesBusy || scheduledBusy || !historicalExpiries.length} onChange={(event) => setScheduleEndExpiry(event.target.value)} value={scheduleEndExpiry}>
                    {historicalExpiries.map((expiry) => <option key={expiry} value={expiry}>{expiry}</option>)}
                  </select>
                </label>
                <label>
                  Entry sessions before expiry
                  <input disabled={scheduledBusy} max={5} min={1} onChange={(event) => setScheduleEntrySessions(Math.max(1, Math.min(5, Math.trunc(Number(event.target.value) || 1))))} type="number" value={scheduleEntrySessions} />
                </label>
                <label>
                  Estimated fee per leg order (₹)
                  <input disabled={scheduledBusy} max={1000} min={0} onChange={(event) => setScheduleFee(event.target.value)} step="0.01" type="number" value={scheduleFee} />
                </label>
                <label>
                  Estimated slippage per unit per side (₹)
                  <input disabled={scheduledBusy} max={100} min={0} onChange={(event) => setScheduleSlippage(event.target.value)} step="0.01" type="number" value={scheduleSlippage} />
                </label>
              </div>
              <button className="primary-button backtest-submit" disabled={scheduledBusy || historicalVersionUnsupported || !scheduleStartExpiry || !scheduleEndExpiry || scheduleStartExpiry > scheduleEndExpiry || !historicalStrategyKey} type="submit">
                {scheduledBusy ? "Loading multiple expiries…" : "Run scheduled replay"}
              </button>
            </form>
            {scheduledError ? <p className="error-banner backtest-error" role="alert">{scheduledError}</p> : null}
            {scheduledBacktest ? (
              <div className="backtest-result" aria-live="polite">
                <div className="backtest-summary">
                  <div><span>NET P&amp;L ESTIMATE</span><strong className={changeClass(scheduledBacktest.net_pnl)}>{formatSignedPrice(scheduledBacktest.net_pnl)}</strong></div>
                  <div><span>GROSS P&amp;L</span><strong className={changeClass(scheduledBacktest.gross_pnl)}>{formatSignedPrice(scheduledBacktest.gross_pnl)}</strong></div>
                  <div><span>ESTIMATED COSTS</span><strong>₹{formatPrice(scheduledBacktest.estimated_cost)}</strong></div>
                  <div><span>CLOSED-TRADE DRAWDOWN</span><strong>₹{formatPrice(scheduledBacktest.max_closed_trade_drawdown)}</strong></div>
                </div>
                <div className="backtest-result-title">
                  <strong>{scheduledBacktest.strategy_name} · {scheduledBacktest.trade_count} expiries</strong>
                  {scheduledBacktest.strategy_version_id ? <span>Saved version {strategyVersions.find((version) => version.id === scheduledBacktest.strategy_version_id)?.name || scheduledBacktest.strategy_version_id} · hash {scheduledBacktest.strategy_definition_hash?.slice(0, 12)}…</span> : null}
                  <span>{scheduledBacktest.winning_trades} positive net trades · version {scheduledBacktest.strategy_version} · input fingerprint {scheduledBacktest.observed_input_digest.slice(0, 12)}…</span>
                </div>
                <div className="paper-table-scroll">
                  <table className="paper-table backtest-table">
                    <thead><tr><th>Expiry</th><th>Entry → Exit</th><th>Gross</th><th>Estimated costs</th><th>Net</th><th>Cumulative net</th></tr></thead>
                    <tbody>{scheduledBacktest.trades.map((trade) => (
                      <tr key={trade.expiry}>
                        <td>{trade.expiry}</td>
                        <td>{trade.entry_date} → {trade.exit_date}</td>
                        <td className={changeClass(trade.gross_pnl)}>{formatSignedPrice(trade.gross_pnl)}</td>
                        <td>₹{formatPrice(trade.estimated_cost)}</td>
                        <td className={changeClass(trade.net_pnl)}>{formatSignedPrice(trade.net_pnl)}</td>
                        <td className={changeClass(trade.cumulative_net_pnl)}>{formatSignedPrice(trade.cumulative_net_pnl)}</td>
                      </tr>
                    ))}</tbody>
                  </table>
                </div>
                <ul className="backtest-assumptions">
                  {scheduledBacktest.assumptions.map((assumption) => <li key={assumption}>{assumption}</li>)}
                  {scheduledBacktest.warnings.map((warning) => <li className="backtest-warning" key={warning}>{warning}</li>)}
                </ul>
              </div>
            ) : null}
            {scheduledHistoryError ? <p className="features-note error-banner" role="alert">{scheduledHistoryError}</p> : null}
            {scheduledRuns.length ? (
              <div className="backtest-history">
                <h4>Saved scheduled replays <span>Most recent 20</span></h4>
                <div className="paper-table-scroll">
                  <table className="paper-table backtest-history-table">
                    <thead><tr><th>Saved</th><th>Strategy</th><th>Range</th><th>Trades</th><th>Estimated net</th><th /></tr></thead>
                    <tbody>{scheduledRuns.map((run) => (
                      <tr key={run.result.run_id}>
                        <td>{new Date(run.created_at).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" })}</td>
                        <td>{run.result.strategy_name}</td>
                        <td>{run.result.start_date} → {run.result.end_date}</td>
                        <td>{run.result.trade_count}</td>
                        <td className={changeClass(run.result.net_pnl)}>{formatSignedPrice(run.result.net_pnl)}</td>
                        <td><button aria-label={"View scheduled replay for " + run.result.strategy_name} className="table-action" onClick={() => setScheduledBacktest(run.result)} type="button">View</button> <button aria-label={"Export scheduled replay for " + run.result.strategy_name} className="table-action" onClick={() => exportReplayRun(run)} type="button">Export JSON</button></td>
                      </tr>
                    ))}</tbody>
                  </table>
                </div>
              </div>
            ) : null}
          </section>

          </> : null}
          {activeSection === "charts" ? <>
          {chain ? (
            <section className="features-panel" aria-label="Option chain analytics">
              <div className="features-heading">
                <div>
                  <div className="panel-kicker">OPTIONS FEATURES</div>
                  <h3>Positioning &amp; liquidity</h3>
                </div>
                <span>Calculated from this snapshot</span>
              </div>
              {optionFeatures ? (
                <>
                  <div className="features-grid">
                    <div><span>Put / call OI</span><strong>{formatPrice(optionFeatures.put_call_ratio_oi)}</strong></div>
                    <div><span>Put / call volume</span><strong>{formatPrice(optionFeatures.put_call_ratio_volume)}</strong></div>
                    <div><span>ATM strike</span><strong>{formatPrice(optionFeatures.atm_strike)}</strong></div>
                    <div><span>ATM IV · call / put</span><strong>{formatPrice(optionFeatures.atm_call_iv_pct)}% / {formatPrice(optionFeatures.atm_put_iv_pct)}%</strong></div>
                    <div><span>ATM skew · put − call</span><strong>{formatPrice(optionFeatures.atm_iv_skew_put_minus_call_points)} pts</strong></div>
                    <div><span>Largest call OI</span><strong>{formatPrice(optionFeatures.largest_call_oi_strike)} <small>{formatPrice(optionFeatures.largest_call_oi_share_pct)}%</small></strong></div>
                    <div><span>Largest put OI</span><strong>{formatPrice(optionFeatures.largest_put_oi_strike)} <small>{formatPrice(optionFeatures.largest_put_oi_share_pct)}%</small></strong></div>
                    <div><span>Median relative spread</span><strong>{formatPrice(optionFeatures.median_relative_spread_pct)}%</strong></div>
                    <div><span>Two-sided quotes</span><strong>{formatCount(optionFeatures.two_sided_quote_count)} / {formatCount(chain.contracts.length)}</strong></div>
                  </div>
                  <p className="features-note">
                    IV rank, IV percentile, and term structure need stored option-chain history across time or expiries.
                    They are left unavailable until that data exists.
                  </p>
                </>
              ) : featuresError ? (
                <p className="features-note error-banner" role="alert">{featuresError}</p>
              ) : (
                <p className="features-note" aria-live="polite">Calculating features…</p>
              )}
            </section>
          ) : null}

          {chain?.quality ? (
            <section className={`quality-panel quality-${chain.quality.status.toLowerCase()}`} aria-label="Market data quality">
              <div className="quality-topline">
                <div>
                  <div className="panel-kicker">MARKET DATA QUALITY</div>
                  <strong>{chain.quality.status === "PASS" ? "Checks passed" : chain.quality.status === "WARN" ? "Review data notes" : "Data quality failed"}</strong>
                </div>
                <span className="quality-badge">{chain.quality.status}</span>
              </div>
              <div className="quality-metrics">
                <span><strong>{chain.quality.complete_strike_count}/{chain.quality.strike_count}</strong> paired strikes</span>
                <span><strong>{chain.quality.missing_field_count}</strong> missing quote fields</span>
                <span><strong>{chain.quality.crossed_market_count}</strong> crossed books</span>
                <span><strong>{chain.quality.abnormal_spread_count}</strong> wide spreads</span>
              </div>
              {chain.quality.issues.length ? (
                <ul className="quality-issues">
                  {chain.quality.issues.map((issue) => (
                    <li key={issue.code} className={`quality-issue-${issue.severity}`}>
                      <span>{issue.message}</span><small>{issue.count}</small>
                    </li>
                  ))}
                </ul>
              ) : <p className="quality-clean">No configured data-quality issues were detected.</p>}
            </section>
          ) : null}

          <section className="chain-panel">
            <div className="chain-heading">
              <div>
                <div className="panel-kicker">OPTION MARKET</div>
                <h3>Calls &amp; puts <span className="chain-expiry">{chain?.expiry || selectedExpiry}</span></h3>
              </div>
              <div className="chain-legend"><span className="call-key" /> Call <span className="put-key" /> Put</div>
            </div>
            {contractsBusy || chainBusy ? (
              <div className="table-state" aria-live="polite"><span className="loader" />Fetching the latest snapshot…</div>
            ) : strikeRows.length === 0 ? (
              <div className="table-state">
                {marketError ? "The option chain could not be loaded. Check the error above." : "Choose an expiry to view its option chain."}
              </div>
            ) : (
              <div className="table-scroll">
                <table className="chain-table">
                  <thead>
                    <tr className="group-row">
                      <th className="call-heading" colSpan={10}>CALL OPTIONS <span>CE</span></th>
                      <th className="strike-head" rowSpan={2}>STRIKE</th>
                      <th className="put-heading" colSpan={10}>PUT OPTIONS <span>PE</span></th>
                    </tr>
                    <tr className="column-row">
                      <th>OPEN INT.</th><th>CHG IN OI</th><th>VOLUME</th><th>IV</th><th>LTP</th>
                      <th>CHG</th><th>BID QTY</th><th>BID</th><th>ASK</th><th>ASK QTY</th>
                      <th>BID QTY</th><th>BID</th><th>ASK</th><th>ASK QTY</th><th>CHG</th>
                      <th>LTP</th><th>IV</th><th>VOLUME</th><th>CHG IN OI</th><th>OPEN INT.</th>
                    </tr>
                  </thead>
                  <tbody>
                    {strikeRows.map((row) => (
                      <tr key={row.strike}>
                        <td>{formatCount(row.call?.open_interest)}</td>
                        <td className={changeClass(row.call?.open_interest_change?.toString())}>
                          {formatSignedCount(row.call?.open_interest_change)}
                        </td>
                        <td>{formatCount(row.call?.quote.volume)}</td>
                        <td>{row.call?.implied_volatility ? `${formatPrice(row.call.implied_volatility)}%` : "—"}</td>
                        <td className="price-cell call-price">
                          <strong>{row.call ? `₹${formatPrice(row.call.quote.last_price)}` : "—"}</strong>
                          <small>{greekSummary(row.call)}</small>
                        </td>
                        <td className={changeClass(row.call?.change)}>
                          {formatSignedPrice(row.call?.change)}
                        </td>
                        <td>{formatCount(row.call?.bid_quantity)}</td>
                        <td>{row.call?.quote.bid ? formatPrice(row.call.quote.bid) : "—"}</td>
                        <td>{row.call?.quote.ask ? formatPrice(row.call.quote.ask) : "—"}</td>
                        <td>{formatCount(row.call?.ask_quantity)}</td>
                        <th className="strike-cell" scope="row">{formatPrice(row.strike)}</th>
                        <td>{formatCount(row.put?.bid_quantity)}</td>
                        <td>{row.put?.quote.bid ? formatPrice(row.put.quote.bid) : "—"}</td>
                        <td>{row.put?.quote.ask ? formatPrice(row.put.quote.ask) : "—"}</td>
                        <td>{formatCount(row.put?.ask_quantity)}</td>
                        <td className={changeClass(row.put?.change)}>
                          {formatSignedPrice(row.put?.change)}
                        </td>
                        <td className="price-cell put-price">
                          <strong>{row.put ? `₹${formatPrice(row.put.quote.last_price)}` : "—"}</strong>
                          <small>{greekSummary(row.put)}</small>
                        </td>
                        <td>{row.put?.implied_volatility ? `${formatPrice(row.put.implied_volatility)}%` : "—"}</td>
                        <td>{formatCount(row.put?.quote.volume)}</td>
                        <td className={changeClass(row.put?.open_interest_change?.toString())}>
                          {formatSignedCount(row.put?.open_interest_change)}
                        </td>
                        <td>{formatCount(row.put?.open_interest)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <div className="chain-footnote">
              <span>Read-only data · No orders can be placed from this screen.</span>
              {chain ? <span>Snapshot time reflects API receipt, not a per-contract exchange timestamp.</span> : null}
            </div>
          </section>
          </> : null}
          <footer className="page-footer">
            <span>Market data for research · Verify information with your broker before acting.</span>
            <span>Live trading disabled</span>
          </footer>
          </>}
        </>
      )}
    </main>
  );
}
