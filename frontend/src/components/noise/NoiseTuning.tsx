"use client";

// The replay and tune block of `/behavior/noise/[id]`.
//
// One form: the six detection values and the criticality, pre-filled from what the engine resolves
// now (never from a hard-coded default), each with the layer that supplies it. "Preview" replays the
// stored buckets under the form's values; "Save" is the log-source PATCH and stays off until a
// preview ran for exactly these values. Any edit makes the shown preview stale. Only the knobs that
// differ from what is in force are sent, so an untouched knob keeps resolving through its own layer.

import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { useCallback, useId, useRef, useState } from "react";

import { useSession } from "@/components/auth/session";
import { useEnumLabels } from "@/i18n/enumLabels";
import {
  ApiError,
  api,
  type NoiseCurrent,
  type NoiseDirection,
  type NoiseKnob,
  type NoisePreview,
  type NoiseWindow,
} from "@/lib/api";
import {
  CRITICALITIES,
  DIRECTIONS,
  KNOB_BOUNDS,
  changedKnobs,
  closesIncidents,
  formFromDetail,
  formIsUnchanged,
  formProblems,
  mergedCustomThresholds,
  previewKey,
  tuningFailure,
  type NumericKnob,
  type TuningFailure,
  type TuningForm,
} from "@/lib/noise";

import { NoisePreviewResult } from "./NoisePreviewResult";

import "@/components/ui/controls.css";
import "./noise.css";

const NUMERIC_KNOBS = Object.keys(KNOB_BOUNDS) as NumericKnob[];
const WINDOWS: readonly NoiseWindow[] = [7, 28];

interface Shown {
  key: string;
  preview: NoisePreview;
  at: string;
}

interface Saved {
  changes: string[];
  closes: boolean;
  reloaded: boolean;
}

function holds(permissions: readonly string[], grant: string): boolean {
  return permissions.includes(grant) || permissions.includes("admin:*");
}

export function NoiseTuning({
  logSourceId,
  initial,
}: {
  logSourceId: string;
  initial: NoiseCurrent;
}) {
  const t = useTranslations("noise.tuning");
  const { label } = useEnumLabels();
  const router = useRouter();
  const session = useSession();
  const uid = useId();

  const [current, setCurrent] = useState<NoiseCurrent>(initial);
  const [form, setForm] = useState<TuningForm>(() => formFromDetail(initial));
  const [shown, setShown] = useState<Shown | null>(null);
  const [running, setRunning] = useState(false);
  const [saving, setSaving] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [failure, setFailure] = useState<TuningFailure | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saved, setSaved] = useState<Saved | null>(null);
  const request = useRef(0);

  const me = session.me;
  const permissions = me ? [...me.permissions, ...me.effective_permissions] : [];
  const canWrite = holds(permissions, "log_source:write");
  const permissionsKnown = session.status === "ready" || session.status === "error";

  const problems = formProblems(form, current);
  const invalid = Object.keys(problems).length > 0;
  const key = previewKey(form, current);
  const fresh = shown !== null && shown.key === key;
  const unchanged = formIsUnchanged(form, current);
  const changed = changedKnobs(form, current);

  const edit = useCallback((next: Partial<TuningForm>) => {
    setForm((f) => ({ ...f, ...next }));
    setFailure(null);
    setSaveError(null);
    setSaved(null);
  }, []);

  async function runPreview(over?: Partial<TuningForm>) {
    const f = { ...form, ...over };
    if (Object.keys(formProblems(f, current)).length > 0) return;
    const ticket = ++request.current;
    const forKey = previewKey(f, current);
    setRunning(true);
    setPreviewError(null);
    setFailure(null);
    setSaved(null);
    try {
      const preview = await api.noisePreview(logSourceId, {
        window_days: f.window,
        thresholds: changedKnobs(f, current),
        ...(f.criticality !== current.criticality ? { criticality: f.criticality } : {}),
      });
      if (ticket === request.current) {
        setShown({ key: forKey, preview, at: new Date().toISOString() });
      }
    } catch (err) {
      if (ticket !== request.current) return;
      if (err instanceof ApiError && err.status === 422) {
        const parsed = tuningFailure(err.detail, err.message);
        setFailure(parsed);
        setPreviewError(parsed.general ? t("rejected", { detail: parsed.general }) : t("rejectedField"));
      } else if (err instanceof ApiError && err.status === 403) {
        setPreviewError(t("previewForbidden"));
      } else {
        setPreviewError(t("previewFailed", { detail: t("previewRetry") }));
      }
    } finally {
      if (ticket === request.current) setRunning(false);
    }
  }

  async function save() {
    if (!shown || !fresh || !canWrite || invalid || unchanged) return;
    const changes = describeChanges();
    const closes = closesIncidents(form, current);
    setSaving(true);
    setSaveError(null);
    setFailure(null);
    const p = shown.preview;
    try {
      await api.tuneLogSource(logSourceId, {
        custom_thresholds: mergedCustomThresholds(form, current),
        ...(form.criticality !== current.criticality ? { criticality: form.criticality } : {}),
        preview: {
          at: shown.at,
          window: p.window.days,
          current: p.current?.total_incidents ?? null,
          proposed: p.proposed?.total_incidents ?? null,
          state: p.state,
        },
      });
    } catch (err) {
      setSaving(false);
      if (err instanceof ApiError && err.status === 422) {
        const parsed = tuningFailure(err.detail, err.message);
        setFailure(parsed);
        setSaveError(parsed.general ? t("rejected", { detail: parsed.general }) : t("rejectedField"));
      } else if (err instanceof ApiError && err.status === 403) {
        setSaveError(t("saveForbidden"));
      } else if (err instanceof ApiError && err.status === 401) {
        setSaveError(t("saveExpired"));
      } else {
        setSaveError(t("saveFailed"));
      }
      return;
    }
    let reloaded = true;
    try {
      const detail = await api.noiseSource(logSourceId);
      setCurrent(detail.current);
      setForm(formFromDetail(detail.current, form.window));
    } catch {
      reloaded = false;
    }
    setShown(null);
    setSaved({ changes, closes, reloaded });
    setSaving(false);
    router.refresh();
  }

  function describeChanges(): string[] {
    const out: string[] = [];
    const eff = current.effective_thresholds;
    for (const knob of Object.keys(changed) as NoiseKnob[]) {
      out.push(
        t("saved.knob", {
          name: t(`knob.${knob}.label`).toLowerCase(),
          from: String(eff[knob].value),
          to: String(changed[knob]),
        }),
      );
    }
    if (form.criticality !== current.criticality) {
      out.push(
        t("saved.criticality", {
          from: label("severity", current.criticality),
          to: label("severity", form.criticality),
        }),
      );
    }
    return out;
  }

  const eff = current.effective_thresholds;
  const origin = (knob: NoiseKnob): string => {
    const e = eff[knob];
    const usable = knob === "direction" ? DIRECTIONS.some((d) => d === e.value) : typeof e.value === "number";
    if (!usable) return t("origin.unreadable", { value: JSON.stringify(e.value) });
    return e.source === "criticality"
      ? t("origin.criticality", { value: label("severity", current.criticality) })
      : t(`origin.${e.source}`);
  };

  const writeNote =
    !permissionsKnown || canWrite
      ? null
      : session.status === "ready"
        ? t("needWrite")
        : t("needWriteUnread");

  let saveHint: string | null = null;
  if (!canWrite) saveHint = null;
  else if (unchanged) saveHint = t("saveNothing");
  else if (invalid) saveHint = null;
  else if (!fresh) saveHint = t("saveNeedsPreview");
  else saveHint = t("saveFirstPreviewed");

  const canSave = canWrite && fresh && !invalid && !unchanged && !saving && !running;
  const criticalityMoved = form.criticality !== current.criticality;

  return (
    <div className="noise-tuning">
      <p className="noise-intro">{t("intro")}</p>
      {writeNote && (
        <div className="notice" role="status">
          <p>{writeNote}</p>
        </div>
      )}
      <form
        className="noise-form"
        aria-label={t("formLabel")}
        onSubmit={(e) => {
          e.preventDefault();
          void runPreview();
        }}
      >
        <div className="noise-knobs" role="group" aria-label={t("knobsLegend")}>
          {NUMERIC_KNOBS.map((knob) => {
            const b = KNOB_BOUNDS[knob];
            const id = `${uid}-${knob}`;
            const problem = problems[knob];
            const serverMessage = failure?.fields[knob];
            const message = serverMessage
              ? serverMessage
              : problem
                ? t(`problem.${problem}`, { min: String(b.min), max: String(b.max) })
                : null;
            const value = form.values[knob];
            const edited = knob in changed;
            return (
              <div className="noise-knob" key={knob}>
                <div className="noise-knob-name">
                  <label htmlFor={id}>{t(`knob.${knob}.label`)}</label>
                  <p className="field-hint" id={`${id}-hint`}>
                    {t(`knob.${knob}.hint`)} ({t("range", { min: String(b.min), max: String(b.max) })})
                  </p>
                </div>
                <div className="noise-knob-input">
                  <input
                    id={id}
                    className="field noise-number"
                    type="number"
                    inputMode="decimal"
                    min={b.min}
                    max={b.max}
                    step={b.step}
                    value={value}
                    onChange={(e) => edit({ values: { ...form.values, [knob]: e.target.value } })}
                    aria-invalid={message ? true : undefined}
                    aria-describedby={`${id}-hint${message ? ` ${id}-err` : ""}`}
                    disabled={saving}
                  />
                  <span className="noise-unit">{t(`knob.${knob}.unit`)}</span>
                </div>
                <div className="noise-knob-origin">
                  <span className="noise-origin">{origin(knob)}</span>
                  {edited && (
                    <span className="noise-edited">
                      {t("edited", { now: String(changed[knob]), was: String(eff[knob].value) })}
                    </span>
                  )}
                </div>
                {message && (
                  <p className="field-error noise-knob-error" id={`${id}-err`} role="alert">
                    {message}
                  </p>
                )}
              </div>
            );
          })}

          <div className="noise-knob">
            <div className="noise-knob-name">
              <label htmlFor={`${uid}-direction`}>{t("knob.direction.label")}</label>
              <p className="field-hint" id={`${uid}-direction-hint`}>
                {t("knob.direction.hint")}
              </p>
            </div>
            <div className="noise-knob-input">
              <select
                id={`${uid}-direction`}
                className="select-control"
                value={form.direction}
                onChange={(e) => edit({ direction: e.target.value as NoiseDirection })}
                aria-describedby={`${uid}-direction-hint${failure?.fields.direction ? ` ${uid}-direction-err` : ""}`}
                disabled={saving}
              >
                {DIRECTIONS.map((d) => (
                  <option key={d} value={d}>
                    {t(`directionOption.${d}`)}
                  </option>
                ))}
              </select>
            </div>
            <div className="noise-knob-origin">
              <span className="noise-origin">{origin("direction")}</span>
              {"direction" in changed && (
                <span className="noise-edited">
                  {t("edited", { now: form.direction, was: String(eff.direction.value) })}
                </span>
              )}
            </div>
            {failure?.fields.direction && (
              <p className="field-error noise-knob-error" id={`${uid}-direction-err`} role="alert">
                {failure.fields.direction}
              </p>
            )}
          </div>

          <div className="noise-knob">
            <div className="noise-knob-name">
              <label htmlFor={`${uid}-criticality`}>{t("criticalityLabel")}</label>
              <p className="field-hint" id={`${uid}-criticality-hint`}>
                {t("criticalityHint")}
              </p>
            </div>
            <div className="noise-knob-input">
              <select
                id={`${uid}-criticality`}
                className="select-control"
                value={form.criticality}
                onChange={(e) => edit({ criticality: e.target.value })}
                aria-describedby={`${uid}-criticality-hint`}
                disabled={saving}
              >
                {CRITICALITIES.map((c) => (
                  <option key={c} value={c}>
                    {label("severity", c)}
                  </option>
                ))}
              </select>
            </div>
            <div className="noise-knob-origin">
              <span className="noise-origin">{label("severity", current.criticality)}</span>
              {criticalityMoved && (
                <span className="noise-edited">
                  {t("edited", { now: label("severity", form.criticality), was: label("severity", current.criticality) })}
                </span>
              )}
            </div>
          </div>
        </div>

        <ul className="noise-notes">
          {current.lab_mode && <li>{t("labMode")}</li>}
          {current.has_by_type && <li>{t("byType")}</li>}
          {closesIncidents(form, current) && <li>{t("narrowing")}</li>}
        </ul>

        <div className="noise-actions">
          <fieldset className="noise-window">
            <legend>{t("windowLegend")}</legend>
            {WINDOWS.map((w) => (
              <label key={w} className="noise-radio">
                <input
                  type="radio"
                  name={`${uid}-window`}
                  checked={form.window === w}
                  onChange={() => edit({ window: w })}
                  disabled={saving}
                />
                {t("window", { days: w })}
              </label>
            ))}
          </fieldset>
          <button type="submit" className="action secondary" disabled={invalid || running || saving}>
            {running ? t("previewing") : t("preview", { days: form.window })}
          </button>
          <button
            type="button"
            className="action"
            disabled={!canSave}
            aria-describedby={saveHint ? `${uid}-save-hint` : undefined}
            onClick={() => void save()}
          >
            {saving ? t("saving") : t("save")}
          </button>
        </div>
        {saveHint && (
          <p className="field-hint" id={`${uid}-save-hint`}>
            {saveHint}
          </p>
        )}
      </form>

      <div aria-live="polite">
        {previewError && (
          <div className="notice" role="alert">
            <p>{previewError}</p>
          </div>
        )}
        {saveError && (
          <div className="notice" role="alert">
            <p>{saveError}</p>
          </div>
        )}
        {saved && (
          <div className="banner ok" role="status">
            <p>
              <strong>{t("saved.title")}</strong>{" "}
              {saved.changes.length > 0 && t("saved.changes", { list: saved.changes.join("; ") })}{" "}
              {t("saved.recorded")}
              {saved.closes && ` ${t("saved.closed")}`}
              {!saved.reloaded && ` ${t("saved.reloadFailed")}`}
            </p>
          </div>
        )}
      </div>

      {running && (
        <p className="muted" role="status">
          {t("previewRunning", { days: form.window })}
        </p>
      )}
      {shown && (
        <NoisePreviewResult
          preview={shown.preview}
          storedCriticality={current.criticality}
          stale={!fresh}
          onUseSevenDays={() => {
            edit({ window: 7 });
            void runPreview({ window: 7 });
          }}
        />
      )}
    </div>
  );
}
