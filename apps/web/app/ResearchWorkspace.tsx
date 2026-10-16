"use client";

import { useEffect, useState } from "react";

type Candidate = {id: string; definition: {name: string; market: string; family: string; hypothesis: string; entry_rule: string; exit_rule: string; limitation: string; source_license: string}; source_review: string};
type Metrics = {closed_trades: number; winning_trades: number; losing_trades: number; breakeven_trades: number; net_pnl: number; win_rate: number | null};
type Report = {status: string; metrics: {training: Metrics; validation: Metrics}; cost_model: {id: string; fee_fraction: string; slippage_fraction: string}; boundary_or_gap_trades_excluded: number; open_trade_excluded: boolean; holdout_evaluated: boolean; limitations: string[]};
type Trial = {id: string; manifest_digest: string; manifest: {experiment_family: string; trial_number: number; declared_search_budget: number; cost_model_id: string; train_start: string; train_end: string; validation_start: string; validation_end: string; holdout_start: string; holdout_end: string}; result: {outcome: string; reason_code: string} | null; engine_evaluation?: {result_digest: string | null; result: {outcome: string; reason_code?: string; evidence_origin: string; report?: Report} | null} | null};
const PAGE_SIZE = 25;
type Readiness = {schema_version: number; policy_version: string; assessed_at: string; status: string; checks: {key: string; label: string; status: string; detail: string; evidence_ref: string | null; evidence_recorded_at: string | null}[]};
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
  return <section className="research-readiness" aria-label="Data and evidence readiness">
    <strong>Evidence readiness · {failures.length ? "Blocked" : "Unverified"}</strong>
    <p>{failures.length ? failures[0].detail : "Required reviews are incomplete."} Research inspection only; no qualified recommendation.</p>
    <details><summary>Review checks and missing evidence</summary>
      <p className="features-note">Assessment generated {new Date(readiness.assessed_at).toLocaleString("en-IN")}. Refreshing does not renew the evidence below. A matching record digest does not verify source data.</p>
      <ul>{readiness.checks.map(check => <li key={check.key}>
        <div><strong>{check.label}</strong><span className={`readiness-label readiness-${check.status.toLowerCase()}`}>{check.status.replaceAll("_", " ")}</span></div>
        <p>{check.detail}</p>
        {check.evidence_ref ? <small className="research-digest">{check.evidence_ref}<br />Recorded {check.evidence_recorded_at ? new Date(check.evidence_recorded_at).toLocaleString("en-IN") : "time unavailable"}</small> : <small>No recorded assessment evidence</small>}
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
    <div className="workspace-section-heading"><h3>Trial {trial.manifest.trial_number}</h3><span className="research-status">{state}</span></div>
    <p className="features-note">{trial.manifest.experiment_family} · Declared trial {trial.manifest.trial_number} of {trial.manifest.declared_search_budget}</p>
    <ResearchReadiness readiness={trial.readiness} />
    {trial.engine_evaluation === undefined ? <p className="paper-warning">This API does not expose engine evidence yet. An updated API is required.</p> : null}
    {result?.reason_code ? <p className="paper-warning">Reason: {result.reason_code.replaceAll("_", " ").toLowerCase()}. This attempt remains in the research history.</p> : null}
    {report ? <>
      <div className="research-metrics">{(["training", "validation"] as const).map(window => <div key={window}>
        <span>{window === "training" ? "Training" : "Validation"} · {report.metrics[window].closed_trades} closes</span>
        <strong>{money(report.metrics[window].net_pnl)}</strong>
        <small>{report.metrics[window].winning_trades} wins · {report.metrics[window].losing_trades} losses · {report.metrics[window].breakeven_trades} flat · illustrative costs</small>
      </div>)}</div>
      <p className="features-note">{report.status === "INSUFFICIENT_SAMPLE" ? "At least 20 closed trades in each window are required for sample review." : "Sample size alone does not establish an edge."} Profit probability is unavailable.</p>
    </> : <p className="workspace-empty">{evaluation ? "A fixed-rule binding is saved. No completed replay evidence is available." : "No engine evaluation has been recorded for this trial."}</p>}
    <details className="workspace-disclosure"><summary>Windows, costs &amp; evidence</summary>
      <dl className="research-facts"><dt>Training</dt><dd>{trial.manifest.train_start} → {trial.manifest.train_end}</dd><dt>Validation</dt><dd>{trial.manifest.validation_start} → {trial.manifest.validation_end}</dd><dt>Reserved holdout</dt><dd>{trial.manifest.holdout_start} → {trial.manifest.holdout_end}</dd><dt>Cost model</dt><dd>{trial.manifest.cost_model_id}</dd><dt>Engine evidence</dt><dd>{result?.evidence_origin ?? "Not recorded"}</dd><dt>Operator evidence</dt><dd>{trial.result ? `${trial.result.outcome} · ${trial.result.reason_code}` : "Not recorded"}</dd></dl>
      {report ? <><p className="features-note">Holdout evaluated: {report.holdout_evaluated ? "Yes — requires review" : "No"}. Boundary/gap trades excluded: {report.boundary_or_gap_trades_excluded}. Open trade excluded: {report.open_trade_excluded ? "Yes" : "No"}.</p><p className="features-note">Illustrative fee {report.cost_model.fee_fraction}, slippage {report.cost_model.slippage_fraction} (fractions per engine policy; not a complete broker charge schedule).</p><ul>{report.limitations.map(note => <li key={note}>{note}</li>)}</ul></> : null}
      <p className="features-note">Operator records and engine replay evidence are separate. Source, data quality and the hypothesis-to-rule binding remain unreviewed. No automatic promotion.</p>
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

  useEffect(() => {
    const controller = new AbortController();
    setBusy(true); setError(""); setCandidates([]); setSelected(""); setTrials([]); setTrialOffset(0);
    readRows<Candidate>(`${apiUrl}/research/candidates?limit=${PAGE_SIZE}&offset=${candidateOffset}`, token, controller.signal)
      .then(rows => { if (!controller.signal.aborted) { setCandidates(rows); setSelected(rows[0]?.id ?? ""); } })
      .catch((failure: unknown) => { if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : "Research could not be loaded. Check the API and retry."); })
      .finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [apiUrl, token, candidateOffset, refresh]);

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
  return <section className="paper-workspace app-section-page research-workspace" aria-label="Strategy research">
    <div className="workspace-section-heading"><div><span className="panel-kicker">RESEARCH · READ ONLY</span><h2>Strategy research</h2></div><button className="refresh-button" disabled={busy || trialBusy} onClick={() => setRefresh(value => value + 1)} type="button">Refresh evidence</button></div>
    <p>Review hypotheses and chronological replay results before considering a strategy. Research results stay separate from your paper portfolio.</p>
    <div className="research-notice">Recommendations are not qualified yet. Profit probability is unavailable. Automatic promotion is disabled.</div>
    {error ? <p role="alert" className="error-banner">{error}</p> : null}
    {busy ? <p role="status">Loading research…</p> : !error && !candidates.length ? <p className="workspace-empty">{candidateOffset ? "No more hypotheses on this page." : "No research hypotheses are registered for this account. Your paper positions are unchanged; synthetic demonstrations are kept in a disposable database."}</p> : null}
    {candidates.length ? <label className="research-picker">Research hypothesis<select value={selected} onChange={event => {setTrials([]); setTrialOffset(0); setSelected(event.target.value);}}>{candidates.map(row => <option key={row.id} value={row.id}>{row.definition.name} · {row.definition.market}</option>)}</select></label> : null}
    <div className="research-pagination"><button type="button" disabled={busy || candidateOffset === 0} onClick={() => setCandidateOffset(value => Math.max(0, value - PAGE_SIZE))}>Previous hypotheses</button><span>Page {candidateOffset / PAGE_SIZE + 1}</span><button type="button" disabled={busy || candidates.length < PAGE_SIZE} onClick={() => setCandidateOffset(value => value + PAGE_SIZE)}>Next hypotheses</button></div>
    {candidate ? <><details className="workspace-disclosure"><summary>Hypothesis &amp; declared rules · Unreviewed</summary><p>{candidate.definition.hypothesis}</p><p><strong>Entry:</strong> {candidate.definition.entry_rule}</p><p><strong>Exit:</strong> {candidate.definition.exit_rule}</p><p>{candidate.definition.limitation}</p><p>Declared license: {candidate.definition.source_license}. Source review: {candidate.source_review}. Text is not executable strategy code.</p></details>
      <h3>Recorded trials</h3>{trialBusy ? <p role="status">Loading trials…</p> : trialError ? <p role="alert" className="error-banner">{trialError}</p> : !trials.length ? <p className="workspace-empty">No trials on this page. No performance or probability can be inferred.</p> : <div className="research-trials">{trials.map(trial => <ResearchTrialCard key={trial.id} trial={trial} />)}</div>}
      <div className="research-pagination"><button type="button" disabled={trialBusy || trialOffset === 0} onClick={() => setTrialOffset(value => Math.max(0, value - PAGE_SIZE))}>Previous trials</button><span>Page {trialOffset / PAGE_SIZE + 1}</span><button type="button" disabled={trialBusy || trials.length < PAGE_SIZE} onClick={() => setTrialOffset(value => value + PAGE_SIZE)}>Next trials</button></div>
    </> : null}
  </section>;
}
