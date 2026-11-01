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
- The first import's strikes were centred on NIFTY about a week before expiry, so after a large move one side of the ladder could be missing. Rules that needed those strikes skipped the week. The rich premium study below adds a private wider import and reruns the earlier verdicts against it.

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

## Rich premium study, fixed on 1 November 2026 before the later half was read for these rules

The owner asked for a strategy with a better win rate and profit, found from the historical data itself. This is a fourth study, and the whole search now totals 45 rules. Rules are in `packages/backtesting/rich_premium.py`.

**How the rules were found.** Only expiries before 1 November 2025 were read for them. About 5,200 relationships between market readings and later results were checked and most were noise. About 8,100 hedged settings were then run across strikes, entry times, exits, stops and targets, each with several thresholds. That is a wide search, so development results overstate what to expect, and the shorter-horizon rule below does not clear a family-wise luck check on its own.

**A data problem found on the way.** The first import took strikes within about 3% of NIFTY about a week before expiry. After a large move one side of the ladder was missing, so far-strike rules skipped those weeks and the nearest listed strike could sit far from the market. This study adds a second private import, `work/history-wide.db`, with strikes within 5% of the lowest and highest NIFTY level seen in each expiry's window. Earlier studies ran before it existed.

**The idea.** At 09:20 compare the move the at-the-money straddle implies until expiry with a forecast of the move until expiry. The forecast blends the last session, week and month of squared 5-minute parity changes and overnight gaps, and each weekend or holiday before expiry counts as one more session. A reading of 1 means the straddle charges about what recent movement predicts. Unfiltered hedged trades came to about nothing on development expiries. Days with a reading of at least 1 made money and days below it lost, so the reading sorts days rather than adding a new trade.

| Rule | Reading | Sold strikes | Bought wings | Exit |
| --- | --- | --- | --- | --- |
| `rich_condor_day` | at least 1.0 | 30-delta call and put | 300 points beyond each | Same day |
| `rich_condor_next_close` | at least 1.0 | 30-delta call and put | 300 points beyond each | Next session's close, or the same day when the next session is expiry day |
| `very_rich_condor_next_close` | at least 1.1 | 20-delta call and put | 300 points beyond each | As above |

All three decide at 09:20 from bars closed by then, skip the session before a weekend or holiday unless it is expiry day, and use one lot of 65 with no stop or target. Exits are the 15:10 bar, or the 15:05 bar on expiry day. A bought wing with no bar on the exit day counts as worthless and a sold leg keeps its last price, so a quiet contract cannot flatter a result.

**Choices made from development results, and why:**

- Wings of 300 points are the widest round width that keeps one lot's maximum loss under the ₹20,000 per-trade limit at any credit. Wider wings scored a little better but would be refused on some days.
- No profit target. Small targets showed win rates near 100%, and those vanished when the threshold or target changed slightly. A tiny target hides the tail rather than removing it.
- Not held overnight into expiry day. A 17-year model and the development tail both pointed the same way, and leaving a session earlier about halved the development drawdown.

**Tests:** one lot of 65, dated charges, recorded spread bands at the median, as in the global study. The later half is expiries from 1 November 2025. It runs once for these three rules, on the same five tests: positive average after charges, at least 15 trades, t of at least 2.45, worst trade within ₹20,000 and drawdown within ₹60,000. A rule that passes is rerun at the band's upper quartile and with entry one bar later, and must stay positive in both.

**The later half is reused, not pristine.** It was read for `far_put_write`, and market statistics for those months were seen while researching. Results for these rules on it have not been read. A pass is evidence, not proof.

**Long-run check.** Weekly option prices do not exist before the sample, so a model prices a hypothetical weekly from India VIX back to 2009, at today's index level. It checks whether rich-looking days paid in other eras and how bad the bad days got. It assumes option prices relative to VIX were always as they are now, and its daily-candle reading only partly agrees with the real one, so it is a stress test and not a backtest.

**Forward test.** These rules are frozen by this commit, so sessions from 2 November 2026 are the first data nobody has seen. They are not added to the 26 frozen paper templates, and the forward period does not change. Whether any of them ever moves into paper trading is the owner's decision after it ends.

**Limits of this study:**

- The sample has no one-session fall of 2.5% or more from open to close, which 22 years of NIFTY say is unusually calm. Wings cap the loss on each trade, but several capped losses in a row are possible.
- Much of the profit comes from selling at 09:20, because rich straddles keep falling through the morning. Later entries earned clearly less, so real fills at that minute matter, and only live quotes can show them.
- Spread bands come from a single recorded day.
- Weekly expiry moved from Thursday to Tuesday between the two halves, so the later half trades different days of the week before expiry. Sessions four days out barely appear in development.
- Next-close rules can hold two trades at once. Results are per trade.
- Simulated results, not a forecast, and not investment advice.

### Rich premium outcome, 1 November 2026

**0 of 3 rules passed the later half. Across all four studies, 1 of 45 rules has passed, and it is naked.** `rich_condor_day` and `rich_condor_next_close` lost a little after charges and won barely more than half the time, far from their development results. `very_rich_condor_next_close` stayed positive but did not clear the chance test. None passed, so none is rerun at wider spreads or a later entry. The later half is now spent for this study as well.

What the later half showed:

- Hedged selling on an ordinary session lost money after charges, so the whole level was lower than in the earlier half.
- The reading still sorted days the same way, and results rose with it in both halves. A reading of 1 was too low to pay for costs, and readings just above 1 earned nothing in either half in the one-night form.
- Same-day results did not carry over. Across every setting, a same-day exit's earlier result said nothing about its later one. Only about a fifth of one-night settings were positive in the later half, and those sit at high readings.
- Most profit in held trades arrived on the following session, not the entry day. That is why the same-day form failed and the one-night form at high readings did not.
- The last change before the run made things worse. Leaving before expiry day was chosen from the long-run model and the development tail, and on the later half it removed the best trades. The third rule in its earlier form, held into expiry day, would have cleared the tests. That does not count, because it is known only from reading the result.

**What the far strikes changed in earlier studies.** Rerunning the 22 published and global rules on development expiries with far strikes changed no verdict. `far_put_write` gained the weeks it had skipped without getting worse, on both halves, and it is still naked. The 9:20 straddle's worst day, 17 April 2025, was overstated because the nearest listed strike sat far from the market, but it stays past the per-trade limit. Hedged rules got more trades and slightly worse averages.

### Forward-only rules, fixed on 1 November 2026

Two rules sit in `night_rules()` in `packages/backtesting/rich_premium.py`. **They were chosen after reading both halves, so history cannot validate them.** Sessions from 2 November 2026 are the only real test.

| Rule | Reading | Sold strikes | Bought wings | Decision and entry |
| --- | --- | --- | --- | --- |
| `very_rich_night_condor` | at least 1.1 | 15-delta call and put | 300 points beyond each | 09:30 |
| `very_rich_night_condor_920` | at least 1.1 | 20-delta call and put | 300 points beyond each | 09:20 |

Both exit at the next session's close, including when that is expiry day, or the same day when the entry day is expiry day. Both skip the session before a weekend or holiday unless it is expiry day, and use one lot of 65 with no stop or target.

**For them:** positive with high win rates in both halves, with nearby settings positive too. Upper-quartile spreads and entries a few minutes late changed little. A luck check across both halves, using the lower of the two t values over about 48,000 settings, puts the 15-delta one-night form above its 95% line. The 20-delta 09:20 form does not reach that line on its own.

**Against them:**

- They are in sample by construction, and the 09:30 form was picked with the later half in view.
- The sample has no large fall, and the worst loss so far is well short of the most a trade can lose.
- The 17-year model is much weaker. Picking rich days clearly beat trading every day, but its average per trade sits well below the sample, its win rates are lower, it has several losing years and its reading only partly agrees with the real one.
- At about 30 trades a year, the six-week forward period will see too few to decide. About six months is the least that could tell.

They are not in the 26 frozen paper templates and do not change the forward period. They are scored afterwards with `--study night --forward`. Whether either moves into paper trading is the owner's decision.

Numbers are in `work/bt/rich-*.json` and `work/explore/`, private and local.

## Older years test, fixed on 1 November 2026 before any 2019 to 2024 price was read

`very_rich_night_condor` was picked after both recorded halves were read, and neither half holds a large fall. The owner approved downloading NSE's public daily F&O files to test it on years that do. These files give each contract's open and close only, so this is a rougher form of the rule, in `packages/backtesting/daily_condor.py`. It is not the rule itself, and no result here can validate it: only sessions from 2 November 2026 can do that.

**Data.** About 1,400 daily files from `archives.nseindia.com`, sessions 1 January 2019 to 30 September 2024, fetched by `scripts/import-nse-daily.py` into `work/nse-fo/`. They stay private and out of git. Only the two nearest NIFTY expiries are kept each day, and a contract with no trades that day has no price. Sessions whose NIFTY option contract count falls below 15% of the median of the previous 22 counted sessions are special sessions and do not count. Trades start on 11 February 2019, when weekly options began; earlier files only warm up the forecast.

**The daily form.**
- Reading: the opening straddle of the nearest expiry against the same blended forecast, with each session's variance now the squared overnight gap plus the Garman-Klass range from the index's daily prices.
- Cut: at least 1.20, the reading that picks the same number of days on the recorded sessions as the intraday rule's 1.1. It was set by that count and not by results. On those sessions the daily reading tracks the intraday one only moderately and about half the chosen days are shared, so this tests a close relative of the rule.
- Skips: the session before a weekend or holiday unless it is expiry day.
- Trade: at the open sell the 15-delta call and put, with the same strike tolerance, and buy wings beyond them. Exit at the next session's close, or the same session's close on expiry day.
- Size: the index was far lower in these years, so the wing is the same share of the index as 300 points at 24,500, to the nearest 50 points, one step wider or narrower if that strike did not trade. Units are set so a full wing loss costs what 300 points cost on one lot of 65.
- Exit marks: a bought leg with no trade that day counts as worthless. A sold leg with no trade keeps its last price, and a sold leg never marks below its value against the index close.
- Costs: today's charges and the recorded median half-spreads by premium band, applied to the actual premium. Charges and spreads were both different in these years; spreads were probably wider, which is why doubled spreads are reported.

**What counts.** One run, for the primary form above: positive average, at least 15 trades, t of at least 2.45, worst trade no worse than ₹20,000, drawdown no more than ₹60,000. Each of these is reported beside it and none can rescue it: doubled spreads, 20-delta, fixed 300-point wings on 65 units, the top 14% of readings by trailing rank, and every usable day with no reading at all. Each year and the crash window of 20 February to 30 April 2020 are reported for the primary form and for every day.

**What a result means.** A pass says the rough form survived years the recorded data lacks; the rule remains unproven until new sessions test it. A failure lowers the expectation for the rule and is recorded as such. Either way the rule stays forward-only, and paper trading and the frozen forward period do not change. No setting changes after the run; a different setting would be another picked-after-seeing rule.

## Older years outcome, 1 November 2026

The primary form failed its one run: the average was close to zero and t was far below 2.45, though the worst trade and drawdown stayed inside their limits. Nothing reported beside it passed either, and the frozen protocol does not let them rescue it. All figures stay in private `work/`.

- **The reading did not help.** Trading every usable day did better on average than trading only the chosen days, in the opposite order to the recorded sessions. Every day still failed: its t, its drawdown and its worst trade all missed their limits. That result was not a test of anything and is not a new candidate.
- **Crashes were not what hurt.** In the 2020 crash window the reading chose few days, because recent movement was already high and the forecast rose with it. The damage came in 2022 and 2023, ordinary years for the index.
- **What this changes.** The expectation for `very_rich_night_condor` falls: its recorded results may belong to that period. This was a rougher form sharing about half its days with the rule, so it does not disprove the intraday rule. It stays forward-only and unproven, and paper trading is unchanged. No setting will change because of this run.

Data notes: 30 March 2021 is not on the archive and was left out. The count rule dropped five special sessions and left two Muhurat sessions in the calendar (24 October 2022 and 12 November 2023, if listed); no primary trade fell on them.
