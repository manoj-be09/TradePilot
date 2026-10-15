# NIFTY 50 paper acceptance

Checkpoint: 15 October 2026. Scope is NIFTY 50 only; added-index execution and live broker orders remain disabled.

## Verified

- Owner rebuilt API and worker; readiness passed and the expiry-test capability route is present.
- Authenticated Edge shows six closed indicative trades and their twelve orders reconciled with zero cash difference before the expiry test.
- Explicitly authorized expiry test: one NIFTY 22,950 CE lot, contract expiry 19 October 2026, debit and modeled maximum loss ₹7,696. Saved name starts TEST ONLY. No automatic P&L stop, target, trailing stop or expiry square-off. Manual close remains available before expiry.
- After entry: thirteen orders, one open trade, six closed trades; cash ₹10,06,867.25 matched expected cash, difference zero.
- Latest browser observation retains the open test and hides quote-based marks when the NFO session is closed or unverified.
- 125 consolidated tests passed, covering paper entry/exit, worker restart, idempotency, settlement, bounded expiry-test policy, quote quality, reconnect/cache expiry, journal and feed-probe behavior. Batch-apply self-check passed separately.
- Isolated historical settlement checks used actual NSE archive data; the new full expiry-test lifecycle uses a mocked archive and deliberately synthetic historical expiry. Neither is actual held-through-expiry evidence.

## Remaining evidence

1. **Actual expiry:** retain the existing labeled position; do not fabricate its expiry, close it just to finish a checklist, or create replacements automatically. After its actual contract expiry, obtain the official dated NIFTY close, review corrections/final settlement terms, inspect the read-only cash preview, then record the reviewed settlement. Recheck cash, orders, repeat-request protection and worker behavior. A test entry is not a passed expiry gate.
2. **Strict bid/ask time:** provider receipt/tick time does not establish exchange-origin book-update time. Indicative paper mode remains WARN and excluded from qualified feedback. Require documented semantics and raw-message validation before calling strict freshness verified.
3. **Shared worker warning:** owner-provided diagnostic identified an equity data warning as the first of three warnings. This does not prove all warnings are equity-only. Keep each position's quote and exit evidence visible; do not change quote gates to clear the aggregate banner.

## Operator test flow

Open Future & Options → Strategies. Check the labeled test, contract identity and premium cap. Expand Paper ledger check and run Check ledger now; it must show zero difference. Inspect Exit checks for skipped/held observations. Outside the verified session, unavailable marks are expected. After expiry use the settlement section and retain the official archive fingerprint. Do not use the normal strategy performance numbers as profitability or strict execution validation.

This checkpoint supports owner-approved indicative paper testing. It is not a claim of complete strict-feed or actual-expiry acceptance, live execution, fees/slippage parity or profitability.
