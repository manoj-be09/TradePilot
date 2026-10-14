"use client";

import { useEffect, useState } from "react";

type Provenance = {
  study_id: string; generation: number; parent_study_id: string | null;
  dataset_digest: string; report_digest: string;
  assumptions: Record<string, unknown>; missing_assumptions: string[]; scope: string;
};

export function StudyProvenance({ studyId, apiUrl, token }: {
  studyId: string; apiUrl: string; token: string;
}) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<Provenance | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setData(null); setError(""); setBusy(false);
    if (!open) return;
    const controller = new AbortController();
    setBusy(true);
    void (async () => {
      try {
        const response = await fetch(`${apiUrl}/equity/validation-studies/${studyId}/provenance`, {
          headers: { Authorization: `Bearer ${token}` }, cache: "no-store",
          signal: controller.signal,
        });
        if (!response.ok) throw new Error(response.status === 404
          ? "Study provenance unavailable. Check the API update."
          : response.status === 409 ? "Saved evidence failed integrity review."
          : "Could not read study provenance.");
        const result = await response.json() as Provenance;
        if (!controller.signal.aborted) setData(result);
      } catch (cause) {
        if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "Provenance unavailable.");
      } finally {
        if (!controller.signal.aborted) setBusy(false);
      }
    })();
    return () => controller.abort();
  }, [open, studyId, apiUrl, token]);

  return <details className="equity-context-details" onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>Saved research assumptions</summary>
    {busy && <p role="status">Loading saved assumptions…</p>}
    {error && <p role="alert" className="error-banner">{error}</p>}
    {data?.study_id === studyId && <>
      <p className="muted">{data.scope}. Generation {data.generation}.</p>
      {data.missing_assumptions.length > 0 && <p role="status">Missing legacy assumptions: {data.missing_assumptions.join(", ")}.</p>}
      <dl>{Object.entries(data.assumptions).map(([name, value]) => <div key={name}>
        <dt>{name.replaceAll("_", " ")}</dt>
        <dd style={{ overflowWrap: "anywhere" }}>{typeof value === "object"
          ? <pre style={{ whiteSpace: "pre-wrap" }}>{JSON.stringify(value, null, 2)}</pre>
          : String(value)}</dd>
      </div>)}</dl>
      <details><summary>Study identifiers</summary><dl>
        {Object.entries({ study: data.study_id, parent: data.parent_study_id ?? "Root study",
          dataset: data.dataset_digest, report: data.report_digest }).map(([name, value]) => <div key={name}>
          <dt>{name}</dt><dd style={{ overflowWrap: "anywhere" }}>{value}</dd>
        </div>)}
      </dl></details>
    </>}
  </details>;
}
