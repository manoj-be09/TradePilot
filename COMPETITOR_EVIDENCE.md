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
