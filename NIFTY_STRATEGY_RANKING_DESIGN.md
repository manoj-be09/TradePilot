# NIFTY strategy ranking: market fit, probability and trade contracts

Design review: 19 October 2026. Source baseline: `4a534edfe2ab21918b38b5c4b82d61fece5d895f`; Graphify matched that commit when checked. 20 October implementation: the Strip/template-identity prerequisite, a narrow backend market-fit **preview**, bounded frozen policy sets, and study-bound immutable NO_TRADE opportunity records are implemented in source. Opportunity inputs still use a client-submitted chain and hash-only provider bars, with source, cost and point-in-time availability reviews explicitly unverified. The qualified policy selector, calibrated probability and trading recommendation remain unimplemented. Complements STRATEGY_INTELLIGENCE_PLAN.md, PROBABILITY_RESEARCH.md and COST_MODEL.md.

## 1. Decision and scope

Build this capability. It directly serves the NIFTY paper-trading goal. The ranking unit is a complete **strategy + strikes + actual expiry + quantities + entry/exit policy + costs + decision timestamp**, not a strategy name. The same iron condor can be attractive or unattractive at different prices, widths and holding periods.

The default ordering should be **best supported after-cost value within risk limits**. Probability of profit (POP) is a separately qualified forecast, not the ranking objective by itself. A hypothetical 90% chance of gaining Rs100 and 10% chance of losing Rs1,500 has expected P&L of minus Rs60 before costs. A high win rate can conceal a poor trade.

Deliver two clearly separate capabilities:

1. **Market-fit comparison:** deterministic, explainable exposure matching with explicit missing inputs. Initially ordinal labels, not a percentage masquerading as probability. This can be implemented before a predictive model is qualified.
2. **Evidence-qualified ranking:** policy-specific net outcome forecasts, uncertainty, capital and tail-risk constraints, independently evaluated on later data. Only this stage can publish a validated policy POP or claim demonstrated selection advantage.

Keep `NO_TRADE` in every decision. No existing strategy is presumed qualified. An incumbent is only a frozen benchmark unless its evidence passes the same gates. No automatic deployment, promotion, changed owner positions, or live orders is part of this design.

## 2. What the present app actually does

| Current component | Finding at the audited commit | Required change |
|---|---|---|
| F&O ordering, `apps/web/app/page.tsx:1263` | PCR OI above 1.05/below 0.95 selects fixed bullish/bearish lists. The score adds payoff-ratio credit and a capped historical average-R contribution after 20 closes. | Label as heuristic; replace with one backend-owned, versioned selector used by UI, replay and snapshots. |
| Entry ticket, `page.tsx:1308` | Generic stop = 40% of modeled maximum loss; target and trail derived from payoff. Most templates get only a chain-median spread filter. | Freeze strategy-specific complete policies; thresholds are hypotheses, not optimized rules. Check every selected leg, not just the chain median. |
| Historical frequency, `apps/api/main.py:1410` | Smoothed qualified-paper frequency; no future-policy calibration. Saved realized P&L is not all-in net return. | Retain as descriptive history. Never rename it POP or train net labels from it without valid cost provenance. |
| Options features, `packages/features/options.py:161` | IV rank/percentile are unavailable; synchronized historical term-structure inputs are absent. | Store missing values honestly; capture point-in-time chains before relying on these features. |
| Regime classifier, `packages/regime/classifier.py:65` | Existing trailing price/ADX/DI/breakout heuristic is separate from F&O ordering. | Reuse it as a frozen baseline, with proper bar availability, not as a learned probability model. |
| Option replay, `packages/backtesting/replay.py:182` | Daily-open/daily-close option replay. | Keep indicative. Build a matching intraday policy adapter before validating stops, targets or policy POP. |
| Payoff, `packages/options/payoff.py:41` | Exact piecewise-linear expiry extrema, breakevens and call-tail detection. | Reuse; keep costs, early exits, fill uncertainty and margin separate. |
| Registry, `packages/paper_trading/strategies.py:210` | `strip` contains one put and two calls, identical exposure to `strap`, despite its downside description. Existing tests mirror this error. | Versioned correction and independent economic invariant tests before admitting this family. Preserve old saved definitions, fills and outcomes. |
| Unversioned waiting deployments, `apps/api/main.py:1933` | They retain no template identity and resolve the current registry at fill. Saved explicit versions have identity checks; these legacy waiting requests do not. | Pin template identity when arming and verify before filling. Legacy unpinned Strip requests must fail closed for deliberate review/rearming; a registry-only correction would silently change their legs. Existing open positions continue using their saved actual legs. |

There are 26 same-expiry templates in the executable registry, but template availability is not evidence of predictive performance. A green regression suite does not certify the economic interpretation of every template. A correction test must check economics independently: with strike 100 and each option premium 5, a proper Strip (one call, two puts) returns 25 at spot 80 and 5 at spot 120; Strap returns 5 and 25 respectively. Both lose their 15 debit at spot 100. These are dimensionless synthetic unit-test values, not market results.

## 3. Public methods worth borrowing

| Source | Useful mechanism | Adaptation and limitation |
|---|---|---|
| [OptionStrat builder](https://optionstrat.com/tutorials/options-builder) | Clear legs, payoff, Greeks, expiry breakevens and scenario sliders. Its tutorial describes chance of profit based on implied move and notes constant-IV assumptions. | Show assumptions and distinguish expiry scenarios from policy forecasts. No paid features or subscription needed for this research. |
| [Sensibull payoff table](https://blog.sensibull.com/2023/07/06/payoff-table-on-strategy-builder-analyse-widgets/) | Separate target-date and expiry-date outcomes. | Show planned-exit scenarios separately from expiry maximum profit/loss. |
| [CME bull spreads](https://www.cmegroup.com/education/courses/option-strategies/bull-spread) | Call and put constructions can represent equivalent bullish payoff exposure. | Do not assume that a credit spread is inherently superior in high IV or count economic twins as independent evidence. Financing, actual prices, liquidity and margin can still differ. |
| [QuantConnect fill models](https://www.quantconnect.com/docs/v2/writing-algorithms/reality-modeling/trade-fills/key-concepts) | Explicit, testable fill assumptions. | Pin bid/ask, latency, quantity and nonfill models rather than infer execution from a chart. |
| [NY Fed, Malz](https://www.newyorkfed.org/research/staff_reports/sr677.html) | Option-smile-based risk-neutral distributions. | Useful as scenario inputs; risk-neutral probabilities are not automatically real-world policy probabilities. |
| [Calibration documentation](https://scikit-learn.org/stable/modules/calibration.html) | Disjoint calibration data and reliability diagnostics. | Evaluate the selected subset as well as all predictions; sparse samples can require withholding percentages. |
| [Bailey and Lopez de Prado, Deflated Sharpe Ratio](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf) | Selection bias and multiple-search effects. | Retain all attempted variants; a corrected performance statistic is not a trade POP estimate. |

These sources establish mechanisms, not a verified ranking of successful traders or proof of NIFTY profitability. No proprietary trader algorithm or audited live track record was recovered. The entry/exit constants below are our proposed experiment, not prescriptions attributed to these sources.

## 4. Inputs and evidence states

Every input has value, instrument identity, source/artifact digest, event time, availability time, quality state and allowed use. `UNKNOWN` is not `PASS`. Required evidence that is unknown or failed blocks the dependent forecast/entry; optional inputs can be omitted only in a separately validated model version.

At every **actual paper fill**, retain the server-observed chain digest and quality/timestamp basis, actual listed expiry and contract identities, calendar days to expiry, chain-scoped call/put volume and open interest (including change), PCR, ATM IV/skew, valid two-sided quote count, Greek-field coverage, quality issue codes and median spread, and each filled leg's bid/ask, displayed quantities, volume, OI, IV/Greeks and quote/book timestamps. These are observed input records, not market-wide totals or exchange-certified executable fills. The provider's displayed bid/ask quantity is **not** a count of buyers or sellers. Keep buyer/seller counts, news, validated support/resistance and realized volatility explicitly unavailable until separate timestamped sources and frozen definitions exist. OI concentration may be shown descriptively but is not a validated support/resistance level. [Upstox option-chain fields](https://upstox.com/developer/api-documentation/get-pc-option-chain/), [Upstox full-feed fields](https://upstox.com/developer/api-documentation/v3/get-market-data-feed/).

For prediction research, predeclare a small candidate feature set rather than admitting every recorded field: prior completed-bar direction/range, same-horizon realized volatility, contemporaneous IV/skew and spread, DTE, OI/volume participation, and portfolio capacity. Add event/news flags only from a licensed source with publication **and ingestion** timestamps before the decision; never reconstruct them from later headlines or model memory. Missingness, source corrections and delayed arrival are features of the evidence, not values to fill with future data. Train only on policy-matched, after-cost outcomes with disjoint chronological calibration/validation and protected holdout. A single button may compare current templates under one displayed chain, but it must not arm every template or turn one quote snapshot into a backtest or performance ranking.

| Input group | Initial use | Important qualification |
|---|---|---|
| Direction | Completed NIFTY bars, trailing returns, EMA, ADX/DI, breakout and range | Fit transforms only on earlier data. The NIFTY cash index has no directly traded volume; do not invent index VWAP. A futures VWAP proxy requires explicit instrument/basis provenance. |
| Volatility | ATM IV, past realized volatility, later a forecast of realized variance and IV history | Compare compatible horizons/annualization. IV percentile is relative price history, not proof options are overpriced. Expiry IV versus recent intraday RV is not a like-for-like edge estimate. |
| Surface and contract | Moneyness, actual expiry/DTE, skew, term structure, aggregate Greeks | Read actual listed contracts/lot sizes. Never hardcode an expiry weekday or substitute another expiry when lookup fails. |
| Session and events | Decision time, verified session, scheduled event times known then | Event uncertainty is a reason to abstain in the first study. Today's knowledge cannot be backfilled into historical features. |
| Execution | Per-leg bid/ask, available size, exchange-book timestamp evidence, receive time, spread, synchronization and latency | Last trade time or receive time does not prove quote freshness. No midpoint-fill assumption. |
| Account | Free/reserved capital, existing exposures, loss limits, risk settings | Account-scoped; an attractive standalone trade may worsen the portfolio. |

OI/PCR may be a descriptive input after evaluation, but it is not an independent directional truth: outstanding puts can represent hedges and calls can be covered positions. Do not infer dealer gamma from aggregate OI without position-direction evidence.

Keep three independent statuses in the UI: **research evidence**, **current market fit**, and **entry/account readiness**. A market-fit shortlist can be visible while all entries remain blocked.

## 5. Concrete strategy contracts

### Common notation and exact payoff

`Q` = actual units per lot times accepted lots; `D` = net debit per unit; `C` = net credit per unit; `W` = strike width. All legs in these recipes have the same actual expiry. Buy premiums use admissible asks and sell premiums use admissible bids under the declared fill policy. `B` = modeled expiry maximum loss in rupees; `G` = modeled expiry maximum gain.

For signed leg units `q_j`, entry price `p_j`, and expiry underlying `s >= 0`:

`gross_payoff(s) = sum(q_j * (intrinsic_j(s) - p_j))`

Evaluate at zero, every strike and the tail slope, using the existing payoff engine. Do not call the edge of a plotted range the maximum. With actual saved units use multiplier 1; do not multiply by lot size twice. The formulas below are **before costs** and assume the complete portfolio remains intact to expiry. If a cost depends on terminal state, apply it scenario by scenario; do not subtract one fixed fee and claim an exact all-in bound. Early liquidation and legging can differ from these expiry bounds.

### Entry and exit policy common to the initial research recipe

The following numeric constants are a **proposed version `nifty-intraday-fit-v0` for falsifiable paper research**, not deployed settings or proven optima. Changing any of them is a new policy/search variant.

- Normal verified sessions only. One decision after the completed 09:55–10:00 IST bar has actually arrived, with a 10:01 latest entry cutoff. No repeated intraday selection in v0.
- Use exactly the last 250 complete, ordered 5-minute index bars through that decision, including prior sessions. Session calendar gaps are valid; unexplained missing bars invalidate the input. Pin aggregation, overnight-boundary treatment, indicator implementation and availability timestamps. Never fill at the already-observed bar close.
- Choose the earliest actual listed expiry with **5–10 calendar days** from the local decision date; otherwise abstain. This filters the opportunity population and must be reported.
- Reference strike `K0` is the listed strike nearest spot, lower strike on an exact tie. `K_i` is the i-th listed strike above/below it. Every required strike must exist in the reviewed contract set. No ad hoc widening or alternate expiry search after seeing outcomes.
- One complete lot per independent shadow policy; never round a risk-based size up to one lot. If one lot exceeds the study/account limit, skip it. Capital and maximum rupee loss must be frozen before study execution; no owner amount is assumed here.
- For v0, declare exchange-book age at decision <=2 seconds, maximum inter-leg timestamp skew <=1 second and each leg's `(ask-bid)/mid <=5%`, with positive valid prices and enough displayed size. These are proposed eligibility limits, not claims that present Upstox fields satisfy them. Actual additional latency/slippage must be measured/versioned; an unknown required timestamp still blocks qualification.
- Candidate preview and limits use a single admissible snapshot. A paper basket may fill only on the next admissible snapshot after the declared latency, before cutoff, when the entire frozen net limit is satisfied. Otherwise record `NO_FILL`; never choose earlier favourable per-leg prices. This is a synchronized basket simulation, not proof of executable exchange atomicity. Sequential/partial-fill execution requires a separately validated policy.
- Require net conservative value and capacity gates for recommendation-qualified use. Before those exist, these recipes produce **research-only shadow comparisons**, not actionable recommendations.
- Compute provisional stop `S = 0.25 * B` and target `T = 0.25 * min(B,G)` for finite `G`; if gain is unbounded use `T = 0.25 * B`. Recheck these and all limits against the prospective fill snapshot **before accepting the simulated basket**. Round amounts to rupee paise consistently. After acceptance, freeze the thresholds from that actual simulated fill; never recompute them from later marks.
- Before accepting entry, reject a recipe if its estimated immediate round-trip spread, fees and measured slippage exceed `0.25 * min(S,T)`. This intentionally rejects uneconomic narrow/multi-leg setups rather than pretending costs are negligible. Cost-model identity and sensitivity assumptions are required. A later discovered cost/slippage breach remains a recorded outcome and risk exception; never erase a completed fill or relabel it `NO_FILL` to make results look better.
- Evaluate executable **net liquidation P&L** at 5-minute checkpoints after entry. Trigger stop at `P&L <= -S`, target at `P&L >= T`; also evaluate the strategy invalidation below. No trailing stop, averaging, roll, adjustment or reentry in v0. Continuous monitoring is a different future policy.
- At 15:10 IST send an exit intent for every remaining study position. This is a deadline for intent, not guaranteed closure. Missing quotes create a pending exit; retry on admissible observations, escalate, block further entries and retain unresolved/censored outcomes. Never manufacture an end-of-day fill. Unresolved exposure still counts in risk; the first study pauses pending review if it cannot establish a reliable close path.
- Exit priority if reasons coincide: safety/time exit, stop, regime invalidation, target. Use the next admissible fill after the decision latency. An intra-bar stop/target hit cannot be inferred favourably from OHLC. Stops are triggers, not guaranteed maximum losses.

For the basic directional/range baseline, use the existing pinned `classify_market_regime` output: bullish = `TRENDING_UP` or `BREAKOUT`; bearish = `TRENDING_DOWN` or `BREAKDOWN`; range = `RANGE_BOUND`. Other/unknown outputs mean abstention. These labels are descriptive hypotheses. Do not silently merge high-volatility, unclear and sideways sessions.

### Primary comparison families

All rows inherit the common policy, exact price limits, risk gates, stop/target and time exit above. The table defines a comparison universe; it does **not** authorize seven simultaneous challengers.

| Setup | Frozen legs / entry fit | Additional invalidation | Gross expiry maximum gain / loss | Expiry breakeven(s) |
|---|---|---|---|---|
| Bull call spread | Bullish; buy call `K0`, sell call `K+1`; `0 < D < W` | Completed checkpoint no longer bullish | `Q(W-D)` / `QD` | `K0+D` |
| Bull put spread | Bullish; sell put `K0`, buy put `K-1`; `0 < C < W` | Completed checkpoint no longer bullish | `QC` / `Q(W-C)` | `K0-C` |
| Bear put spread | Bearish; buy put `K0`, sell put `K-1`; `0 < D < W` | Completed checkpoint no longer bearish | `Q(W-D)` / `QD` | `K0-D` |
| Bear call spread | Bearish; sell call `K0`, buy call `K+1`; `0 < C < W` | Completed checkpoint no longer bearish | `QC` / `Q(W-C)` | `K0+C` |
| Iron condor | Range; buy put `K-2`, sell put `K-1`, sell call `K+1`, buy call `K+2`; distinct ordered strikes; `0<C<min(Wput,Wcall)` | Range label lost or spot closes outside the short strikes | `QC` / `Q(max(Wput,Wcall)-C)` | `K-1-C`, `K+1+C` |
| Long straddle | Later volatility study: buy call and put at `K0`, `D>0`; admit only when a separately validated same-horizon movement/IV-path model finds net value | Frozen volatility-fit condition fails; common stop/target/time rules | Unbounded upside gain / `QD`; downside gain at spot zero is `Q(K0-D)` | `K0-D`, `K0+D` when nonnegative |
| Long strangle | Later volatility study: buy put `K-1`, call `K+1`, `D>0`; same qualification requirement | Frozen volatility-fit condition fails; common stop/target/time rules | Unbounded upside gain / `QD`; downside gain at zero is `Q(K-1-D)` | `K-1-D`, `K+1+D` when nonnegative |

The volatility-fit condition for the last two rows is explicitly **unavailable until its own model/policy is frozen and validated**; until then their entry decision is `ABSTAIN`. Low historical IV alone is not their entry trigger. Unlimited upside does not mean high expected gain, and NIFTY's downside price range is finite.

The bull call and bull put recipes above use different strike pairs, so they are not identical trades. At matched strikes, expiry and appropriate financing, call/put vertical constructions can be economically equivalent. Deduplicate payoff exposure for search/uncertainty accounting while retaining separate liquidity/capital/implementation records. Never apply a blanket `high IV => credit`, `low IV => debit` preference: a vertical's net Greeks depend on its strikes.

Remaining templates stay in the catalogue. Iron butterflies, call/put butterflies and condors, broken wings, backspreads, inverse condors, guts, strip/strap and standalone calls/puts require individually frozen recipes, economic tests and evidence before entering this selector. Multi-expiry, futures-linked, covered and unbounded-loss structures remain outside this release. Every admitted later template must supply the same exact contract and payoff computation; unsupported means unavailable, not low-ranked.

## 6. Historical outcome model and probability

The forecast target is:

`p(a,t) = P(net realized P&L of policy a > 0 by its horizon | information available at t, entry filled under its frozen policy)`

Loss, flat, no-fill, unresolved and censored are separate outcomes. Conditional fill POP is not the probability of making money from submitting an order. Show fill probability separately and use a consistent opportunity-level outcome for ranking. In the simple all-or-none model, a nonfill gives zero trading P&L (and any real charged costs); partial-fill losses require their own model.

Start with a compact regularized logistic model for positive net outcome plus regularized, shrunk conditional gain/loss estimates, or a coherent empirical distribution over matched policy replay outcomes. Use policy/expiry/horizon identifiers and a small predeclared feature set. Estimate net expectancy, tail loss and uncertainty alongside POP. Do not synthesize a thin-tailed normal distribution from mean and standard deviation and call it observed option risk.

Fit the probability model on training data, calibrate on a disjoint later window, then evaluate on a still-later validation window. A simple sigmoid calibrator is a candidate, not automatically an improvement. A raw logistic output is not proof of calibration. Richer boosting, regime mixtures and neural models are later challengers only if simpler baselines fail for demonstrated reasons.

Evaluate reliability curves, Brier score, log loss, calibration slope/intercept, class counts and uncertainty. A better Brier score alone does not establish good calibration. Check policies, regimes, DTE, decision horizon and **the actually selected winner subset**. Report distinct sessions/expiries as well as trade rows; fifty spreads on one day are not fifty independent observations.

Use session/expiry block resampling and predeclared dependence treatment for uncertainty. An initial proposed display tolerance is at most a 20-percentage-point-wide 95% interval for a supported forecast group. This is a **precision** requirement, not a calibration test: a confidently incorrect forecast can pass it. Separately preregister forecast groups and a calibration-error tolerance; a possible research criterion is that a dependence-aware upper confidence bound on absolute group calibration discrepancy is no more than 10 percentage points on unseen selected trades, with multiplicity addressed across groups. Treat these settings as acceptance hypotheses, not universal guarantees or per-trade conditional coverage theorems. Merely failing to reject miscalibration does not establish calibration. A fixed trade count does not establish qualification. Do not print precise POP outside observed support or while material labels are missing.

Historical modeled replay, forward simulated counterfactuals, actual forward-paper fills and operator records retain separate provenance and evaluation reports. Unselected strategies do not acquire real fill outcomes merely because their hypothetical return was computed. Existing owner-selected trades alone are selection-biased for ranking every alternative. Never pool synthetic fixtures with market evidence or retrofit current features/costs onto old trades.

## 7. Ranking and abstention algorithm

For every scheduled opportunity:

```text
snapshot = freeze only information actually available now
if session, required source/contract/time/cost evidence fail: record ABSTAIN
construct only the predeclared candidate policies and exact baskets
reject missing legs, unsupported rules, economic duplicates as independent trials,
       excessive spread/cost, undefined risk, or inadequate capital
assess current directional/volatility/exposure fit with reasons
if only heuristic evidence exists:
    show an ordinal research shortlist; POP unavailable; do not qualify entry
else:
    predict filled and opportunity-level net outcomes under frozen policy
    estimate uncertainty, expected shortfall and portfolio stress exposure
    reject out-of-support, stale, uncalibrated or risk-limit-failing candidates
    order eligible candidates by conservative expected net return / reserved capital
    choose NO_TRADE if none clears the predeclared value hurdle
persist all accepted/rejected alternatives, action, reasons and versions
recheck executable quote and account gates before any separately authorized entry
```

Proposed selection objective, over a common decision-to-exit horizon:

`score(a,t) = lower_95_bound(E[opportunity_net_P&L(a,t)]) / reserved_capital(a,t)`

Reserved capital is a documented premium/margin/liquidity-buffer model, not the stop amount. Never double-count debit or allow received credit to create fictitious free capital. If comparable margin/capital is unavailable, capital-efficiency ranking is unavailable. Use frozen fixed study capital for research-only comparisons instead; disclose that different denominator rather than claiming broker efficiency.

Apply hard caps for expiry modeled loss, expected shortfall, stress loss, portfolio exposures and daily loss; do not blend a risk breach into a compensating weighted score. Tail estimates require support; stress scenarios complement rather than prove their probabilities. Use a minimum conservative value hurdle greater than or equal to zero, set before evaluation, with explicit residual execution/model uncertainty assumptions.

The lower bound is a model-dependent uncertainty estimate, not a guaranteed floor. Account for searching many candidates using simultaneous/selection-aware uncertainty and evaluate the **whole selector** on protected later data. A pointwise 95% interval is insufficient protection when selecting the largest of many noisy estimates.

No-trade contributes zero incremental trading P&L and no new position; it does not eliminate risk from existing holdings. Keep an eligible incumbent on statistically inconclusive comparisons; abstain if it fails current risk/data gates. Require predeclared improvement and review cadence for changing policy versions. Do not switch daily just because the top displayed score changes. Open positions follow their saved exit policy, not a freshly reranked entry list.

### Conditional shortlist, not today's trade recommendation

| Observed situation | Structures to compare first | Decision boundary |
|---|---|---|
| Supported upward trend | Bull call / bull put configurations | Compare actual net payoff, price, liquidity and capital; no fixed universal winner. |
| Supported downward trend | Bear put / bear call configurations | Same gates; bearish label alone is not a trade. |
| Stable range with evidence of attractive short-vol exposure | Defined-risk iron condor | Event, tail and four-leg cost risk can make no-trade win. |
| Supported movement/volatility expansion not already overpriced | Long straddle / strangle, in a later qualified study | Expected move must overcome premium, decay and costs; low IV alone is insufficient. |
| Mixed signals, missing evidence, abnormal spreads or event uncertainty | No trade | Explain exactly which input or risk gate failed. |

There is no honest current-market numeric leaderboard in this review: no fresh authenticated executable chain, complete qualified policy dataset or calibrated selector has been established. Do not invent a current NIFTY level, trade price, POP or expected return to populate examples.

## 8. First frozen experiment and validation

Do not launch the entire catalogue as a search batch. Proposed first study:

- Benchmark/incumbent: **NO_TRADE**, until an existing fixed policy is actually admitted by evidence review.
- Challenger 1: the directional vertical policy, choosing bull call on the bullish baseline label and bear put on the bearish label; otherwise abstaining. Its two constructors and all decisions are part of one explicitly declared policy, not hidden extra searches.
- Challenger 2: range-gated iron condor under the same clock, costs and risk contract.
- Evaluate a frozen combined selector as a separately counted searched policy. The comparison universe, attempted parameters, reruns, failures and combined rule all belong to one canonical parent experiment family. No replacement challenger after inspecting the final holdout.
- Each policy has its own independent shadow ledger and starts validation with a clean account. Use the same opportunity calendar, capital convention and input availability. Do not run training into validation and simply filter out earlier trades afterward.

These first challengers have mutually exclusive regime conditions. This study can test routing, value filtering and abstention; it cannot establish skill at choosing between multiple simultaneously eligible strategies. A later separately frozen overlapping-candidate comparison is required before claiming demonstrated competitive ranking advantage. That extension consumes its own declared parent budget and untouched evaluation data.

Before execution, register objective, numeric study capital/loss caps, parent trial budget, data/cost artifacts, execution limits, dates, protected holdout, uncertainty method and review schedule. Those values are not inferable from this design and are not filled with fabricated owner preferences. Scheduled evaluations remain disabled until the actual study and runtime gates are satisfied.

Chronological protocol: training -> disjoint calibration -> validation, then a sealed final holdout for the frozen complete selector. Purge overlapping trade/label intervals; indicators may use prior observable warmup but fitted parameters may not use future data. All candidates for a decision session stay in the same fold. Labels must have matured before fitting. Choose window lengths using coverage and dependence requirements before outcomes, not by selecting the best-looking dates.

Compare paired session results against no-trade, the fixed candidates and the simple frozen regime selector. Measure net return, continuous marked drawdown, expected shortfall, stress, turnover, time in market, costs, fill/missingness rates and dependence on a few sessions. Include sensitivity to adverse spread/slippage and regime transitions. Predeclare statistical and practical advantage hurdles; inconclusive results do not qualify a candidate.

Follow with immutable prospective shadow records and, only when operational evidence permits, qualified paper observations. Drift, expired artifacts, loss of support or fill deterioration downgrade/withhold estimates. New data may justify a reviewed new version; it never overwrites old predictions or automatically promotes a challenger.

## 9. UI contract

Each ranked card should show:

- Action/eligibility and as-of time first: `RESEARCH COMPARISON`, `ENTRY BLOCKED`, `QUALIFIED PAPER CANDIDATE` or `NO TRADE`.
- Exact legs, expiry, lots/units, executable net limit, entry window and signal; short explanation of fit and rejection alternatives.
- Planned stop, target, exit checkpoints, time-exit intent and invalidation; explicitly separate these from expiry maximum profit/loss.
- Breakevens at expiry, current delta/gamma/vega/theta with model time, reserved capital, estimated costs and stress outcomes.
- Validated net POP and its support/uncertainty only when qualified; otherwise `Unavailable — reason`. Historical frequency remains in a separate descriptive section.
- Expected net P&L and downside range only with evidence, plus policy/model/data/cost versions and recommendation expiry.

Use green/red/blue/purple for bullish/bearish/range/volatility exposure **with text labels**, not for implied safety or profit certainty. Use separate status icons/text for readiness. Keep the top few relevant comparisons visible and details expandable. A catalogue entry must not inherit qualified styling merely because its paper template exists. A manual POP sort never bypasses risk gates.

Include separate scenario views for the planned exit and expiry, changing underlying price and IV. Any pre-expiry valuation is a model scenario, not an executable quote or a probability distribution. No need to add a new charting/research platform to deliver this.

## 10. Implementation sequence and acceptance

| Step | Smallest deliverable | Acceptance evidence |
|---|---|---|
| 1. Economics and policy identity | Pin/check armed deployment template identity, then correct Strip with a new version; audit ratio/wing semantics | Independent directional/tail/extrema invariants, legacy unpinned Strip fail-closed behavior, saved-version preservation, no mutation of prior trades |
| 2. Backend market-fit comparison | Reuse existing payoff, classifier and readiness modules; remove independent UI scoring as authority | Deterministic ordering/ties, per-leg quality, missing-input abstention, no probability fabrication, API/UI agreement |
| 3. Point-in-time opportunity ledger | Immutable scheduled snapshots and excluded/nonfill records, canonical parent budget | Future/late data rejected, idempotency, account isolation, digest/time references; no synthetic owner records |
| 4. Matching policy evaluator | Intraday quote-checkpoint adapter and versioned costs/capital | Entry after signal availability, same policy in replay/shadow, no favourable OHLC ordering, unresolved exit handling, independent validation accounts |
| 5. Frozen first study | Two challengers and no-trade, protected chronological evaluation | Retained failures/search accounting, paired session reports, uncertainty and risk gates; failure to find advantage is an acceptable research result |
| 6. Qualified prediction/selection | Small probability/outcome model and conservative selector | Later selected-subset calibration, complete selector holdout, support/drift abstention and prospective evidence |
| 7. Read-only UI release | Cards, scenarios and evidence explanations | Populated authenticated API acceptance and connected Edge desktop/phone checks, with actual versus synthetic evidence identified |

Reuse the modular application and existing migrations/research stores; add storage only for information the current contracts cannot represent. Do not build RL agents, paid AI jobs, broad strategy crawlers, automatic genetic optimization, another database or another scheduler for this slice. LLMs can explain evidence and help propose reviewed hypotheses; they do not manufacture price forecasts, POP or trading permission.

Software correctness, deployed acceptance, data quality and scientific advantage are separate release gates. This design completes the requested algorithm specification; it does not mark any unperformed gate complete. Existing live-order disablement, additional-index defaults, account data, illustrative historical costs and the recorded test-only expiry position remain unchanged.

2026-10-20 implementation note: the first part of step 3 now persists an authenticated, study-bound point-in-time market-fit abstention using the existing immutable recommendation table. It retains all preview alternatives and input identity. When a current provider history supports the regime, the record hashes the exact 250 completed 5-minute bars passed to the classifier, including instrument and interval. This is hash-only identity: the bars are not retained as a reviewed artifact, their publication/availability at the decision time is unproven, and the option chain remains client-submitted. It is an unqualified opportunity observation, not a validated no-fill event or the completed opportunity ledger. Reviewed source/cost evidence, protected evaluation access, and policy-matched outcomes remain open.

The opportunity writer also verifies that the assessed client-chain digest matches the chain being saved. It records the expiry, reference strike, each preview candidate's quoted legs and before-cost per-unit expiry economics, so later review can distinguish different complete baskets on the same day. These observations remain research-only; a digest does not verify the chain source or make an entry fillable.

The next slice adds a separate immutable policy-set record for one incumbent and at most two challengers, with a NO_TRADE comparator, versioned template digests, study/cost/budget binding and fail-closed template drift. Opportunity snapshots carry the policy-set digest when present. The rule-artifact digest is identity only until independently reviewed, and the current market-fit preview may display structures outside the frozen set with an explicit unfrozen flag. No policy has been economically qualified or activated. Actual no-fill evidence, point-in-time bar artifacts and independent policy-matched validation remain open.

Append-only outcome events now accept explicitly unverified operator-recorded shadow statuses for unresolved observations, modelled nonfills and candidate rejections. They retain observed and availability times and source digests while requiring a frozen candidate for shadow nonfill/rejection. They do not establish actual order attempts or executable fills. Point-in-time reviewed artifacts, independent policy-matched validation, outcome maturation and qualified selection still remain open.
