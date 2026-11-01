# Candidate algorithm study

Research only. Results are simulated history, not a forecast. Nothing here reaches paper trading, and the forward period that starts 2 November 2026 keeps its 26 frozen templates. Result numbers come from private data, so they stay in `work/bt/` and out of this public repository.

## Protocol, fixed on 1 November 2026 before any candidate result was seen

**Question:** does choosing when to trade, and which template, beat taking the same templates every week, on both win rate and profit after charges?

**Data:** imported 5-minute NIFTY weekly option candles. Expiries before 1 November 2025 are for development. Later expiries are the holdout.

**Rules:** the 12 fixed rules in `packages/backtesting/candidate_algorithms.py`. Each reads the signal in `packages/backtesting/entry_signals.py` at 09:20 on the arming day, using only bars closed by then, and names a template or sits out. The template's own entry and exit rules then run unchanged.

**Costs:** the dated charge schedule and a 0.25% half-spread, as in the template replays. Each trade is scaled to the current lot of 65 for comparison, because the lot size changed during the period.

**Advancing from development:** at least 15 trades, positive average after charges, t of at least 1.5, and both average and win rate above trading the same templates every week. At most 3 rules advance, highest t first.

**Passing the holdout, run once:** positive average after charges, at least 15 trades, t of at least 2.45 (two-sided 5% shared across 3 rules), average and win rate above trading every week, worst trade within ₹20,000 and drawdown within ₹60,000. A rule that passes is also rerun at a 0.5% half-spread to see whether the gain survives dearer fills.

**A pass does not make a rule validated.** It only makes the rule a candidate to run in paper trading after 14 December 2026.

## Round 2, fixed on 1 November 2026 before any round 2 result was seen

Round 1 advanced no rule, so the holdout was not run. It also showed why: on one-strike structures the most a trade can lose is close to what charges cost, so no entry filter can rescue them.

Round 2 adds 8 rules on 7 wider structures, in `WIDE_STRUCTURES`, on the same development expiries and under the same advancing and holdout tests. A rule that trades every week compares with the narrow templates it replaces. The template exit formula is unchanged. These structures are not in the paper strategy registry.

The search now totals 20 rules, and this is the last round before the holdout. If none advances, the holdout stays unread and the answer is that no rule was found.

## Outcome, 1 November 2026

**0 of 20 rules advanced, so no rule was found and the holdout stays unread.** Expiries from 1 November 2025 on have not been used by this study and remain available for one future test.

- Choosing when to trade did not rescue one-strike structures. Their losses come mostly from charges and spread.
- Wider wings raised the win rate a lot but did not make the structures profitable after charges: the occasional larger loss took back the many small wins. Higher win rate alone is not better.
- Selling options only when they looked dear did not clear the test, on narrow or wide structures.
- Trend and fade rules did not clear the test either.
- Buying a straddle three days out when options looked cheap was the one reading worth watching, but with too few trades to advance. It stays an idea, not a finding, and must not be retuned on these expiries.

Numbers are in `work/bt/candidates-development-0.25.json` and the log beside it, both private and local.

## Known limits

- The holdout is not pristine. Whole-period results for the 26 templates, and the direction of the market in those years, were already seen before these rules were written.
- About 50 weekly trades per half at most, and fewer after filtering, in one provider's candles with no historical bid/ask.
- Exits are checked on 5-minute closes, so a stop can realize more than its amount.
- The straddle volatility reading is approximate, and the first expiries have no rank while the trailing window fills.
- Weekly expiry moved from Thursday to Tuesday during the period, which the rules do not model separately.

## Published rules study, fixed on 1 November 2026 before any result was seen

The owner asked for strategies published online to be tested. This is a new study, outside the 20 rules above, and the whole search now totals 31 rules. Rules are in `packages/backtesting/published_strategies.py`.

**No source offers an audited trading record, so nothing here is evidence that any trader succeeded.** SEBI reports that over 91% of individual F&O traders lost money in FY25, so a popular rule starts with no credit.

| Rule | Published idea | Our form |
| --- | --- | --- |
| `straddle_920_leg_stop` | [Zerodha forum analysis](https://tradingqna.com/t/detailed-analysis-of-920-short-straddle-strategy-is-it-stopped-working/130677) | Sell the nearest-expiry straddle at 09:20, 25% stop per leg, the other leg runs to 15:15. Naked |
| `straddle_920_no_stop` | [Zerodha forum post](https://tradingqna.com/t/reports-of-the-death-of-920-short-straddle-are-greatly-exaggerated/179061) | Same with no stop. Naked |
| `iron_fly_920_leg_stop` | Same thread's hedged form | Add wings six strikes out; a stopped leg closes its wing |
| `iron_fly_920_skip_expiry_day` | [Bank Nifty blog](https://bankniftyoptions-com.pages.dev/artigos/banknifty-920-straddle-strategy) | Same, not on expiry day |
| `condor_take_half` | [tastylive](https://www.tastylive.com/shows/market-measures/episodes/iron-condors-management-and-wing-width-05-09-2016) | Weekly wide condor, close at 50% of credit, no loss stop. Their 45-day entry cannot be tested on our data |
| `condor_take_half_when_rich` | tastylive, high volatility | Same, only in the top third of the trailing volatility premium |
| `iron_fly_take_quarter` | [tastylive](https://www.tastylive.com/shows/options-jive/episodes/why-manage-at-25-not-50-for-short-straddles-09-06-2016) | Weekly iron fly, close at 25% of credit, no loss stop |
| `short_strangle_weekly` | [Bangur 2020](https://www.risk.net/journal-of-investment-strategies/7721271/strangle-to-resuscitate-evidence-from-india) | Weekly short strangle four strikes out, held. The paper is monthly and its exact rules are paywalled. Naked |
| `overnight_straddle` | [Muravyev and Ni, summary](https://www.cxoadvisory.com/equity-options/intraday-versus-overnight-option-returns/) | Sell at 15:15, buy back about 09:20 next session. Naked |
| `overnight_iron_fly` | Same | With wings |
| `opening_range_breakout` | [AlgoTest](https://docs.algotest.in/sample-algo-trading-strategies/range-breakout-strategy), [OneTradeJournal](https://onetradejournal.com/strategies/opening-range-breakout) | Buy an at-the-money option on the first close beyond the 09:15-09:30 range, up to 14:00; stop on a close beyond the far side; out by 15:15 |

**Tests:** one lot of 65, dated charges, 0.25% half-spread on every fill. A leg stop triggers on the bar's high and fills at the stop or at a worse gap open.

**Advancing, on expiries before 1 November 2025:** at least 15 trades, positive average after charges, t of at least 1.5. At most 3 advance, highest t first.

**Passing the holdout, run once:** positive average after charges, at least 15 trades, t of at least 2.45, worst trade within ₹20,000 and drawdown within ₹60,000. A rule that passes is rerun at a 0.5% half-spread. A naked rule can never be armed in paper trading as written.

**Limits of this study:** the rules are adapted to our data, so a failure tests our form of an idea, not every form of it. Spread on far strikes is probably wider than 0.25%. Stops on real orders can slip further than a 5-minute bar shows. A run of daily trades can share one market regime, so t may overstate certainty.

### Published rules outcome, 1 November 2026

**0 of 11 published rules advanced, so the holdout stays unread.** Across both studies, 0 of 31 rules advanced.

- The 9:20 straddle, with or without a stop, was positive after charges, but not clearly enough to separate it from chance. Without a stop, one day's loss went past the per-trade limit. It is naked, so paper trading could not arm it as written.
- Adding wings to the 9:20 straddle removed that tail, but extra charges and spread turned it negative.
- Taking profit at a set share of credit gave high win rates without a clear profit. The high-volatility condor had too few trades.
- The overnight idea did not survive charges and spread, which is what its own sources warn about.
- The opening range breakout won less than a third of the time and lost after charges.

Numbers are in `work/bt/published-development-0.25.json`, private and local.

## Global strategies study, fixed on 1 November 2026 before any result was seen

The owner asked for the search to go beyond Indian sources. This is a third study, and the whole search now totals 42 rules. Rules are in `packages/backtesting/global_strategies.py`.

**Evidence from other markets does not transfer by itself.** The strongest research also warns that costs and margin take much of the gross return: [Santa-Clara and Saretto](https://www.cxoadvisory.com/equity-options/good-deals-in-broad-market-index-options/) report that costs and margin calls cut the Sharpe ratio of a short strangle from 1.83 to 0.16, and [Gray](https://ideas.repec.org/a/wly/jfutmk/v36y2016i2p107-126.html) finds costs erode the Goyal and Saretto straddle.

| Rule | Published idea | Our form |
| --- | --- | --- |
| `weekly_put_write` | [Cboe WPUT](https://www.cxoadvisory.com/equity-options/performance-of-cboe-putwrite-indexes) | Sell an at-the-money put a week out and hold. Naked; no T-bill return is counted |
| `far_put_write` | [Early Retirement Now](https://earlyretirementnow.com/2021/11/10/passive-income-through-option-writing-part-9-2016-2021-backtest-guest-post-by-spintwig) | Sell a 5-delta put about 3 days out, once a week and held. Naked |
| `index_style_condor` | [Cboe CNDR study](https://ir.cboe.com/news/news-details/2016/Study-Analyzes-Performance-of-CBOE-SP-500-SPX-Options-Selling-Indexes-02-23-2016/default.aspx) | Sell 20-delta, buy 5-delta, weekly and held. Deltas are from memory of Cboe's method, not read from it |
| `index_style_butterfly` | Cboe BFLY, same study | Sell the centre, buy 5-delta, weekly and held. Same caveat |
| `near_strangle` | Santa-Clara and Saretto | Sell 10-delta strangle a week out and hold. Naked |
| `delta_hedged_straddle` | [Volatility premium research](https://quantpedia.com/overview-of-different-short-volatility-strategies/) | Sell a weekly straddle and rebalance whole-lot futures near each close. Parity forward stands in for the future. Naked |
| `weekend_straddle` | [Jones and Shemesh](https://ideas.repec.org/a/bla/jfinan/v73y2018i2p861-900.html) | Sell before a gap of three or more days, buy back after the next open. Sources report little effect in S&P 500 index options. Naked |
| `zero_day_condor` | [US 0DTE form](https://optionstradingiq.com/?p=28544) | On expiry day sell 14-delta with wings three strikes out, 30% target, out before 11:00 |
| `straddle_momentum` | [Heston and others](https://www.cxoadvisory.com/?p=965), index form | Buy a held weekly straddle only if the last four finished ones averaged a profit. The paper is cross-sectional stock options |
| `volatility_gap_straddle` | Goyal and Saretto, index form | Buy a held weekly straddle only when implied is below realized. The paper is cross-sectional stock options |
| `tail_hedge_puts` | [AQR and Universa debate](https://www.aqr.com/insights/research/white-papers/tail-risk-hedging-contrasting-put-and-trend-strategies) | Buy a 5-delta put each week and hold. As a hedge it could be worth holding without profit; this tests it alone |

**Tests:** one lot of 65, dated charges, and half-spreads from our recorded quotes by premium band, at the band median, because a flat 0.25% flatters cheap options. Those bands come from private data and stay local. Advancing and holdout tests are the same as the published rules study. A rule that passes is rerun at the band's upper quartile.

**Not testable on our data:** stock-level strategies (cross-sectional straddles, option momentum as published, dispersion), anything longer than about 10 days to expiry, covered calls on the index, and term structure trades.

### Global strategies outcome, 1 November 2026

**1 of 11 advanced: `far_put_write`. It then passed every holdout test once, and stayed positive at the upper-quartile spread. Across all three studies, 1 of 42 rules passed. It is a candidate, not a validated strategy, and paper trading cannot arm it as written.**

**The holdout is now spent.** Expiries from 1 November 2025 have been read for this rule, so no later rule can claim them as untouched. The earlier statements that the holdout stayed unread describe the time of those studies.

Why a pass here is weak evidence about risk:

- It sells a naked put, so its loss has no cap. A single large fall while a trade is open would exceed the ₹20,000 per-trade limit and take back about two years of gains.
- Neither half contained that fall during a held trade. A high t reads the wins well and says little about a crash that did not happen in the sample.
- The study's data starts in September 2024, so it misses earlier large falls.
- The two halves are different trades. Thursday expiries were mostly held about three days, while Tuesday expiries are held about one day and no weekend.
- Strikes outside the imported range were skipped. On development expiries the skipped weeks were not the worst ones, but about a quarter of weeks in the sample have no trade.
- Premiums are small, so real bid/ask matters more than percentage bands show.
- The Cboe and academic results that motivated this family were measured on other markets, mostly before costs.

The other ten did not advance. Weekly at-the-money put writing, index-style condors and butterflies, delta hedging, the weekend effect, 0DTE condors, straddle momentum and the volatility gap all failed in our form. Far puts bought as a hedge lost every time, as a hedge is expected to; that does not test their value as protection.

Numbers are in `work/bt/global-*.json`, private and local.
