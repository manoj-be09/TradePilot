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

## Known limits

- The holdout is not pristine. Whole-period results for the 26 templates, and the direction of the market in those years, were already seen before these rules were written.
- About 50 weekly trades per half at most, and fewer after filtering, in one provider's candles with no historical bid/ask.
- Exits are checked on 5-minute closes, so a stop can realize more than its amount.
- The straddle volatility reading is approximate, and the first expiries have no rank while the trailing window fills.
- Weekly expiry moved from Thursday to Tuesday during the period, which the rules do not model separately.
