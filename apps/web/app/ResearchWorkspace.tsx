"use client";

import { useEffect, useState } from "react";

type Candidate = {id: string; definition: {name: string; market: string; family: string; hypothesis: string; entry_rule: string; exit_rule: string; limitation: string; source_license: string}; source_review: string};
type Study = {id: string; study_key: string; manifest: {objective: string; incumbent_rule_version: string; parent_search_budget: number; holdout_start: string; holdout_end: string; cost_model_id: string; capital?: string; review_interval_days?: number}; manifest_digest: string; created_at: string; promotion_eligible: false; execution_enabled: false};
type Recommendation = {id: string; decision: string; reason_code: string; snapshot: {selector_version?: string; rejected_reasons?: string[]; risk_context?: Record<string, unknown>; evidence_snapshot?: {kind?: string; chain_source?: string; chain_provider_id?: string | null; chain_received_at?: string | null; bar_received_at?: string | null; bar_provider_id?: string | null; chain_timestamp_basis?: string; chain_artifact_review?: string; bar_artifact_review?: string; cost_artifact_review?: string; point_in_time_availability_review?: string; realized_volatility_annualized_percent?: string | null; prior_20_bar_high?: string | null; prior_20_bar_low?: string | null; technical_level_status?: string}}; snapshot_digest: string; expires_at: string; created_at: string; promotion_eligible: false; execution_enabled: false};
type Metrics = {closed_trades: number; winning_trades: number; losing_trades: number; breakeven_trades: number; net_pnl: number; win_rate: number | null};
type EquityReport = {status: string; metrics: {training: Metrics; validation: Metrics}; cost_model: {id: string; fee_fraction: string; slippage_fraction: string}; boundary_or_gap_trades_excluded: number; open_trade_excluded: boolean; holdout_evaluated: boolean; limitations: string[]};
type NiftyReplayReport = {status: string; study_key: string; study_manifest_digest: string; strategy_version: string; replay_input_digest: string; trade_count: number; holdout_evaluated: false; promotion_eligible: false; limitations: string[]};
type Report = EquityReport | NiftyReplayReport;
type TrialEvidence = {id: string; key: string; status: string; detail: string; artifact_ref: string; artifact_digest: string | null; artifact_version: string; recorded_at: string; received_at?: string; allowed_use: string};
type Trial = {id: string; manifest_digest: string; manifest: {experiment_family: string; trial_number: number; declared_search_budget: number; cost_model_id: string; rights_artifact_digest?: string | null; cost_artifact_digest?: string | null; train_start: string; train_end: string; validation_start: string; validation_end: string; holdout_start: string; holdout_end: string; holdout_artifact_digest?: string | null}; result: {outcome: string; reason_code: string} | null; candidate?: Candidate["definition"]; evidence?: TrialEvidence[]; engine_evaluation?: {result_digest: string | null; result: {outcome: string; reason_code?: string; evidence_origin: string; report?: Report} | null} | null};
const PAGE_SIZE = 25;
type Readiness = {schema_version: number; policy_version: string; assessed_at: string; status: string; checks: {key: string; label: string; status: string; detail: string; evidence_ref: string | null; evidence_recorded_at: string | null; evidence_received_at?: string | null; artifact_digest?: string | null; artifact_version?: string | null; allowed_use?: string | null}[]};
const money = (value: number) => Number.isFinite(value) ? new Intl.NumberFormat("en-IN", {style: "currency", currency: "INR", maximumFractionDigits: 2}).format(value) : "Unavailable";

async function readRows<T>(url: string, token: string, signal: AbortSignal): Promise<T[]> {
  const response = await fetch(url, {headers: {Authorization: `Bearer ${token}`}, cache: "no-store", signal});
  if (!response.ok) {
    if (response.status === 401) throw new Error("Your session expired. Sign in again to view research.");
    if (response.status === 404) throw new Error("Research is unavailable in this API build. The API needs the latest build and migrations.");
    throw new Error("Could not load research evidence. Check API availability and migrations, then retry.");
  }
  const rows: unknown = await response.json();
  if (!Array.isArray(rows)) throw new Error("The API returned an unsupported research response.");
  return rows as T[];
}

export function ResearchReadiness({readiness}: {readiness?: Readiness}) {
  if (!readiness || readiness.schema_version !== 1) return <p className="research-notice">Evidence readiness is unavailable in this API response. No qualification can be inferred.</p>;
  const failures = readiness.checks.filter(check => check.status === "FAIL");
  const holdout = readiness.checks.find(check => check.key === "holdout");
  const costs = readiness.checks.find(check => check.key === "costs");
  return <section className="research-readiness" aria-label="Data and evidence readiness">
    <strong>Evidence readiness · {failures.length ? "Blocked" : "Unverified"}</strong>
    <p>{failures.length ? failures[0].detail : "Required reviews are incomplete."} Research inspection only; no qualified recommendation.</p>
    <div className="research-readiness-summary"><span><b>Protected holdout</b><em className={`readiness-label readiness-${(holdout?.status ?? "UNKNOWN").toLowerCase()}`}>{holdout?.status ?? "UNKNOWN"}</em></span><small>{holdout?.artifact_digest ? "Artifact identity bound" : "Artifact identity not declared"}</small><span><b>Cost evidence</b><em className={`readiness-label readiness-${(costs?.status ?? "UNKNOWN").toLowerCase()}`}>{costs?.status ?? "UNKNOWN"}</em></span><small>{costs?.artifact_digest ? "Artifact identity bound" : "Artifact identity not declared"}</small></div>
    <details><summary>Review checks and missing evidence</summary>
      <p className="features-note">Assessment generated {new Date(readiness.assessed_at).toLocaleString("en-IN")}. Refreshing does not renew the evidence below. A matching record digest does not verify source data.</p>
      <ul>{readiness.checks.map(check => <li key={check.key}>
        <div><strong>{check.label}</strong><span className={`readiness-label readiness-${check.status.toLowerCase()}`}>{check.status.replaceAll("_", " ")}</span></div>
        <p>{check.detail}</p>
        {check.evidence_ref ? <small className="research-digest">{check.evidence_ref}<br />Artifact {check.artifact_version ?? "version unavailable"} · Use {check.allowed_use ?? "unspecified"}<br />Declared review time {check.evidence_recorded_at ? new Date(check.evidence_recorded_at).toLocaleString("en-IN") : "unavailable"} · Server received {check.evidence_received_at ? new Date(check.evidence_received_at).toLocaleString("en-IN") : "unavailable"}</small> : <small>No recorded assessment evidence</small>}
      </li>)}</ul>
      <small className="research-digest">Assessment policy: {readiness.policy_version}</small>
    </details>
  </section>;
}

export function ResearchTrialCard({trial}: {trial: Trial & {readiness?: Readiness}}) {
  const evaluation = trial.engine_evaluation;
  const result = evaluation?.result;
  const report = result?.report;
  const state = result?.outcome === "COMPLETED" ? (report?.status === "INSUFFICIENT_SAMPLE" ? "Insufficient sample" : "Replay complete · Unqualified") : result ? "Evaluation failed" : evaluation ? "Awaiting evaluation" : "Not evaluated";
  return <article className="research-trial">
    <div className="workspace-section-heading"><h3>{trial.candidate ? `${trial.candidate.name} · ` : ""}Trial {trial.manifest.trial_number}</h3><span className="research-status">{state}</span></div>
    <p className="features-note">{trial.manifest.experiment_family} · Declared trial {trial.manifest.trial_number} of {trial.manifest.declared_search_budget}</p>
    <ResearchReadiness readiness={trial.readiness} />
    {trial.engine_evaluation === undefined ? <p className="paper-warning">This API does not expose engine evidence yet. An updated API is required.</p> : null}
    {result?.reason_code ? <p className="paper-warning">Reason: {result.reason_code.replaceAll("_", " ").toLowerCase()}. This attempt remains in the research history.</p> : null}
    {report && "metrics" in report ? <>
      <div className="research-metrics">{(["training", "validation"] as const).map(window => <div key={window}>
        <span>{window === "training" ? "Training" : "Validation"} · {report.metrics[window].closed_trades} closes</span>
        <strong>{money(report.metrics[window].net_pnl)}</strong>
        <small>{report.metrics[window].winning_trades} wins · {report.metrics[window].losing_trades} losses · {report.metrics[window].breakeven_trades} flat · illustrative costs</small>
      </div>)}</div>
      <p className="features-note">{report.status === "INSUFFICIENT_SAMPLE" ? "At least 20 closed trades in each window are required for sample review." : "Sample size alone does not establish an edge."} Profit probability is unavailable.</p>
    </> : report ? <>
      <div className="research-notice"><strong>NIFTY scheduled replay · {report.trade_count} trades · {report.status.replaceAll("_", " ")}</strong><p>Strategy {report.strategy_version} · holdout evaluated: No · promotion eligible: No.</p><p>Replay evidence is recorded for review only; it is not a calibrated profit probability or a deployment recommendation.</p></div>
    </> : <p className="workspace-empty">{evaluation ? "A fixed-rule binding is saved. No completed replay evidence is available." : "No engine evaluation has been recorded for this trial."}</p>}
    <details className="workspace-disclosure"><summary>Windows, costs &amp; evidence</summary>
      <dl className="research-facts"><dt>Training</dt><dd>{trial.manifest.train_start} → {trial.manifest.train_end}</dd><dt>Validation</dt><dd>{trial.manifest.validation_start} → {trial.manifest.validation_end}</dd><dt>Reserved holdout</dt><dd>{trial.manifest.holdout_start} → {trial.manifest.holdout_end}</dd><dt>Rights artifact</dt><dd>{trial.manifest.rights_artifact_digest ? `${trial.manifest.rights_artifact_digest.slice(0, 12)}…` : "Not declared · remains unverified"}</dd><dt>Holdout artifact</dt><dd>{trial.manifest.holdout_artifact_digest ? `${trial.manifest.holdout_artifact_digest.slice(0, 12)}…` : "Not declared · remains unverified"}</dd><dt>Cost model</dt><dd>{trial.manifest.cost_model_id}</dd><dt>Cost artifact</dt><dd>{trial.manifest.cost_artifact_digest ? `${trial.manifest.cost_artifact_digest.slice(0, 12)}…` : "Not declared · remains unverified"}</dd><dt>Engine evidence</dt><dd>{result?.evidence_origin ?? "Not recorded"}</dd><dt>Operator evidence</dt><dd>{trial.result ? `${trial.result.outcome} · ${trial.result.reason_code}` : "Not recorded"}</dd></dl>
      {report && "metrics" in report ? <><p className="features-note">Holdout evaluated: {report.holdout_evaluated ? "Yes — requires review" : "No"}. Boundary/gap trades excluded: {report.boundary_or_gap_trades_excluded}. Open trade excluded: {report.open_trade_excluded ? "Yes" : "No"}.</p><p className="features-note">Illustrative fee {report.cost_model.fee_fraction}, slippage {report.cost_model.slippage_fraction} (fractions per engine policy; not a complete broker charge schedule).</p><ul>{report.limitations.map(note => <li key={note}>{note}</li>)}</ul></> : report ? <><p className="features-note">Study {report.study_key} · manifest {report.study_manifest_digest} · replay input {report.replay_input_digest}</p><ul>{report.limitations.map(note => <li key={note}>{note}</li>)}</ul></> : null}
      <p className="features-note">Operator records and engine replay evidence are separate. Source, data quality and the hypothesis-to-rule binding remain unreviewed. No automatic promotion.</p>
      {trial.evidence?.length ? <><h4>Reviewed evidence references</h4><ul>{trial.evidence.map(item => <li key={item.id}><strong>{item.key}</strong> · {item.status} · {item.artifact_version} · {item.allowed_use}<br /><small>{item.detail} · Declared review {new Date(item.recorded_at).toLocaleString("en-IN")} · Server received {item.received_at ? new Date(item.received_at).toLocaleString("en-IN") : "unavailable"}</small></li>)}</ul></> : null}
      <small className="research-digest">Manifest: {trial.manifest_digest}<br />Engine result: {evaluation?.result_digest ?? "Not recorded"}</small>
    </details>
  </article>;
}

export function ResearchWorkspace({apiUrl, token}: {apiUrl: string; token: string}) {
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [candidateOffset, setCandidateOffset] = useState(0);
  const [selected, setSelected] = useState("");
  const [trials, setTrials] = useState<Trial[]>([]);
  const [trialOffset, setTrialOffset] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [busy, setBusy] = useState(true);
  const [trialBusy, setTrialBusy] = useState(false);
  const [error, setError] = useState("");
  const [trialError, setTrialError] = useState("");
  const [compareAll, setCompareAll] = useState(false);
  const [allTrials, setAllTrials] = useState<Trial[]>([]);
  const [allTrialOffset, setAllTrialOffset] = useState(0);
  const [allTrialBusy, setAllTrialBusy] = useState(false);
  const [allTrialError, setAllTrialError] = useState("");
  const [studies, setStudies] = useState<Study[]>([]);
  const [selectedStudy, setSelectedStudy] = useState("");
  const [studyBusy, setStudyBusy] = useState(false);
  const [studyError, setStudyError] = useState("");
  const [recommendations, setRecommendations] = useState<Recommendation[]>([]);
  const [recommendationBusy, setRecommendationBusy] = useState(false);
  const [recommendationError, setRecommendationError] = useState("");
  const [abstentionBusy, setAbstentionBusy] = useState(false);
  const [abstentionMessage, setAbstentionMessage] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    setBusy(true); setError(""); setCandidates([]); setSelected(""); setTrials([]); setTrialOffset(0); setCompareAll(false); setAllTrials([]); setAllTrialOffset(0);
    setStudyBusy(true); setStudyError(""); setStudies([]); setSelectedStudy(""); setRecommendations([]); setRecommendationError("");
    readRows<Candidate>(`${apiUrl}/research/candidates?limit=${PAGE_SIZE}&offset=${candidateOffset}`, token, controller.signal)
      .then(rows => { if (!controller.signal.aborted) { setCandidates(rows); setSelected(rows[0]?.id ?? ""); } })
      .catch((failure: unknown) => { if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : "Research could not be loaded. Check the API and retry."); })
      .finally(() => { if (!controller.signal.aborted) setBusy(false); });
    readRows<Study>(`${apiUrl}/research/studies?limit=${PAGE_SIZE}&offset=0`, token, controller.signal)
      .then(rows => { if (!controller.signal.aborted) { setStudies(rows); setSelectedStudy(rows[0]?.id ?? ""); } })
      .catch((failure: unknown) => { if (!controller.signal.aborted) setStudyError(failure instanceof Error ? failure.message : "Frozen studies could not be loaded."); })
      .finally(() => { if (!controller.signal.aborted) setStudyBusy(false); });
    return () => controller.abort();
  }, [apiUrl, token, candidateOffset, refresh]);

  useEffect(() => {
    setRecommendations([]); setRecommendationError("");
    if (!selectedStudy) { setRecommendationBusy(false); return; }
    const controller = new AbortController();
    setRecommendationBusy(true);
    readRows<Recommendation>(`${apiUrl}/research/studies/${encodeURIComponent(selectedStudy)}/recommendations?limit=${PAGE_SIZE}&offset=0`, token, controller.signal)
      .then(rows => { if (!controller.signal.aborted) setRecommendations(rows); })
      .catch((failure: unknown) => { if (!controller.signal.aborted) setRecommendationError(failure instanceof Error ? failure.message : "Recommendations could not be loaded."); })
      .finally(() => { if (!controller.signal.aborted) setRecommendationBusy(false); });
    return () => controller.abort();
  }, [apiUrl, token, selectedStudy, refresh]);

  useEffect(() => {
    if (!compareAll) return;
    const controller = new AbortController();
    setAllTrialBusy(true); setAllTrialError("");
    readRows<Trial>(`${apiUrl}/research/trials?limit=${PAGE_SIZE}&offset=${allTrialOffset}`, token, controller.signal)
      .then(rows => { if (!controller.signal.aborted) setAllTrials(rows); })
      .catch((failure: unknown) => { if (!controller.signal.aborted) setAllTrialError(failure instanceof Error ? failure.message : "All trials could not be loaded. Check the API and retry."); })
      .finally(() => { if (!controller.signal.aborted) setAllTrialBusy(false); });
    return () => controller.abort();
  }, [apiUrl, token, allTrialOffset, compareAll]);

  useEffect(() => {
    setTrials([]); setTrialError("");
    if (!selected) { setTrialBusy(false); return; }
    const controller = new AbortController();
    setTrialBusy(true);
    readRows<Trial>(`${apiUrl}/research/candidates/${encodeURIComponent(selected)}/trials?limit=${PAGE_SIZE}&offset=${trialOffset}`, token, controller.signal)
      .then(rows => { if (!controller.signal.aborted) setTrials(rows); })
      .catch((failure: unknown) => { if (!controller.signal.aborted) setTrialError(failure instanceof Error ? failure.message : "Trial evidence could not be loaded. Check the API and retry."); })
      .finally(() => { if (!controller.signal.aborted) setTrialBusy(false); });
    return () => controller.abort();
  }, [apiUrl, token, selected, trialOffset]);
  const candidate = candidates.find(row => row.id === selected);
  const study = studies.find(row => row.id === selectedStudy);
  async function recordAbstention() {
    if (!study || abstentionBusy) return;
    setAbstentionBusy(true); setAbstentionMessage(""); setRecommendationError("");
    const reviewDays = Math.max(1, study.manifest.review_interval_days ?? 1);
    const expiresAt = new Date(Date.now() + reviewDays * 24 * 60 * 60 * 1000).toISOString();
    try {
      const response = await fetch(`${apiUrl}/research/studies/${encodeURIComponent(study.id)}/recommendations`, {
        method: "POST",
        headers: {Authorization: `Bearer ${token}`, "Content-Type": "application/json"},
        body: JSON.stringify({expires_at: expiresAt}),
      });
      if (!response.ok) throw new Error(response.status === 401 ? "Your session expired. Sign in again." : "The abstention could not be recorded.");
      setAbstentionMessage("Manual, unreviewed abstention recorded. No strategy was selected and no order was created.");
      setRefresh(value => value + 1);
    } catch (failure: unknown) {
      setRecommendationError(failure instanceof Error ? failure.message : "The abstention could not be recorded.");
    } finally {
      setAbstentionBusy(false);
    }
  }
  return <section className="paper-workspace app-section-page research-workspace" aria-label="Strategy research">
    <div className="workspace-section-heading"><div><span className="panel-kicker">RESEARCH · READ ONLY</span><h2>Strategy research</h2></div><div><button className="refresh-button" disabled={busy || trialBusy || allTrialBusy || studyBusy || recommendationBusy} onClick={() => setRefresh(value => value + 1)} type="button">Refresh evidence</button> <button className="refresh-button" disabled={busy || trialBusy || allTrialBusy || studyBusy || recommendationBusy} onClick={() => { setCompareAll(value => !value); setAllTrialOffset(0); }} type="button">{compareAll ? "Selected hypothesis" : "Compare all trials"}</button></div></div>
    <p>Review hypotheses and chronological replay results before considering a strategy. Research results stay separate from your paper portfolio.</p>
    <div className="research-notice">Recommendations are not qualified yet. Profit probability is unavailable. Automatic promotion is disabled.</div>
    {studyBusy ? <p role="status">Loading frozen study…</p> : studyError ? <p role="alert" className="error-banner">{studyError}</p> : study ? <section className="research-trial" aria-label="Frozen parent study and recommendations">
      <div className="workspace-section-heading"><h3>Frozen NIFTY parent study</h3><span className="research-status">Research only</span></div>
      {studies.length > 1 ? <label className="research-picker">Study<select value={selectedStudy} onChange={event => setSelectedStudy(event.target.value)}>{studies.map(row => <option key={row.id} value={row.id}>{row.study_key}</option>)}</select></label> : <p className="features-note">{study.study_key}</p>}
      <p>{study.manifest.objective}</p>
      <dl className="research-facts"><dt>Incumbent</dt><dd>{study.manifest.incumbent_rule_version}</dd><dt>Search budget</dt><dd>{study.manifest.parent_search_budget} declared trials</dd><dt>Protected holdout</dt><dd>{study.manifest.holdout_start} → {study.manifest.holdout_end}</dd><dt>Cost model</dt><dd>{study.manifest.cost_model_id}</dd></dl>
      <p className="features-note">Study identity {study.manifest_digest} · execution disabled · promotion ineligible.</p>
      <div className="research-action-row"><button className="refresh-button" disabled={abstentionBusy || recommendationBusy || studyBusy} onClick={recordAbstention} type="button">{abstentionBusy ? "Recording abstention…" : "Record manual abstention"}</button><small>Unreviewed research record only; this never assesses evidence, selects a strategy or creates an order.</small></div>
      {abstentionMessage ? <p className="research-notice" role="status">{abstentionMessage}</p> : null}
      {recommendationBusy ? <p role="status">Loading recommendation snapshots…</p> : recommendationError ? <p role="alert" className="error-banner">{recommendationError}</p> : recommendations.length ? <><h4>Latest recommendation snapshots</h4><ul>{recommendations.map(item => <li key={item.id}><strong>{item.decision}</strong> · {item.reason_code.replaceAll("_", " ")} · expires {new Date(item.expires_at).toLocaleDateString("en-IN")}<br /><small>Selector {item.snapshot.selector_version ?? "unknown"} · {item.snapshot.rejected_reasons?.join("; ") ?? "No rejection detail recorded."}</small>{item.snapshot.evidence_snapshot?.kind === "NIFTY_MARKET_FIT_OPPORTUNITY_V1" ? <details><summary>Opportunity source and review limits</summary><p>Chain: {item.snapshot.evidence_snapshot.chain_source ?? "unavailable"} · provider {item.snapshot.evidence_snapshot.chain_provider_id ?? "unavailable"} · timestamp basis {item.snapshot.evidence_snapshot.chain_timestamp_basis ?? "unavailable"} · server received {item.snapshot.evidence_snapshot.chain_received_at ?? "unavailable"}. Historical bars: provider {item.snapshot.evidence_snapshot.bar_provider_id ?? "unavailable"} · received {item.snapshot.evidence_snapshot.bar_received_at ?? "unavailable"}. Chain review: {item.snapshot.evidence_snapshot.chain_artifact_review ?? "unavailable"}; bars: {item.snapshot.evidence_snapshot.bar_artifact_review ?? "unavailable"}; costs: {item.snapshot.evidence_snapshot.cost_artifact_review ?? "unavailable"}; point-in-time availability: {item.snapshot.evidence_snapshot.point_in_time_availability_review ?? "unavailable"}. Derived completed-bar context: annualized realized volatility (percent) {item.snapshot.evidence_snapshot.realized_volatility_annualized_percent ?? "unavailable"}; previous 20-bar low {item.snapshot.evidence_snapshot.prior_20_bar_low ?? "unavailable"} and high {item.snapshot.evidence_snapshot.prior_20_bar_high ?? "unavailable"} ({item.snapshot.evidence_snapshot.technical_level_status ?? "unavailable"}). These levels are not validated support/resistance. This is research-only abstention, not a qualified prediction or paper entry.</p></details> : null}</li>)}</ul></> : <p className="workspace-empty">No recommendation snapshots are recorded. The system abstains until reviewed evidence is sufficient.</p>}
    </section> : null}
    {error ? <p role="alert" className="error-banner">{error}</p> : null}
    {busy ? <p role="status">Loading research…</p> : !error && !candidates.length ? <p className="workspace-empty">{candidateOffset ? "No more hypotheses on this page." : "No research hypotheses are registered for this account. Your paper positions are unchanged; synthetic demonstrations are kept in a disposable database."}</p> : null}
    {compareAll ? <><h3>All recorded trials</h3>{allTrialBusy ? <p role="status">Loading all trials…</p> : allTrialError ? <p role="alert" className="error-banner">{allTrialError}</p> : !allTrials.length ? <p className="workspace-empty">No trials are recorded across this account. No performance or probability can be inferred.</p> : <div className="research-trials">{allTrials.map(trial => <ResearchTrialCard key={trial.id} trial={trial} />)}</div>}<div className="research-pagination"><button type="button" disabled={allTrialBusy || allTrialOffset === 0} onClick={() => setAllTrialOffset(value => Math.max(0, value - PAGE_SIZE))}>Previous trials</button><span>Page {allTrialOffset / PAGE_SIZE + 1}</span><button type="button" disabled={allTrialBusy || allTrials.length < PAGE_SIZE} onClick={() => setAllTrialOffset(value => value + PAGE_SIZE)}>Next trials</button></div></> : <>{candidates.length ? <label className="research-picker">Research hypothesis<select value={selected} onChange={event => {setTrials([]); setTrialOffset(0); setSelected(event.target.value);}}>{candidates.map(row => <option key={row.id} value={row.id}>{row.definition.name} · {row.definition.market}</option>)}</select></label> : null}
    <div className="research-pagination"><button type="button" disabled={busy || candidateOffset === 0} onClick={() => setCandidateOffset(value => Math.max(0, value - PAGE_SIZE))}>Previous hypotheses</button><span>Page {candidateOffset / PAGE_SIZE + 1}</span><button type="button" disabled={busy || candidates.length < PAGE_SIZE} onClick={() => setCandidateOffset(value => value + PAGE_SIZE)}>Next hypotheses</button></div>
    {candidate ? <><details className="workspace-disclosure"><summary>Hypothesis &amp; declared rules · Unreviewed</summary><p>{candidate.definition.hypothesis}</p><p><strong>Entry:</strong> {candidate.definition.entry_rule}</p><p><strong>Exit:</strong> {candidate.definition.exit_rule}</p><p>{candidate.definition.limitation}</p><p>Declared license: {candidate.definition.source_license}. Source review: {candidate.source_review}. Text is not executable strategy code.</p></details>
      <h3>Recorded trials</h3>{trialBusy ? <p role="status">Loading trials…</p> : trialError ? <p role="alert" className="error-banner">{trialError}</p> : !trials.length ? <p className="workspace-empty">No trials on this page. No performance or probability can be inferred.</p> : <div className="research-trials">{trials.map(trial => <ResearchTrialCard key={trial.id} trial={trial} />)}</div>}
      <div className="research-pagination"><button type="button" disabled={trialBusy || trialOffset === 0} onClick={() => setTrialOffset(value => Math.max(0, value - PAGE_SIZE))}>Previous trials</button><span>Page {trialOffset / PAGE_SIZE + 1}</span><button type="button" disabled={trialBusy || trials.length < PAGE_SIZE} onClick={() => setTrialOffset(value => value + PAGE_SIZE)}>Next trials</button></div>
    </> : null}</>}
  </section>;
}
