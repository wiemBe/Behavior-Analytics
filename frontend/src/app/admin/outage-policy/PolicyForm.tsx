"use client";

// The editor for one policy row: a mode, a grace, and what saving does.
//
// The impact is shown while the operator edits, before anything is sent. A
// failed save keeps the input and says why; a 422 on the grace is shown at the
// grace field, where it can be corrected. Submitting twice is not possible while
// a save is in flight.

import { useId, useRef, useState, type FormEvent } from "react";

import { ChangeStatus, type ChangeOutcome } from "@/components/ui/ChangeStatus";
import type { OutageMode } from "@/lib/api";

import {
  GRACE_MAX,
  GRACE_MIN,
  MODE_LABELS,
  formatMinutes,
  parseGrace,
  saveFailure,
  type Impact,
} from "./outage";

import "@/components/ui/controls.css";

export interface PolicyBody {
  mode: OutageMode;
  silence_grace_buckets: number | null;
}

export function ImpactNote({ impact, title }: { impact: Impact; title: string }) {
  return (
    <div className={impact.known ? "impact" : "impact is-unknown"} role="group" aria-label={title}>
      <p className="impact-title">{title}</p>
      <ul className="impact-lines" aria-live="polite">
        {impact.lines.map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
    </div>
  );
}

export function PolicyForm({
  label,
  context,
  initialMode,
  initialGrace,
  bucketSeconds,
  impactFor,
  isUnchanged,
  onSave,
  onCancel,
}: {
  /** Names the form: "Edit Firewall", "Override for pa-fw-01". */
  label: string;
  /** One line under the title: what the row is about. */
  context?: string;
  initialMode: OutageMode;
  initialGrace: number | null;
  bucketSeconds: number | null;
  impactFor: (mode: OutageMode, grace: number | null) => Impact;
  /** True when saving would store what is already in effect. */
  isUnchanged: (mode: OutageMode, grace: number | null) => boolean;
  onSave: (body: PolicyBody) => Promise<void>;
  onCancel: () => void;
}) {
  const uid = useId();
  const [mode, setMode] = useState<OutageMode>(initialMode);
  const [graceText, setGraceText] = useState(initialGrace === null ? "" : String(initialGrace));
  const [graceError, setGraceError] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<ChangeOutcome>({ kind: "idle" });
  const [pending, setPending] = useState(false);
  const inFlight = useRef(false);

  const parsed = parseGrace(graceText);
  const grace = parsed.ok ? parsed.value : initialGrace;
  const impact = impactFor(mode, grace);
  const unchanged = parsed.ok && isUnchanged(mode, grace);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (inFlight.current) return;
    const g = parseGrace(graceText);
    if (!g.ok) {
      setGraceError(g.message);
      return;
    }
    inFlight.current = true;
    setPending(true);
    setGraceError(null);
    setOutcome({ kind: "pending" });
    try {
      await onSave({ mode, silence_grace_buckets: g.value });
    } catch (err) {
      const failure = saveFailure(err);
      if (failure.field === "grace") {
        setGraceError(failure.text);
        setOutcome({ kind: "idle" });
      } else {
        setOutcome({ kind: failure.kind, text: failure.text });
      }
    } finally {
      inFlight.current = false;
      setPending(false);
    }
  }

  const modeId = `${uid}-mode`;
  const graceId = `${uid}-grace`;
  const hintId = `${uid}-grace-hint`;
  const errId = `${uid}-grace-err`;

  return (
    <form className="rule-form outage-form" onSubmit={(e) => void submit(e)} aria-label={label}>
      <p className="outage-form-title">{label}</p>
      {context && <p className="field-hint">{context}</p>}
      <div className="rule-form-grid">
        <span id={modeId} className="outage-form-label">
          Mode
        </span>
        <div className="fm-radios" role="radiogroup" aria-labelledby={modeId}>
          {(Object.keys(MODE_LABELS) as OutageMode[]).map((m) => (
            <label key={m} className="fm-radio">
              <input
                type="radio"
                name={modeId}
                value={m}
                checked={mode === m}
                disabled={pending}
                onChange={() => setMode(m)}
              />
              {MODE_LABELS[m]}
            </label>
          ))}
        </div>
        <label htmlFor={graceId}>Grace, in buckets</label>
        <div>
          <input
            id={graceId}
            className="field field-narrow"
            inputMode="numeric"
            autoComplete="off"
            value={graceText}
            disabled={pending}
            aria-invalid={graceError ? true : undefined}
            aria-describedby={graceError ? `${hintId} ${errId}` : hintId}
            onChange={(e) => {
              setGraceText(e.target.value);
              setGraceError(null);
            }}
          />
          <p id={hintId} className="field-hint">
            {GRACE_MIN} to {GRACE_MAX}. Blank uses the default.
            {bucketSeconds !== null &&
              ` One bucket is ${formatMinutes(bucketSeconds / 60)}.`}
          </p>
          {graceError && (
            <p id={errId} className="field-error" role="alert">
              {graceError}
            </p>
          )}
        </div>
      </div>
      <ImpactNote impact={impact} title="Before you save" />
      <div className="actions">
        <button type="submit" className="action" disabled={pending || unchanged}>
          {pending ? "Saving…" : "Save"}
        </button>
        <button type="button" className="action secondary" disabled={pending} onClick={onCancel}>
          Cancel
        </button>
        {unchanged && !pending && <span className="field-hint">Nothing differs from what is in effect.</span>}
      </div>
      <ChangeStatus outcome={outcome} />
    </form>
  );
}
