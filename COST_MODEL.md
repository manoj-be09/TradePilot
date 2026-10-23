# F&O paper reporting charges

Model: `option-premium-turnover-illustrative-v1`.

This is a deliberately illustrative analytical model, not a current Upstox/NSE tariff or an estimate of complete broker charges. It applies 0.10% to the absolute filled premium turnover of each leg and rounds each leg to INR0.01 using ROUND_HALF_UP. A spread is charged on every leg; offsetting cash flows do not net the charge basis. Existing bid/ask fills already incorporate the spread; no extra spread/slippage is added.

New OPEN, manual CLOSE and automatic risk-rule CLOSE orders save the model ID, rate, currency, basis, rounding, leg inputs, turnover, charge and limitations inside the existing order fill snapshot, in the same database transaction as the order. Existing order snapshots are never backfilled. Idempotent entry/close replay retains its original saved order. Expiry intrinsic settlement records explicitly state charges unavailable: premium-turnover assumptions do not model settlement charges.

These analytical charges do not alter account cash, order cash flows, recorded realized P&L, sizing, stop/target/trailing decisions or historical strategy feedback. The report labels the adjustment separately as P&L after illustrative charges. It is not an all-in after-cost return.

The read-only trade-list response recomputes each known snapshot from its original fills and accepts only an exact match for this version. A closed trade requires exactly one executed OPEN and one executed CLOSE with valid cost records; missing, altered, unknown-version, legacy, incomplete and settlement records withhold adjusted P&L. Report groups with any closed trade lacking coverage withhold the whole adjusted group total rather than mixing before/after-charge outcomes. Open positions do not publish realized adjusted P&L.

Excluded: actual brokerage minima/caps, STT, GST, stamp duty, exchange and regulatory fees, exercise/settlement charges, market impact, additional slippage, financing and data subscriptions. Current official tariffs and instrument/date-specific tax bases must be verified before a separate broker-calibrated model is introduced. Preserve this v1 calculation unchanged when adding future versions; never relabel or recompute old evidence using new assumptions.

Validation for the illustrative order-snapshot model: pure unit arithmetic and file-backed disposable SQLite order-snapshot persistence/ledger preservation checks; frontend complete-only coverage checks; TypeScript, Ruff and mypy. API/worker end-to-end regression and deployed new-fill UI verification remain required on a host with functioning event-loop socketpair and Docker engine. No synthetic trades may be inserted into the owner's account for that verification.

## Public Upstox comparison scenario

`upstox-public-nfo-options-2026-10-scenario-v1` is a separate, read-only
quote/payoff comparison benchmark, reviewed on 2026-10-23 against
[Upstox's dated charge schedule](https://upstox.com/brokerage-charges/). It
uses standard-plan ₹20 brokerage per separately executed NIFTY option leg
order; NSE option transaction charges of 0.03553% of premium, effective
1 March 2026; sell-side option STT of 0.15% of premium, effective 1 April
2026; 0.003% buy-side stamp duty; ₹10/crore SEBI fee; and 18% GST on
brokerage and transaction charges. Charge components are rounded to paise
per assumed order. Upstox says IPFT is combined with transaction charges in
its current table. [Upstox Plus may charge ₹30 per option order](https://upstox.com/help-center/does-the-brokerage-plan-change-with-upstox-plus-264072/);
the owner's plan has not been verified.

The scenario estimates entry using caller-supplied premiums and a reverse
exit at the *same* premiums. It assumes one order per leg per side, and does
not model the actual exit premium, exercise/settlement, account discounts,
slippage, financing, market impact, or the broker's exact rounding. It is
shown apart from gross expiry payoff, never deducted from paper cash or
historical trades, and does not qualify a policy for after-cost ranking or
profit probability. This scenario must be revised for future tariff dates;
the existing illustrative order-snapshot model above remains unchanged.
