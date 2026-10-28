"use client";

// The editor for one policy row: a mode, a grace, and what saving does.
//
// The impact is shown while the operator edits, before anything is sent. A
// failed save keeps the input and says why; a 422 on the grace is shown at the
// grace field, where it can be corrected. Submitting twice is not possible while
// a save is in flight.

import { useId, useRef, useState, type FormEvent } from "react";
import { useTranslations } from "next-intl";

import { ChangeStatus, type ChangeOutcome } from "@/components/ui/ChangeStatus";
import type { OutageMode } from "@/lib/api";
import { plainCount } from "@/lib/figureText";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";

import {
  GRACE_MAX,
  GRACE_MIN,
  MODE_LABELS,
  formatMinutes,
  modeLabel,
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
  const t = useTranslations("outage.form");
  const i18n = useBehaviorI18n();
  const uid = useId();
  const [mode, setMode] = useState<OutageMode>(initialMode);
  const [graceText, setGraceText] = useState(initialGrace === null ? "" : String(initialGrace));
  const [graceError, setGraceError] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<ChangeOutcome>({ kind: "idle" });
  const [pending, setPending] = useState(false);
  const inFlight = useRef(false);

  const parsed = parseGrace(graceText, i18n);
  const grace = parsed.ok ? parsed.value : initialGrace;
  const impact = impactFor(mode, grace);
  const unchanged = parsed.ok && isUnchanged(mode, grace);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (inFlight.current) return;
    const g = parseGrace(graceText, i18n);
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
      const failure = saveFailure(err, i18n);
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
          {t("mode")}
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
              {modeLabel(m, i18n)}
            </label>
          ))}
        </div>
        <label htmlFor={graceId}>{t("grace")}</label>
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
            {t("hint", {
              min: plainCount(GRACE_MIN, i18n.locale),
              max: plainCount(GRACE_MAX, i18n.locale),
            })}
            {bucketSeconds !== null &&
              t("bucketHint", { duration: formatMinutes(bucketSeconds / 60, i18n) })}
          </p>
          {graceError && (
            <p id={errId} className="field-error" role="alert">
              {graceError}
            </p>
          )}
        </div>
      </div>
      <ImpactNote impact={impact} title={t("before")} />
      <div className="actions">
        <button type="submit" className="action" disabled={pending || unchanged}>
          {pending ? t("saving") : t("save")}
        </button>
        <button type="button" className="action secondary" disabled={pending} onClick={onCancel}>
          {t("cancel")}
        </button>
        {unchanged && !pending && <span className="field-hint">{t("unchanged")}</span>}
      </div>
      <ChangeStatus outcome={outcome} />
    </form>
  );
}
