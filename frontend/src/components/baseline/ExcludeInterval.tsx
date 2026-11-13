"use client";

// "Exclude interval from training" on a resolved Phase A anomaly (SOC-13d, D-73).
//
// It sits in the analyst-triage block and answers one question: is the baseline protected from
// this incident, and may I protect more of it? The detected span is excluded by the engine
// whatever happens here; this control only lets an analyst widen the excluded interval to buckets
// next to it that the incident also contaminated. The change applies at the next baseline rebuild,
// so on success the text says "pending" and never that the expected range changed.
//
// The server owns every rule (lifecycle, overlap, cap, alignment, widen-only). The form refuses
// the obvious ones before sending, and shows a coded refusal from the server exactly as it came.

import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";

import { useSession } from "@/components/auth/session";
import { ChangeStatus, type ChangeOutcome } from "@/components/ui/ChangeStatus";
import { ApiError, trainingExclusionApi, type TrainingExclusion } from "@/lib/api";
import {
  MAX_DAYS,
  REASON_MAX,
  TRAIN_PERMISSION,
  canExclude,
  defaultInterval,
  exclusionAvailability,
  exclusionTargetOf,
  intervalProblem,
  intervalText,
  isLive,
  parseUtcField,
  permissionOf,
  resultKey,
  stateKey,
  utcFieldText,
  type IntervalProblem,
  type Span,
  type TargetFields,
} from "@/lib/trainingExclusion";
import type { AnomalyDetail } from "@/lib/api";
import type { ExclusionLoad } from "@/lib/trainingExclusionLoad";

import "@/components/ui/controls.css";
import "./baseline.css";

/** Refreshes the server-rendered page once, so the chart reads the new exclusion. */
function PageRefresh() {
  const router = useRouter();
  useEffect(() => {
    router.refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return null;
}

/** Any family's anomaly: the lifecycle fields plus whatever names the subject. */
type AnomalyFields = Pick<AnomalyDetail, "id" | "state" | "detected_at" | "resolved_at" | "anomaly_end"> &
  TargetFields;

export function ExcludeInterval({
  anomaly,
  bucketSeconds,
  load,
}: {
  anomaly: AnomalyFields;
  /** The bucket width the server aligns to; null when the page could not read one (for a rule: the
   *  match-window width). */
  bucketSeconds: number | null;
  load: ExclusionLoad;
}) {
  const t = useTranslations("trainingExclusion");
  const session = useSession();
  const uid = useId();
  const kind = exclusionAvailability(anomaly);
  const target = exclusionTargetOf(anomaly);
  const variant = target?.variant ?? "source";
  const family = variant !== "source";

  const [rows, setRows] = useState<TrainingExclusion[]>(load.kind === "ok" ? load.items : []);
  const [open, setOpen] = useState(false);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [reason, setReason] = useState("");
  const [needsReason, setNeedsReason] = useState(false);
  const [pending, setPending] = useState(false);
  const [refused, setRefused] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<ChangeOutcome>({ kind: "idle" });
  const [refresh, setRefresh] = useState(false);
  const inFlight = useRef(false);
  const buttonRef = useRef<HTMLButtonElement | null>(null);
  const startRef = useRef<HTMLInputElement | null>(null);
  const restore = useRef(false);

  // Focus goes into the form when it opens and back to the button when it closes.
  useEffect(() => {
    if (open) startRef.current?.focus();
    else if (restore.current) {
      restore.current = false;
      buttonRef.current?.focus();
    }
  }, [open]);

  if (kind === "unsupported") return null;
  if (kind === "active") return <Shell uid={uid} note={t("block.active")} />;
  if (kind === "not-resolved") return <Shell uid={uid} note={t("block.notResolved")} />;

  const permissions = session.me ? [...session.me.permissions, ...session.me.effective_permissions] : [];
  const known = session.status === "ready" || session.status === "error";
  const allowed = known && canExclude(permissions);
  const base: Span | null =
    bucketSeconds === null ? null : defaultInterval(anomaly, bucketSeconds);
  const minutes = bucketSeconds === null ? 0 : bucketSeconds / 60;

  const mine = rows.filter((r) => r.origin_anomaly_id === anomaly.id && isLive(r));
  const baseIso = base && {
    start: new Date(base.startMs).toISOString(),
    end: new Date(base.endMs).toISOString(),
  };

  const problem: IntervalProblem | null =
    base && bucketSeconds !== null ? intervalProblem(start, end, base, bucketSeconds) : null;
  const shownStart = parseUtcField(start);
  const shownEnd = parseUtcField(end);
  const typed =
    shownStart !== null && shownEnd !== null
      ? intervalText(new Date(shownStart).toISOString(), new Date(shownEnd).toISOString())
      : "—";

  function problemText(p: IntervalProblem): string {
    const edge = base ? (p === "start-later" ? base.startMs : base.endMs) : null;
    const key = family && p === "unaligned" ? "form.problem.unalignedFamily" : `form.problem.${p}`;
    return t(key as never, {
      grid: family ? t(`variant.${variant}.grid` as never) : "",
      minutes,
      days: MAX_DAYS,
      time: edge === null ? "" : `${utcFieldText(edge)} UTC`,
    } as never);
  }

  function openForm() {
    if (!base) return;
    setStart(utcFieldText(base.startMs));
    setEnd(utcFieldText(base.endMs));
    setReason("");
    setNeedsReason(false);
    setRefused(null);
    setOutcome({ kind: "idle" });
    setOpen(true);
  }

  function close() {
    restore.current = true;
    setOpen(false);
    setRefused(null);
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (inFlight.current || problem !== null || shownStart === null || shownEnd === null) return;
    const text = reason.trim();
    if (text.length < 1 || text.length > REASON_MAX) {
      setNeedsReason(true);
      return;
    }
    inFlight.current = true;
    setPending(true);
    setRefused(null);
    setOutcome({ kind: "pending" });
    try {
      const body = {
        starts_at: new Date(shownStart).toISOString(),
        ends_at: new Date(shownEnd).toISOString(),
        reason: text,
      };
      const row = target && target.family !== "log_source"
        ? await trainingExclusionApi.create(anomaly.id, body, target.family)
        : await trainingExclusionApi.create(anomaly.id, body);
      setRows((cur) => [row, ...cur.filter((r) => r.id !== row.id)]);
      restore.current = true;
      setOpen(false);
      setOutcome({ kind: "ok", text: t(resultKey(row) as never) });
      setRefresh(true);
    } catch (err) {
      setOutcome({ kind: "idle" });
      setRefused(refusalText(err));
    } finally {
      inFlight.current = false;
      setPending(false);
    }
  }

  function refusalText(err: unknown): string {
    if (err instanceof ApiError) {
      if (err.status === 403) {
        return t("form.forbidden", { permission: permissionOf(err) ?? TRAIN_PERMISSION });
      }
      if (err.status === 404) return t("form.gone");
      // A coded 409 or 422 carries the server's own sentence; it is shown as it came.
      if ((err.status === 409 || err.status === 422) && err.message) return err.message;
    }
    return t("form.notSaved", { reason: err instanceof ApiError && err.message ? err.message : "" }).trim();
  }

  const ids = { start: `${uid}-start`, end: `${uid}-end`, reason: `${uid}-reason`, hint: `${uid}-hint` };
  const startBad = problem === "start-unreadable" || problem === "unaligned" || problem === "start-later" || problem === "start-order";
  const endBad = problem === "end-unreadable" || problem === "end-earlier" || problem === "too-long";
  // "Not widened" is the state the form opens in: it explains, it does not accuse.
  const hardProblem = problem !== null && problem !== "not-widened";

  return (
    <Shell uid={uid}>
      {refresh && <PageRefresh />}
      <p className="training-line">
        <span className="training-label">{t("block.protectionLabel")}</span>{" "}
        {mine.length === 0 ? (
          t("block.autoOnly")
        ) : (
          <>{t("block.auto")}</>
        )}
      </p>
      {load.kind === "error" && (
        <p className="training-note" role="status">
          {t("block.listUnknown")}
        </p>
      )}
      {load.kind === "forbidden" && (
        <p className="training-note" role="status">
          {t("block.listForbidden", { permission: load.permission })}
        </p>
      )}
      {mine.length > 0 && (
        <ul className="training-rows" aria-label={t("block.protectionLabel")}>
          {mine.map((r) => (
            <li key={r.id}>
              <span className="num">{intervalText(r.starts_at, r.ends_at)}</span>
              <span className="pill">{t(stateKey(r) as never)}</span>
            </li>
          ))}
        </ul>
      )}

      {known && !allowed && <p className="training-note">{t("block.readOnly", { permission: TRAIN_PERMISSION })}</p>}

      {allowed && base === null && (
        <p className="training-note">
          {family ? t(`variant.${variant}.noWidth` as never) : t("block.noWidth")}
        </p>
      )}

      {allowed && base !== null && !open && (
        <div className="actions">
          <button ref={buttonRef} type="button" className="action secondary" onClick={openForm}>
            {t("block.open")}
          </button>
        </div>
      )}

      {allowed && base !== null && open && bucketSeconds !== null && (
        <form
          className="rule-form training-form"
          aria-label={t("form.title")}
          noValidate
          onSubmit={(e) => void submit(e)}
          onKeyDown={(e) => {
            if (e.key === "Escape" && !pending) close();
          }}
        >
          <p className="outage-form-title">{t("form.title")}</p>
          <p className="training-confirm">
            {family
              ? t("form.confirmFamily", {
                  interval: typed,
                  baseline: t(`variant.${variant}.baseline` as never),
                  scopeNote: t(`variant.${variant}.scopeNote` as never),
                  when: t(`variant.${variant}.when` as never),
                })
              : t("form.confirm", { interval: typed })}
          </p>
          <div className="rule-form-grid">
            <label htmlFor={ids.start}>{t("form.start")}</label>
            <div>
              <input
                id={ids.start}
                ref={startRef}
                className="field field-narrow"
                type="text"
                inputMode="numeric"
                autoComplete="off"
                spellCheck={false}
                value={start}
                disabled={pending}
                aria-invalid={startBad && hardProblem ? true : undefined}
                aria-describedby={ids.hint}
                onChange={(e) => {
                  setStart(e.target.value);
                  setRefused(null);
                }}
              />
            </div>
            <label htmlFor={ids.end}>{t("form.end")}</label>
            <div>
              <input
                id={ids.end}
                className="field field-narrow"
                type="text"
                inputMode="numeric"
                autoComplete="off"
                spellCheck={false}
                value={end}
                disabled={pending}
                aria-invalid={endBad && hardProblem ? true : undefined}
                aria-describedby={ids.hint}
                onChange={(e) => {
                  setEnd(e.target.value);
                  setRefused(null);
                }}
              />
              <p className="field-hint" id={ids.hint}>
                {family
                  ? t("form.formatFamily", { minutes, grid: t(`variant.${variant}.grid` as never) })
                  : t("form.format", { minutes })}{" "}
                {t("form.widenOnly", {
                  interval: baseIso ? intervalText(baseIso.start, baseIso.end) : "—",
                  days: MAX_DAYS,
                })}
              </p>
              {problem !== null && (
                <p
                  className={hardProblem ? "field-error" : "field-hint"}
                  role={hardProblem ? "alert" : undefined}
                >
                  {problemText(problem)}
                </p>
              )}
            </div>
            <label htmlFor={ids.reason}>{t("form.reason")}</label>
            <div>
              <textarea
                id={ids.reason}
                className="field"
                rows={3}
                maxLength={REASON_MAX}
                value={reason}
                disabled={pending}
                aria-invalid={needsReason ? true : undefined}
                aria-describedby={`${ids.reason}-help`}
                onChange={(e) => {
                  setReason(e.target.value);
                  setNeedsReason(false);
                }}
              />
              <p className="field-hint" id={`${ids.reason}-help`}>
                {t("form.reasonHelp", { max: REASON_MAX })}{" "}
                {t("form.count", { used: reason.length, max: REASON_MAX })}
              </p>
              {needsReason && (
                <p className="field-error" role="alert">
                  {t("form.needsReason", { max: REASON_MAX })}
                </p>
              )}
            </div>
          </div>
          {refused && (
            <p className="field-error" role="alert">
              {refused}
            </p>
          )}
          <div className="actions">
            <button type="submit" className="action" disabled={pending || problem !== null}>
              {pending ? t("form.saving") : t("form.save")}
            </button>
            <button type="button" className="action secondary" disabled={pending} onClick={close}>
              {t("form.cancel")}
            </button>
          </div>
        </form>
      )}
      {!open && <ChangeStatus outcome={outcome} />}
    </Shell>
  );
}

function Shell({
  uid,
  note,
  children,
}: {
  uid: string;
  note?: string;
  children?: ReactNode;
}) {
  const t = useTranslations("trainingExclusion");
  return (
    <div className="training" role="group" aria-labelledby={`${uid}-h`}>
      <h3 className="triage-subheading" id={`${uid}-h`}>
        {t("block.heading")}
      </h3>
      {note && <p className="training-note">{note}</p>}
      {children}
    </div>
  );
}
