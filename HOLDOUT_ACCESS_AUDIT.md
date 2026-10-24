# Research holdout access audit — source paths

**Date:** 25 October 2026

**Scope:** repository code and isolated tests only. No owner artifact store, database permissions, provider entitlement, or independent validation account was inspected. This is a Day 13 **PARTIAL** source audit, not a holdout qualification.

| Path | What the code enforces | What remains unproved |
| --- | --- | --- |
| `packages/regime/frozen_nifty_study.py`, `candidate_registry.py` | Frozen chronological windows and optional `holdout_artifact_digest`; trial/study identity checks reject changed manifests. | A digest does not establish that the holdout artifact exists, is authentic, or is inaccessible to the evaluator. |
| `packages/regime/research_inputs.py` | When a distinct holdout digest is declared, daily evaluation input must end at validation; full input is rejected. Legacy manifests may provide all windows, then return only pre-holdout bars to the replay adapter. | The caller and process still possess legacy raw holdout bytes before filtering. A separate digest is not a storage or role boundary. |
| `packages/regime/fixed_rule_evaluator.py` | The equity replay engine receives only `ResearchWindows.reviewed`; its output says `holdout_evaluated=false` and cannot promote. | This is an offline equity adapter with illustrative costs, not independent NIFTY holdout access control. |
| `apps/api/research_routes.py`, `packages/regime/nifty_study_replay.py` | The authenticated NIFTY route loads an owned stored scheduled replay; its assessment rejects result dates outside training/validation and retains an unqualified result. | The route sees an already-produced summary, not raw replay inputs. It cannot prove that the producing job never read the protected holdout. The replay's `observed_input_digest` hashes observed legs and settings, so it is not interchangeable with the frozen dataset digest. |
| `packages/regime/research_readiness.py` | A recorded holdout PASS without a matching frozen holdout digest fails readiness. | Matching metadata and a review record do not prove separate credentials, storage policy, access logs, or a one-time independent evaluation. |
| `packages/regime/research_recommendations.py` | Prospective opportunity records must follow the frozen holdout end date. | This time boundary does not protect the holdout while policies are developed or selected. |

## Evidence required to close Day 13

- [ ] Review an actual holdout artifact, its rights, source dates, content digest and separation from training/validation artifacts.
- [ ] Give the training/replay role no holdout read permission; document a separate validation principal and its allowed one-time release procedure.
- [ ] Retain access logs that show authorized and denied reads, including attempts before release and after revocation.
- [ ] Prove with isolation tests that training, tuning, NIFTY policy replay and recommendation paths cannot fetch or receive holdout bytes before release.
- [ ] Record an independent validation result once under the frozen policy, cost model and canonical parent budget; prohibit repeated hidden tuning or automatic promotion.

Until those observations exist, holdout readiness remains **UNKNOWN/BLOCKED** for evidence-qualified ranking and probability claims. No actual owner holdout data, paper trade or source rights were changed by this audit.
