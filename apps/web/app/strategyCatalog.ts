export type StrategyMarketBias = "BULLISH" | "BEARISH" | "SIDEWAYS" | "VOLATILITY" | "INCOME" | "SPECIAL";
export type StrategyCatalogStatus = "PAPER_SUPPORTED" | "RESEARCH_ONLY" | "ENGINE_SUPPORT_PENDING";

export type StrategyCatalogEntry = {
  key: string;
  name: string;
  bias: StrategyMarketBias;
  label: string;
  proficiency: "Novice" | "Intermediate" | "Advanced" | "Expert";
  status: StrategyCatalogStatus;
  summary: string;
};

const paper = (key: string, name: string, bias: StrategyMarketBias, label: string, proficiency: StrategyCatalogEntry["proficiency"], summary: string): StrategyCatalogEntry => ({ key, name, bias, label, proficiency, status: "PAPER_SUPPORTED", summary });
const pending = (key: string, name: string, bias: StrategyMarketBias, label: string, proficiency: StrategyCatalogEntry["proficiency"], summary: string): StrategyCatalogEntry => ({ key, name, bias, label, proficiency, status: "ENGINE_SUPPORT_PENDING", summary });
const research = (key: string, name: string, bias: StrategyMarketBias, label: string, proficiency: StrategyCatalogEntry["proficiency"], summary: string): StrategyCatalogEntry => ({ key, name, bias, label, proficiency, status: "RESEARCH_ONLY", summary });

// Public names and categories are a reference taxonomy. Descriptions are original TradingPilot summaries.
export const STRATEGY_CATALOG: StrategyCatalogEntry[] = [
  paper("long_call", "Long call", "BULLISH", "Bullish", "Novice", "Directional upside with capped premium risk."),
  paper("long_put", "Long put", "BEARISH", "Bearish", "Novice", "Directional downside with capped premium risk."),
  pending("covered_call", "Covered call", "INCOME", "Income", "Novice", "Premium income against an underlying holding."),
  pending("cash_secured_put", "Cash-secured put", "INCOME", "Income", "Novice", "Premium income with a planned purchase obligation."),
  pending("protective_put", "Protective put", "BULLISH", "Bullish", "Novice", "Downside insurance for an underlying holding."),
  paper("bull_put_spread", "Bull put spread", "BULLISH", "Bullish", "Intermediate", "Defined-risk income with a bullish bias."),
  paper("bear_call_spread", "Bear call spread", "BEARISH", "Bearish", "Intermediate", "Defined-risk income with a bearish bias."),
  paper("iron_butterfly", "Iron butterfly", "SIDEWAYS", "Sideways", "Intermediate", "Tighter range income with protected wings."),
  paper("iron_condor", "Iron condor", "SIDEWAYS", "Sideways", "Intermediate", "Wider range income with protected wings."),
  paper("inverse_iron_condor", "Inverse iron condor", "VOLATILITY", "Volatility", "Advanced", "Move-seeking inner wings with defined expiry risk."),
  paper("long_put_butterfly", "Long put butterfly", "SIDEWAYS", "Sideways", "Intermediate", "Defined-risk range structure using puts."),
  paper("long_call_butterfly", "Long call butterfly", "SIDEWAYS", "Sideways", "Intermediate", "Defined-risk range structure using calls."),
  pending("calendar_call_spread", "Calendar call spread", "SIDEWAYS", "Sideways", "Intermediate", "Same-strike calls across two expiries."),
  pending("calendar_put_spread", "Calendar put spread", "SIDEWAYS", "Sideways", "Intermediate", "Same-strike puts across two expiries."),
  pending("diagonal_call_spread", "Diagonal call spread", "BULLISH", "Bullish", "Intermediate", "Call calendar with a strike bias."),
  pending("diagonal_put_spread", "Diagonal put spread", "BEARISH", "Bearish", "Intermediate", "Put calendar with a strike bias."),
  paper("bull_call_spread", "Bull call spread", "BULLISH", "Bullish", "Intermediate", "Defined-risk directional upside."),
  paper("bear_put_spread", "Bear put spread", "BEARISH", "Bearish", "Intermediate", "Defined-risk directional downside."),
  pending("inverse_iron_butterfly", "Inverse iron butterfly", "VOLATILITY", "Volatility", "Intermediate", "Requires a validated tail-risk and margin model."),
  paper("short_put_butterfly", "Short put butterfly", "VOLATILITY", "Volatility", "Intermediate", "Move-seeking put butterfly with bounded risk."),
  paper("short_call_butterfly", "Short call butterfly", "VOLATILITY", "Volatility", "Intermediate", "Move-seeking call butterfly with bounded risk."),
  paper("long_straddle", "Straddle", "VOLATILITY", "Volatility", "Intermediate", "Large move either way with capped premium risk."),
  paper("long_strangle", "Strangle", "VOLATILITY", "Volatility", "Intermediate", "Cheaper large-move setup using different strikes."),
  pending("collar", "Collar", "BULLISH", "Bullish", "Intermediate", "Underlying holding with an upside cap and downside floor."),
  research("short_put", "Short put", "INCOME", "Income", "Advanced", "Premium income with substantial downside obligation."),
  research("short_call", "Short call", "BEARISH", "Bearish", "Advanced", "Premium income with potentially unbounded upside risk."),
  research("short_straddle", "Short straddle", "SIDEWAYS", "Sideways", "Advanced", "Range income with unbounded move risk."),
  research("short_strangle", "Short strangle", "SIDEWAYS", "Sideways", "Advanced", "Wider range income with unbounded move risk."),
  paper("long_call_condor", "Long call condor", "SIDEWAYS", "Sideways", "Advanced", "Wider defined-risk range using four calls."),
  paper("long_put_condor", "Long put condor", "SIDEWAYS", "Sideways", "Advanced", "Wider defined-risk range using four puts."),
  paper("short_call_condor", "Short call condor", "VOLATILITY", "Volatility", "Advanced", "Move-seeking call condor with bounded risk."),
  paper("short_put_condor", "Short put condor", "VOLATILITY", "Volatility", "Advanced", "Move-seeking put condor with bounded risk."),
  research("bull_call_ladder", "Bull call ladder", "BULLISH", "Bullish", "Advanced", "Bullish ladder with an additional short call risk."),
  research("bear_call_ladder", "Bear call ladder", "BEARISH", "Bearish", "Advanced", "Bearish ladder with a volatility-dependent tail."),
  research("bull_put_ladder", "Bull put ladder", "BULLISH", "Bullish", "Advanced", "Put ladder with asymmetric downside behavior."),
  research("bear_put_ladder", "Bear put ladder", "BEARISH", "Bearish", "Advanced", "Put ladder with asymmetric downside tail risk."),
  paper("call_ratio_backspread", "Call ratio backspread", "VOLATILITY", "Volatility", "Advanced", "Upside move structure requiring a 1:2 call ratio."),
  paper("put_broken_wing", "Put broken wing", "BULLISH", "Bullish", "Advanced", "Asymmetric put butterfly with a bounded loss."),
  research("inverse_call_broken_wing", "Inverse call broken wing", "BULLISH", "Bullish", "Advanced", "Asymmetric call structure with a bullish bias."),
  paper("put_ratio_backspread", "Put ratio backspread", "VOLATILITY", "Volatility", "Advanced", "Downside move structure requiring a 2:1 put ratio."),
  paper("call_broken_wing", "Call broken wing", "BEARISH", "Bearish", "Advanced", "Asymmetric call butterfly with a bounded loss."),
  research("inverse_put_broken_wing", "Inverse put broken wing", "BEARISH", "Bearish", "Advanced", "Asymmetric put structure with a bearish bias."),
  research("jade_lizard", "Jade lizard", "INCOME", "Income", "Advanced", "Combined premium structure with assignment and tail risk."),
  research("reverse_jade_lizard", "Reverse jade lizard", "BEARISH", "Bearish", "Advanced", "Reverse premium structure with tail risk."),
  pending("covered_short_straddle", "Covered short straddle", "INCOME", "Income", "Advanced", "Covered income structure requiring underlying support."),
  pending("covered_short_strangle", "Covered short strangle", "INCOME", "Income", "Advanced", "Covered income structure requiring underlying support."),
  research("call_ratio_spread", "Call ratio spread", "BULLISH", "Bullish", "Expert", "Call ratio with potentially unbounded upside risk."),
  research("put_ratio_spread", "Put ratio spread", "BEARISH", "Bearish", "Expert", "Put ratio with potentially unbounded downside risk."),
  pending("long_synthetic_future", "Long synthetic future", "BULLISH", "Bullish", "Expert", "Call and put combination that requires assignment modeling."),
  pending("short_synthetic_future", "Short synthetic future", "BEARISH", "Bearish", "Expert", "Put and call combination that requires assignment modeling."),
  pending("synthetic_put", "Synthetic put", "BEARISH", "Bearish", "Expert", "Synthetic downside protection requiring an underlying short."),
  pending("long_combo", "Long combo", "BULLISH", "Bullish", "Expert", "Separated-strike synthetic structure."),
  pending("short_combo", "Short combo", "BEARISH", "Bearish", "Expert", "Separated-strike short synthetic structure."),
  paper("strip", "Strip", "BEARISH", "Bearish", "Expert", "Bearish-biased volatility structure with a 1:2 option ratio."),
  paper("strap", "Strap", "BULLISH", "Bullish", "Expert", "Bullish-biased volatility structure with a 2:1 option ratio."),
  paper("guts", "Guts", "VOLATILITY", "Volatility", "Expert", "Defined-risk ITM volatility structure."),
  research("short_guts", "Short guts", "SIDEWAYS", "Sideways", "Expert", "Short ITM volatility structure with tail risk."),
  pending("double_diagonal", "Double diagonal", "SIDEWAYS", "Sideways", "Expert", "Two diagonal spreads requiring multi-expiry modeling."),
];

export const STRATEGY_CATALOG_META: Record<string, { bias: StrategyMarketBias; label: string; note: string }> = Object.fromEntries(
  STRATEGY_CATALOG.map((entry) => [entry.key, { bias: entry.bias, label: entry.label, note: entry.summary }]),
);
