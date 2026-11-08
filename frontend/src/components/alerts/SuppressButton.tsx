"use client";

// "Don't alert this again" on an alert or an anomaly (SOC-17d).
//
// The form chooses a duration (1, 7 or 30 days, nothing else), a reason, an owner, and optionally
// the whole source or a planned change. The server derives the condition from the alert or the
// anomaly; nothing here names a detector. A refused request keeps the input and says why, a 422
// next to the field it names, a 404 by refreshing the page. A quiet period never stops an
// incident from being recorded, and the copy says so.

import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";

import { useSession } from "@/components/auth/session";
import { ChangeStatus, type ChangeOutcome } from "@/components/ui/ChangeStatus";
import { utcTimeText } from "@/components/ui/UtcTime";
import { suppressionApi, type Suppression, type SuppressionDays } from "@/lib/api";
import { asTextTranslator } from "@/i18n/textTranslator";
import {
  DAYS,
  OWNER_MAX,
  REASON_MAX,
  REASON_MIN,
  REFERENCE_MAX,
  SUPPRESS_PERMISSION,
  allowsWholeSource,
  canSuppress,
  suppressFailure,
  type SuppressField,
  type SuppressTarget,
} from "@/lib/suppression";

import "@/components/ui/controls.css";
import "./suppression.css";

/** Refreshes the server-rendered page once. Mounted only after a 404, so the button itself needs no
 *  router: a page that renders it in a test or a bare tree has none. */
function PageRefresh() {
  const router = useRouter();
  useEffect(() => {
    router.refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return null;
}

export function SuppressButton({
  target,
  detectorLabel,
  sourceName,
  mismatchWith,
  active,
  onSaved,
}: {
  target: SuppressTarget;
  /** The detector's name when the page knows it; the sentence stays generic without it. */
  detectorLabel: string | null;
  /** The source the condition belongs to, when the page could read it. */
  sourceName: string | null;
  /** The detector the alert's title names when it differs from the condition's, else null. */
  mismatchWith: string | null;
  /** The quiet period already holding this condition, if any: saving then moves its end. */
  active: Suppression | null;
  /** Called with the stored row after a save, so the banner can be read again. */
  onSaved: (row: Suppression) => Promise<void> | void;
}) {
  const t = useTranslations("suppression");
  const say = asTextTranslator(useTranslations());
  const session = useSession();
  const uid = useId();
  const me = session.me;
  const permissions = me ? [...me.permissions, ...me.effective_permissions] : [];
  const known = session.status === "ready" || session.status === "error";
  const allowed = known && canSuppress(permissions);

  const [open, setOpen] = useState(false);
  const [days, setDays] = useState<SuppressionDays>(1);
  const [reason, setReason] = useState("");
  const [owner, setOwner] = useState("");
  const [ownerTouched, setOwnerTouched] = useState(false);
  const [whole, setWhole] = useState(false);
  const [planned, setPlanned] = useState(false);
  const [reference, setReference] = useState("");
  const [fieldError, setFieldError] = useState<{ field: SuppressField; text: string } | null>(null);
  const [needs, setNeeds] = useState<SuppressField | null>(null);
  const [outcome, setOutcome] = useState<ChangeOutcome>({ kind: "idle" });
  const [pending, setPending] = useState(false);
  const [gone, setGone] = useState(false);
  const inFlight = useRef(false);
  const buttonRef = useRef<HTMLButtonElement | null>(null);
  const reasonRef = useRef<HTMLTextAreaElement | null>(null);
  const restore = useRef(false);

  // The owner starts as the signed-in user; once the analyst edits it, it is theirs.
  const subject = me?.subject ?? "";
  useEffect(() => {
    if (!ownerTouched) setOwner(subject);
  }, [subject, ownerTouched]);

  // Focus goes into the form when it opens and back to the button when it closes.
  useEffect(() => {
    if (open) reasonRef.current?.focus();
    else if (restore.current) {
      restore.current = false;
      buttonRef.current?.focus();
    }
  }, [open]);

  if (!known) return null;
  if (!allowed) {
    return (
      <p className="quiet-readonly" role="note">
        {t("button.noPermission", { permission: SUPPRESS_PERMISSION })}
      </p>
    );
  }

  const wholeAllowed = allowsWholeSource(target.family);
  const reasonText = reason.trim();
  const ownerText = owner.trim();
  const referenceText = reference.trim();
  const reasonOk = reasonText.length >= REASON_MIN && reason.length <= REASON_MAX;
  const ownerOk = ownerText.length > 0 && ownerText.length <= OWNER_MAX;
  const referenceOk = !planned || (referenceText.length > 0 && referenceText.length <= REFERENCE_MAX);
  const valid = reasonOk && ownerOk && referenceOk;

  function close() {
    restore.current = true;
    setOpen(false);
    setFieldError(null);
    setNeeds(null);
    setOutcome({ kind: "idle" });
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (inFlight.current) return;
    if (!valid) {
      setNeeds(!reasonOk ? "reason" : !ownerOk ? "owner" : "reference");
      return;
    }
    inFlight.current = true;
    setPending(true);
    setNeeds(null);
    setFieldError(null);
    setOutcome({ kind: "pending" });
    const body = {
      days,
      reason: reasonText,
      owner: ownerText,
      ...(whole && wholeAllowed ? { whole_source: true } : {}),
      ...(planned
        ? { kind: "KNOWN_CHANGE" as const, reference: referenceText }
        : {}),
    };
    try {
      const row =
        target.via === "alert"
          ? await suppressionApi.fromAlert(target.alertId, body)
          : await suppressionApi.fromAnomaly(target.family, target.anomalyId, body);
      restore.current = true;
      setOpen(false);
      setReason("");
      setPlanned(false);
      setReference("");
      setWhole(false);
      setOutcome({
        kind: "ok",
        text: t("form.saved", { until: utcTimeText(row.ends_at, "full") }),
      });
      await onSaved(row);
    } catch (err) {
      const failure = suppressFailure(err, say);
      if (failure.gone) {
        close();
        setOutcome({ kind: "error", text: failure.text });
        setGone(true);
      } else if (failure.field) {
        setFieldError({ field: failure.field, text: failure.text });
        setOutcome({ kind: "idle" });
      } else {
        setOutcome({ kind: failure.kind, text: failure.text });
      }
    } finally {
      inFlight.current = false;
      setPending(false);
    }
  }

  const ids = {
    days: `${uid}-days`,
    reason: `${uid}-reason`,
    owner: `${uid}-owner`,
    whole: `${uid}-whole`,
    planned: `${uid}-planned`,
    reference: `${uid}-reference`,
  };
  const dayText = (n: number) => t("form.day", { count: n });
  const errorAt = (field: SuppressField, local: string) => {
    if (fieldError?.field === field) {
      return (
        <p className="field-error" role="alert">
          {fieldError.text}
        </p>
      );
    }
    return needs === field ? (
      <p className="field-error" role="alert">
        {local}
      </p>
    ) : null;
  };
  const invalid = (field: SuppressField) =>
    fieldError?.field === field || needs === field ? true : undefined;
  const title = active ? t("form.titleAgain") : t("form.title");
  const effect = whole
    ? sourceName
      ? t("form.willWhole", { source: sourceName, days: dayText(days) })
      : t("form.willWholeGeneric", { days: dayText(days) })
    : detectorLabel
      ? sourceName
        ? t("form.willNarrow", { detector: detectorLabel, source: sourceName, days: dayText(days) })
        : t("form.willNarrowDetector", { detector: detectorLabel, days: dayText(days) })
      : t("form.willNarrowGeneric", { days: dayText(days) });

  return (
    <div className="quiet-action">
      {gone && <PageRefresh />}
      {!open && (
        <div className="actions">
          <button
            ref={buttonRef}
            type="button"
            className="action secondary"
            title={t("button.help")}
            onClick={() => {
              setOutcome({ kind: "idle" });
              setOpen(true);
            }}
          >
            {active ? t("button.openAgain") : t("button.open")}
          </button>
        </div>
      )}
      {open && (
        <form className="rule-form quiet-form" onSubmit={(e) => void submit(e)} aria-label={title} noValidate>
          <p className="outage-form-title">{title}</p>
          {active?.ends_at && (
            <p className="field-hint">
              {t("form.alreadyQuiet", { until: utcTimeText(active.ends_at, "full") })}
            </p>
          )}
          {mismatchWith && detectorLabel && (
            <div className="banner warn quiet-mismatch" role="note">
              <div className="quiet-body">
                <p>
                  <strong>{t("form.mismatchTitle")}</strong>
                </p>
                <p>
                  {sourceName
                    ? t("form.mismatch", { alert: mismatchWith, condition: detectorLabel, source: sourceName })
                    : t("form.mismatchGeneric", { alert: mismatchWith, condition: detectorLabel })}
                </p>
              </div>
            </div>
          )}
          <div className="rule-form-grid">
            <span id={ids.days} className="outage-form-label">
              {t("form.duration")}
            </span>
            <div>
              <div className="fm-radios" role="radiogroup" aria-labelledby={ids.days}>
                {DAYS.map((d) => (
                  <label key={d} className="fm-radio">
                    <input
                      type="radio"
                      name={ids.days}
                      checked={days === d}
                      disabled={pending}
                      onChange={() => setDays(d)}
                    />
                    {dayText(d)}
                  </label>
                ))}
              </div>
              <p className="field-hint">{t("form.durationHelp")}</p>
              {errorAt("days", "")}
            </div>

            <label htmlFor={ids.reason}>{t("form.reason")}</label>
            <div>
              <textarea
                id={ids.reason}
                ref={reasonRef}
                className="field"
                rows={3}
                maxLength={REASON_MAX}
                value={reason}
                disabled={pending}
                aria-invalid={invalid("reason")}
                aria-describedby={`${ids.reason}-help`}
                onChange={(e) => {
                  setReason(e.target.value);
                  setNeeds(null);
                  setFieldError(null);
                }}
              />
              <p className="field-hint quiet-count" id={`${ids.reason}-help`}>
                {t("form.reasonHelp", { min: REASON_MIN, max: REASON_MAX })}{" "}
                {t("form.count", { used: reason.length, max: REASON_MAX })}
              </p>
              {errorAt("reason", t("form.needsReason", { min: REASON_MIN }))}
            </div>

            <label htmlFor={ids.owner}>{t("form.owner")}</label>
            <div>
              <input
                id={ids.owner}
                className="field"
                type="text"
                maxLength={OWNER_MAX}
                value={owner}
                disabled={pending}
                aria-invalid={invalid("owner")}
                onChange={(e) => {
                  setOwner(e.target.value);
                  setOwnerTouched(true);
                  setNeeds(null);
                  setFieldError(null);
                }}
              />
              <p className="field-hint">{t("form.ownerHelp")}</p>
              {errorAt("owner", t("form.needsOwner"))}
            </div>

            {wholeAllowed && (
              <>
                <span className="outage-form-label">{t("form.scopeLabel")}</span>
                <div>
                  <label className="fm-radio" htmlFor={ids.whole}>
                    <input
                      id={ids.whole}
                      type="checkbox"
                      checked={whole}
                      disabled={pending}
                      onChange={(e) => setWhole(e.target.checked)}
                    />
                    {t("form.wholeSource")}
                  </label>
                  <p className="field-hint">
                    {sourceName
                      ? t("form.wholeSourceHelp", { source: sourceName })
                      : t("form.wholeSourceHelpGeneric")}
                  </p>
                </div>
              </>
            )}

            <span className="outage-form-label">{t("form.kindLabel")}</span>
            <div>
              <label className="fm-radio" htmlFor={ids.planned}>
                <input
                  id={ids.planned}
                  type="checkbox"
                  checked={planned}
                  disabled={pending}
                  onChange={(e) => {
                    setPlanned(e.target.checked);
                    setNeeds(null);
                  }}
                />
                {t("form.planned")}
              </label>
              <p className="field-hint">{t("form.plannedHelp")}</p>
            </div>

            {planned && (
              <>
                <label htmlFor={ids.reference}>{t("form.reference")}</label>
                <div>
                  <input
                    id={ids.reference}
                    className="field"
                    type="text"
                    maxLength={REFERENCE_MAX}
                    value={reference}
                    disabled={pending}
                    aria-invalid={invalid("reference")}
                    onChange={(e) => {
                      setReference(e.target.value);
                      setNeeds(null);
                      setFieldError(null);
                    }}
                  />
                  <p className="field-hint">{t("form.referenceHelp", { max: REFERENCE_MAX })}</p>
                  {errorAt("reference", t("form.needsReference"))}
                </div>
              </>
            )}
          </div>

          <div className="impact" role="group" aria-label={t("form.before")}>
            <p className="impact-title">{t("form.before")}</p>
            <ul className="impact-lines" aria-live="polite">
              <li>{effect}</li>
            </ul>
          </div>

          <div className="actions">
            <button type="submit" className="action" disabled={pending}>
              {pending ? t("form.saving") : t("form.save")}
            </button>
            <button type="button" className="action secondary" disabled={pending} onClick={close}>
              {t("form.cancel")}
            </button>
          </div>
          <ChangeStatus outcome={outcome} />
        </form>
      )}
      {!open && <ChangeStatus outcome={outcome} />}
    </div>
  );
}
