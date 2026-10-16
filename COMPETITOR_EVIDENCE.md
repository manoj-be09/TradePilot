# Competitor evidence and implementation decisions

## 13 October 2026: first research-history increment

### QuantConnect / LEAN — priorities 1 and 2

Source: [official Research Guide](https://www.quantconnect.com/docs/v2/cloud-platform/backtesting/research-guide).

The documentation describes hypothesis-driven research and a research panel that tracks backtest count, parameter count and research time as rough overfitting indicators. This is documentation evidence; no signed-in QuantConnect workflow was exercised.

TradingPilot decision: retain chronological-review requests, insufficient samples and sanitized provider failures in account-scoped history. Show this behind an expandable panel. Record the returned report digest and distinguish requested capital from the allocation in an already-frozen study. A repeated request returning the same study is not an independent experiment.

Remaining: an immutable candidate/failure registry, full code/data/feature manifests, explicit search budgets and comparable trial-family counts. Request history alone does not satisfy these acceptance criteria or establish strategy profitability.

### AlgoCrab — dashboard experience benchmark

Requested URL: `https://algocrab.com/algo-trading`. A public unauthenticated request on this date redirected to `https://algocrab.com/login` with HTTP 200. A script URL delivered by the resulting page was downloaded as an inert artifact:

- URL: `https://algocrab.com/_next/static/chunks/app/page-04599977a06fc523.js`
- Script SHA-256: `f1f61d783bac1ed7b2fd1d8b1a20ce475d233e98d3dadea7a0b50ffaa7b9693d`
- REA version: 6.4.0; operation: `analyze_javascript_application`, directory format, summary view.
- Parent evidence: `ev_ee95f66622c321bf1128faa348f731b960bffd91c97d6d9ebb8d949350f7e73e`.
- Summary evidence: `ev_1818c02b19a414aafc07880e8c1cbf94de456ece188c79fc85a993a2591433d2`.
- Parsed one JavaScript file, 59,937 text bytes; recovered 22 modules; no parse failures.
- Coverage: partial application graph; 1,951 unresolved semantic relationships. No target code was executed by this static analysis.

This establishes that REA can analyze the retrieved public artifact. It does not establish private strategy-card, order-history or decision-log behavior. The delivered chunk name is not proof of which UI route executes its modules. Unfetched bundles, server code and signed-in state remain unknown. No competitor source was incorporated into TradingPilot.

A dedicated browser launch was rejected by automatic approval review because sandbox approval is disabled. Runtime comparison is parked until an authorized supported browser connection is available. Prior user screenshots remain visual references, distinct from this static evidence.

### Development workflow

Graphify's returned source links remain indexed at `a6fc594619e20ef00ea9d0fe890a9a0cd984bc03`, behind local Git. Use its symbol navigation with current-source verification. Refresh after the meaningful batch before a larger refactor; the current connector does not expose index refresh.

Use the eight priorities in PROJECT_PLAN.md as the acceptance list. These evidence records and small increments do not mark a priority complete. Preserve paper execution gates, versioned strategies, user data and the disabled live-order boundary.

### 15 October 2026 — focused monitoring UI refinement
REA re-analysis of the saved public AlgoCrab artifact again returned summary evidence ev_1818c02b19a414aafc07880e8c1cbf94de456ece188c79fc85a993a2591433d2 (22 modules; partial graph,1951 unknown semantic relations). This does not establish authenticated dashboard layout. Runtime REA requires a CDP endpoint/target not provided by the current Edge extension; initial calls without that schema were rejected, no browser target analyzed. Design decisions below use existing owner references and TradingPilot browser observations, not a claim of recovered competitor UI: default to open positions, separate closed history with counted tabs, surface contract identities, improve typographic hierarchy and light surfaces. Graphify index remains a6fc594; connector exposes no refresh/reindex operation. Source verified directly against current checkout.


### 15 October 2026 — authorized signed-in Edge review
The owner authorized inspecting the signed-in AlgoCrab account in connected Edge. Reviewed all six main navigation destinations: Dashboard, Algo Trading, Broker Panel, Backtest, Bridge Tool and Help Center. This supersedes the earlier runtime-access limitation for browser observation only; it is not REA CDP analysis or recovered private source.

Observed compact persistent navigation, restrained white surfaces, small headings, a prominent summary metric, counted/tabbed work areas and separated activity/marketwatch panels. Strategy and bridge screens separate catalogs from logs/order history. Backtest exposes four steps (strategy, symbol, properties, optional properties), with Continue disabled before selection. Help separates broker setup guides from searchable help. Broker Panel separates balances, orderbook, tradebook, net positions and holdings.

The account has no connected broker or active strategies and an expired subscription. Populated trade states, execution, strategy configuration and paid workflows were not verified. No broker connection, strategy application, purchase, webhook inspection or competitor account mutation was performed. No private account identifiers or source code are retained here.

TradingPilot application: preserve compact summary/metric hierarchy, distinct screen purposes and expandable evidence. Use light blue for the net summary, pale green for gross profit and pale peach for gross loss, with smaller numbers and text labels that remain meaningful without color. Keep provenance and disabled-live-order warnings visible.

Graphify recheck: promoted graph still c24ea1be3f83372b87f95759d38ffd8b007faabd (indexed 13:22 UTC), behind local 0788f5d before this increment. Current source was checked directly; no refresh is claimed.


### 16 October 2026 — adaptive research systems and public results

Owner requested external evidence before selecting reusable components. Primary-source desk review only; no code imported or target run.

- Microsoft RD-Agent(Q), paper v2: https://arxiv.org/html/2505.15155v2 . Authors report CSI300 backtest ARR14.21% and IR1.74 for joint factor/model optimization, compared with baselines. This is a reported simulation on its data/portfolio/cost setup, not independently verified live performance or NIFTY options evidence. Appendix describes chronological splits and market-specific assumptions. Closest conceptual fit: propose hypotheses, implement, evaluate and retain research feedback. Code https://github.com/microsoft/RD-Agent has MIT license (LICENSE inspected).
- Qlib https://github.com/microsoft/qlib and https://qlib.readthedocs.io/en/latest/component/online.html : experiment workflow, rolling training, online model history and simulation of the update routine. Documentation notes public-data limitations on next-day order generation. README numerical examples are backtests, not live records. MIT LICENSE inspected. Candidate reuse: workflow/manifests and comparison patterns; no assumption that its default market data serve Indian options.
- FreqAI https://www.freqtrade.io/en/stable/freqai-running/ : periodic retraining, model identifiers, expiry controls, persisted predictions and recovery. Documentation explicitly labels continual_learning experimental and warns of overfitting/local minima. No standardized independently verified profitability found in inspected docs. Parent Freqtrade LICENSE is GPLv3 (inspected); learn architecture, review obligations before any source incorporation. Do not replace current trading runtime with a crypto bot.
- QuantConnect https://www.quantconnect.com/docs/v2/writing-algorithms/optimization/walk-forward-optimization : walk-forward re-optimization schedule and frequency/overfitting tradeoff. LEAN https://github.com/QuantConnect/Lean/blob/master/LICENSE Apache2.0 inspected. Platform capability is not a profitable strategy record. Reuse validation/reality-model ideas without rewriting our modular engine.
- Numerai https://blog.numer.ai/jpmorgan-secures-500m-capacity/ (26Aug2025): fund self-reports2024net25.45% with stated Founding Class fee assumptions; this is stronger live-business evidence than a demo backtest, but no independent audit was reviewed and it does not isolate the causal benefit of daily self-improvement. Learn forward scoring and diversified signal aggregation; proprietary fund strategies/data are not available to copy.

Conclusion: borrow testable research architecture and suitably licensed components, not advertised returns or opaque winners. No inspected source proves an autonomous daily-changing NIFTY options engine will be profitable. Our next adoption order is bounded hypothesis/evaluation loop, strict chronological/held-out tests and cost stress, forward-paper challenger evidence and conservative promotion/abstention. Preserve traceability and license notices; third-party datasets/models have their own rights and execution assumptions.
