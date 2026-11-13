"use client";

// The analyst training exclusions of one source's baseline (SOC-13d): what was removed from the
// volume baseline, by whom, whether a rebuild has used it yet, and a way to take it back.
//
// The first read is the server's. A failed read is its own state with a Retry, never an empty list;
// a read the caller may not make names the permission. Revoking needs a reason and, like creating,
// the `anomaly:triage` permission; a refusal from the server is shown as it came.

import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { useTranslations } from "next-intl";

import { useSession } from "@/components/auth/session";
import { TableScroll } from "@/components/ui/TableScroll";
import { UtcTime } from "@/components/ui/UtcTime";
import { ApiError, trainingExclusionApi, type TrainingExclusion } from "@/lib/api";
import {
  loadExclusions,
  loadExclusionsFor,
  type ExclusionLoad,
  type ExclusionSubject,
} from "@/lib/trainingExclusionLoad";
import {
  REASON_MAX,
  TRAIN_PERMISSION,
  canExclude,
  intervalText,
  permissionOf,
  stateKey,
  type Variant,
} from "@/lib/trainingExclusion";

import "@/components/ui/controls.css";
import "./baseline.css";

export function TrainingExclusions({
  sourceId,
  subject,
  variant = "source",
  initial,
}: {
  /** A log source (Phase A). Give this or `subject`. */
  sourceId?: string;
  /** Any other family's subject (SOC-13f), with the words of its baseline. */
  subject?: ExclusionSubject;
  variant?: Variant;
  initial: ExclusionLoad;
}) {
  const t = useTranslations("trainingExclusion");
  const session = useSession();
  const uid = useId();
  const [load, setLoad] = useState<ExclusionLoad>(initial);
  const [loading, setLoading] = useState(false);
  const [revoking, setRevoking] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [needsReason, setNeedsReason] = useState(false);
  const [pending, setPending] = useState(false);
  const [refused, setRefused] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const inFlight = useRef(false);
  const reasonRef = useRef<HTMLTextAreaElement | null>(null);

  // Focus goes into the revoke form when it opens.
  useEffect(() => {
    if (revoking) reasonRef.current?.focus();
  }, [revoking]);

  const permissions = session.me ? [...session.me.permissions, ...session.me.effective_permissions] : [];
  const known = session.status === "ready" || session.status === "error";
  const allowed = known && canExclude(permissions);

  async function retry() {
    setLoading(true);
    setLoad(subject ? await loadExclusionsFor(subject) : await loadExclusions(sourceId ?? ""));
    setLoading(false);
  }

  function refusalText(err: unknown): string {
    if (err instanceof ApiError) {
      if (err.status === 403) {
        return t("list.revokeNeedsPermission", { permission: permissionOf(err) ?? TRAIN_PERMISSION });
      }
      if ((err.status === 404 || err.status === 409 || err.status === 422) && err.message) {
        return err.message;
      }
    }
    return t("list.revokeNotSaved", {
      reason: err instanceof ApiError && err.message ? err.message : "",
    }).trim();
  }

  async function submit(e: FormEvent, row: TrainingExclusion) {
    e.preventDefault();
    if (inFlight.current) return;
    const text = reason.trim();
    if (text.length < 1 || text.length > REASON_MAX) {
      setNeedsReason(true);
      return;
    }
    inFlight.current = true;
    setPending(true);
    setRefused(null);
    try {
      const next = await trainingExclusionApi.revoke(row.id, { reason: text });
      setLoad((cur) =>
        cur.kind === "ok"
          ? { kind: "ok", items: cur.items.map((r) => (r.id === next.id ? next : r)) }
          : cur,
      );
      setRevoking(null);
      setReason("");
      setDone(true);
    } catch (err) {
      setRefused(refusalText(err));
    } finally {
      inFlight.current = false;
      setPending(false);
    }
  }

  const rule = variant === "ruleStorm" || variant === "ruleSilence";
  const heading = (
    <h3 className="exclusions-head" id={`${uid}-h`}>
      {t("list.heading")}
    </h3>
  );

  let body;
  if (loading) {
    body = (
      <p className="exclusions-state" role="status">
        {t("list.loading")}
      </p>
    );
  } else if (load.kind === "forbidden") {
    body = (
      <p className="exclusions-state" role="status">
        {t("list.forbidden", { permission: load.permission })}
      </p>
    );
  } else if (load.kind === "error") {
    body = (
      <div className="exclusions-state" role="status">
        <p>{t("list.error")}</p>
        <div className="actions">
          <button type="button" className="action secondary" onClick={() => void retry()}>
            {t("list.retry")}
          </button>
        </div>
      </div>
    );
  } else if (load.items.length === 0) {
    body = <p className="exclusions-state">{t("list.empty")}</p>;
  } else {
    const target = load.items.find((r) => r.id === revoking) ?? null;
    body = (
      <>
        <TableScroll label={t("list.caption")}>
          <table>
            <caption className="sr-only">{t("list.caption")}</caption>
            <thead>
              <tr>
                <th scope="col">{t("list.interval")}</th>
                <th scope="col">{t("list.scope")}</th>
                <th scope="col">{t("list.reason")}</th>
                <th scope="col">{t("list.by")}</th>
                <th scope="col">{t("list.effect")}</th>
                {allowed && <th scope="col">{t("list.action")}</th>}
              </tr>
            </thead>
            <tbody>
              {load.items.map((r) => (
                <tr key={r.id}>
                  <td className="num nowrap">{intervalText(r.starts_at, r.ends_at)}</td>
                  <td>
                    {t.has(`scope.${r.scope_key}` as never) ? t(`scope.${r.scope_key}` as never) : r.scope_key}
                  </td>
                  <td className="exclusions-reason">
                    {r.reason}
                    {r.revoked_at && (
                      <span className="exclusions-revoked">
                        {t("list.revokedBy", { actor: r.revoked_by ?? "—", reason: r.revocation_reason ?? "—" })}
                      </span>
                    )}
                  </td>
                  <td>
                    {r.created_by}
                    <br />
                    <UtcTime value={r.created_at} variant="compact" />
                  </td>
                  <td>
                    <span className="pill">{t(stateKey(r) as never)}</span>
                  </td>
                  {allowed && (
                    <td>
                      {r.state !== "REVOKED" && (
                        <button
                          type="button"
                          className="action secondary"
                          aria-expanded={revoking === r.id}
                          onClick={() => {
                            setRevoking(revoking === r.id ? null : r.id);
                            setReason("");
                            setNeedsReason(false);
                            setRefused(null);
                            setDone(false);
                          }}
                        >
                          {t("list.revoke")}
                        </button>
                      )}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </TableScroll>
        {load.items.length >= 100 && (
          <p className="exclusions-state">{t("list.capped", { limit: 100 })}</p>
        )}
        {target && allowed && (
          <form
            className="rule-form exclusions-form"
            aria-label={t("list.revokeTitle")}
            noValidate
            onSubmit={(e) => void submit(e, target)}
            onKeyDown={(e) => {
              if (e.key === "Escape" && !pending) setRevoking(null);
            }}
          >
            <p className="outage-form-title">{t("list.revokeTitle")}</p>
            <p className="training-confirm">
              {t(target.subject_kind === "rule" ? "list.revokeIntroRule" : "list.revokeIntro", {
                interval: intervalText(target.starts_at, target.ends_at),
              })}
            </p>
            <label htmlFor={`${uid}-reason`}>{t("list.revokeReason")}</label>
            <textarea
              id={`${uid}-reason`}
              className="field"
              rows={3}
              maxLength={REASON_MAX}
              ref={reasonRef}
              value={reason}
              disabled={pending}
              aria-invalid={needsReason ? true : undefined}
              onChange={(e) => {
                setReason(e.target.value);
                setNeedsReason(false);
              }}
            />
            {needsReason && (
              <p className="field-error" role="alert">
                {t("list.needsReason", { max: REASON_MAX })}
              </p>
            )}
            {refused && (
              <p className="field-error" role="alert">
                {refused}
              </p>
            )}
            <div className="actions">
              <button type="submit" className="action" disabled={pending}>
                {t("list.revokeSave")}
              </button>
              <button
                type="button"
                className="action secondary"
                disabled={pending}
                onClick={() => setRevoking(null)}
              >
                {t("list.revokeCancel")}
              </button>
            </div>
          </form>
        )}
        {done && (
          <p className="exclusions-state" role="status">
            {t(rule ? "list.revokedRule" : "list.revoked")}
          </p>
        )}
      </>
    );
  }

  return (
    <div className="exclusions" role="group" aria-labelledby={`${uid}-h`}>
      {heading}
      <p className="exclusions-lead">
        {variant === "source" ? t("list.lead") : t(`variant.${variant}.lead` as never)}
      </p>
      {body}
      {known && !allowed && load.kind === "ok" && load.items.length > 0 && (
        <p className="exclusions-state">
          {t("list.revokeNeedsPermission", { permission: TRAIN_PERMISSION })}
        </p>
      )}
    </div>
  );
}
