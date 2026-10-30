"use client";

import { useEffect, useRef, type ReactNode } from "react";

export type DetailFigure = { label: string; value: string; tone?: "profit" | "loss" };
// met is left out for rules with no live reading to compare against.
export type DetailRule = { text: string; now?: string | null; met?: boolean | null };

function ruleState(rule: DetailRule) {
  if (rule.met === undefined) return null;
  if (rule.met === null) return <small className="detail-rule-state">No reading yet</small>;
  return <small className={`detail-rule-state detail-rule-state--${rule.met ? "met" : "unmet"}`}>{rule.met ? "Met now" : "Not met now"}{rule.now ? `: ${rule.now}` : ""}</small>;
}

export function StrategyDetails({ name, summary, tone, context, figures, legs, entry, exit, notes, action, onClose }: {
  name: string;
  summary: string;
  tone: string;
  context: string;
  figures: DetailFigure[];
  legs?: string[];
  entry: DetailRule[];
  exit: DetailRule[];
  notes: string[];
  action: ReactNode;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    if (dialog.current && !dialog.current.open) dialog.current.showModal();
  }, []);

  return (
    <dialog
      aria-label={`${name} details`}
      className="strategy-details"
      onClick={(event) => { if (event.target === dialog.current) dialog.current?.close(); }}
      onClose={onClose}
      ref={dialog}
    >
      <div className="strategy-details-body">
        <header className={`strategy-details-head strategy-tile--${tone}`}>
          <div><h3>{name}</h3><p>{summary}</p><small>{context}</small></div>
          <button aria-label="Close details" className="strategy-details-close" onClick={() => dialog.current?.close()} type="button">×</button>
        </header>
        <div className="strategy-details-figures">
          {figures.map((figure) => <div key={figure.label}><span>{figure.label}</span><strong className={figure.tone ? `detail-${figure.tone}` : undefined}>{figure.value}</strong></div>)}
        </div>
        {legs?.length ? <section><h4>Position</h4><ul className="strategy-details-legs">{legs.map((leg) => <li key={leg}>{leg}</li>)}</ul></section> : null}
        <section>
          <h4>When it enters</h4>
          <p className="strategy-details-lead">All of these must be true at the same check.</p>
          <ul className="strategy-details-rules">{entry.map((rule) => <li key={rule.text}><span>{rule.text}</span>{ruleState(rule)}</li>)}</ul>
        </section>
        <section>
          <h4>When it exits</h4>
          <p className="strategy-details-lead">Whichever happens first.</p>
          <ul className="strategy-details-rules">{exit.map((rule) => <li key={rule.text}><span>{rule.text}</span></li>)}</ul>
        </section>
        <section>
          <h4>Worth knowing</h4>
          <ul className="strategy-details-notes">{notes.map((note) => <li key={note}>{note}</li>)}</ul>
        </section>
      </div>
      <footer className="strategy-details-foot">{action}</footer>
    </dialog>
  );
}
