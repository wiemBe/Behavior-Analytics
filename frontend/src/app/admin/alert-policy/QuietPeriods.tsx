"use client";

// The quiet periods: every condition held back right now, with who set it, why, until when and
// what was recorded meanwhile, plus every maintenance window (SOC-17d, design §5.8).
//
// Maintenance rows are read-only and belong to a log source, so they link there. An empty list
// says nothing is quiet; a list that could not be read says it is unknown, never "nothing". An
// incident is recorded either way: the copy says only that alerts are held back.

import Link from "next/link";
import { useState } from "react";
import { useTranslations } from "next-intl";

import { useSession } from "@/components/auth/session";
import { ChangeStatus, type ChangeOutcome } from "@/components/ui/ChangeStatus";
import { TableScroll } from "@/components/ui/TableScroll";
import { UtcTime } from "@/components/ui/UtcTime";
import { suppressionApi, type Suppression } from "@/lib/api";
import { useUntil } from "@/lib/useUntil";
import { enumLabel, type EnumTranslator } from "@/i18n/enumLabels";
import { asTextTranslator } from "@/i18n/textTranslator";
import {
  SUPPRESS_PERMISSION,
  canSuppress,
  detectorGroup,
  shortId,
  readFailure,
  suppressFailure,
  type ReadFailure,
} from "@/lib/suppression";

import "@/components/ui/controls.css";
import "./alertPolicy.css";

/** The route that owns a subject: a log source for three families, the watch page for the fourth. */
export function subjectHref(row: Pick<Suppression, "family" | "subject_id">): string {
  return row.family === "property_watch"
    ? `/behavior/watches/${row.subject_id}`
    : `/log-sources/${row.subject_id}`;
}

export function QuietPeriods({
  initial,
  failure,
}: {
  initial: Suppression[] | null;
  failure: ReadFailure | null;
}) {
  const t = useTranslations("suppression");
  const say = asTextTranslator(useTranslations());
  const enums = useTranslations("enums") as unknown as EnumTranslator;
  const until = useUntil();
  const session = useSession();
  const [rows, setRows] = useState<Suppression[] | null>(initial);
  const [problem, setProblem] = useState<ReadFailure | null>(failure);
  const [reloading, setReloading] = useState(false);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [rowOutcome, setRowOutcome] = useState<ChangeOutcome>({ kind: "idle" });
  const [flash, setFlash] = useState<{ kind: "ok" | "crit"; text: string } | null>(null);

  const me = session.me;
  const permissions = me ? [...me.permissions, ...me.effective_permissions] : [];
  const known = session.status === "ready" || session.status === "error";
  const canEnd = known && canSuppress(permissions);

  async function reload(): Promise<boolean> {
    setReloading(true);
    try {
      setRows(await suppressionApi.list(true));
      setProblem(null);
      return true;
    } catch (err) {
      setProblem(readFailure(err, say, "read:alerts"));
      return false;
    } finally {
      setReloading(false);
    }
  }

  const subjectName = (r: Suppression) =>
    r.subject_name ??
    (r.family === "property_watch" ? t("scope.removedWatch") : t("scope.removedSource"));
  const detectorName = (r: Suppression) =>
    r.detector_type === null
      ? t("scope.everyDetector")
      : enumLabel(enums, detectorGroup(r.family), r.detector_type);

  async function end(row: Suppression) {
    if (pending || row.id === null) return;
    setPending(true);
    setRowOutcome({ kind: "pending" });
    try {
      await suppressionApi.end(row.id);
      setConfirming(null);
      setRowOutcome({ kind: "idle" });
      const ok = await reload();
      const text = t("quiet.ended", { name: subjectName(row), detector: detectorName(row) });
      setFlash(ok ? { kind: "ok", text } : { kind: "crit", text: t("quiet.reloadFailed", { text }) });
    } catch (err) {
      const failure = suppressFailure(err, say);
      if (failure.gone) {
        setConfirming(null);
        setRowOutcome({ kind: "idle" });
        await reload();
        setFlash({ kind: "crit", text: t("banner.endGone") });
      } else {
        setRowOutcome({ kind: failure.kind, text: failure.text });
      }
    } finally {
      setPending(false);
    }
  }

  return (
    <section aria-labelledby="quiet-periods">
      <h2 className="section-title" id="quiet-periods">
        {t("quiet.heading")}
      </h2>
      <p className="subtitle quiet-lead">{t("quiet.lead")}</p>

      {flash && (
        <div
          className={`banner outage-note ${flash.kind}`}
          role={flash.kind === "crit" ? "alert" : "status"}
        >
          <p>{flash.text}</p>
        </div>
      )}

      {problem && problem.kind === "forbidden" && (
        <div className="notice">
          <p>
            <strong>{t("quiet.forbiddenTitle")}</strong>
          </p>
          <p>
            {t("quiet.forbiddenHelp", {
              permission: problem.permission.includes(":") ? problem.permission : "read:alerts",
            })}
          </p>
        </div>
      )}
      {problem && problem.kind === "error" && (
        <div className="notice" role="alert">
          <p>
            <strong>{t("quiet.failedTitle")}</strong>
          </p>
          <p>{t("quiet.failedHelp", { reason: problem.text })}</p>
          <div className="actions">
            <button
              type="button"
              className="action secondary"
              disabled={reloading}
              onClick={() => void reload()}
            >
              {reloading ? t("quiet.reloading") : t("quiet.retry")}
            </button>
          </div>
        </div>
      )}

      {rows && !problem && known && !canEnd && rows.some((r) => !r.read_only) && (
        <p className="subtitle" role="note">
          {t("quiet.readOnly", { permission: SUPPRESS_PERMISSION })}
        </p>
      )}

      {rows && rows.length === 0 && !problem && (
        <div className="notice">
          <p>
            <strong>{t("quiet.empty")}</strong>
          </p>
          <p>{t("quiet.emptyHelp")}</p>
        </div>
      )}

      {rows && rows.length > 0 && (
        <TableScroll label={t("quiet.label")}>
          <table className="fleet-table outage-table quiet-table">
            <caption className="sr-only">{t("quiet.caption")}</caption>
            <thead>
              <tr>
                <th scope="col">{t("quiet.subject")}</th>
                <th scope="col">{t("quiet.detector")}</th>
                <th scope="col">{t("quiet.kind")}</th>
                <th scope="col">{t("quiet.owner")}</th>
                <th scope="col">{t("quiet.reason")}</th>
                <th scope="col">{t("quiet.ends")}</th>
                <th scope="col" className="num">
                  {t("quiet.recorded")}
                </th>
                {canEnd && <th scope="col">{t("quiet.actions")}</th>}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => {
                const key = r.id ?? `maintenance-${r.subject_id}-${i}`;
                const name = subjectName(r);
                const detector = detectorName(r);
                const asking = r.id !== null && confirming === r.id;
                return (
                  <QuietRow
                    key={key}
                    row={r}
                    name={name}
                    detector={detector}
                    canEnd={canEnd}
                    asking={asking}
                    pending={pending}
                    outcome={asking ? rowOutcome : { kind: "idle" }}
                    ends={until(r.ends_at)}
                    onAsk={() => {
                      setRowOutcome({ kind: "idle" });
                      setFlash(null);
                      setConfirming(r.id);
                    }}
                    onKeep={() => {
                      setConfirming(null);
                      setRowOutcome({ kind: "idle" });
                    }}
                    onEnd={() => void end(r)}
                  />
                );
              })}
            </tbody>
          </table>
        </TableScroll>
      )}
    </section>
  );
}

function QuietRow({
  row,
  name,
  detector,
  canEnd,
  asking,
  pending,
  outcome,
  ends,
  onAsk,
  onKeep,
  onEnd,
}: {
  row: Suppression;
  name: string;
  detector: string;
  canEnd: boolean;
  asking: boolean;
  pending: boolean;
  outcome: ChangeOutcome;
  ends: string | null;
  onAsk: () => void;
  onKeep: () => void;
  onEnd: () => void;
}) {
  const t = useTranslations("suppression");
  const gone = row.subject_name === null;
  const columns = canEnd ? 8 : 7;
  return (
    <>
      <tr>
        <th scope="row" className="cell-source">
          {gone ? (
            <span className="muted">{name}</span>
          ) : (
            <Link href={subjectHref(row)}>{name}</Link>
          )}
          <span className="cell-sub">
            {t(`family.${row.family}`)}
            {gone && <> · {t("scope.removedId", { id: shortId(row.subject_id) })}</>}
          </span>
        </th>
        <td>
          {row.detector_type === null ? <span className="muted">{detector}</span> : detector}
        </td>
        <td className="nowrap">
          <span className="pill">{t(`kind.${row.kind}` as "kind.ANALYST")}</span>
          {row.reference && <span className="cell-sub">{row.reference}</span>}
          {row.read_only && <span className="cell-sub">{t("quiet.maintenanceNote")}</span>}
        </td>
        <td>{row.owner ?? <span className="muted">—</span>}</td>
        <td className="quiet-reason-cell">{row.reason ?? <span className="muted">—</span>}</td>
        <td>
          {row.ends_at ? (
            <>
              <UtcTime value={row.ends_at} variant="compact" className="nowrap" />
              {ends && <span className="cell-sub">{ends}</span>}
            </>
          ) : (
            <span className="muted">{t("quiet.openEnded")}</span>
          )}
        </td>
        <td className="num">
          {row.hit_count === null ? (
            <span className="muted" title={t("quiet.notCounted")}>
              —
            </span>
          ) : (
            row.hit_count
          )}
        </td>
        {canEnd && (
          <td className="cell-actions">
            {row.read_only ? (
              <span className="muted">—</span>
            ) : (
              <div className="actions">
                <button
                  type="button"
                  className="action secondary"
                  aria-label={t("quiet.endAria", { name, detector })}
                  disabled={pending}
                  onClick={onAsk}
                >
                  {t("quiet.end")}
                </button>
              </div>
            )}
          </td>
        )}
      </tr>
      {asking && (
        <tr className="status-row">
          <td colSpan={columns}>
            <div className="rule-form outage-form">
              <div className="impact" role="group" aria-label={t("banner.endTitle")}>
                <p className="impact-title">{t("banner.endTitle")}</p>
                <ul className="impact-lines">
                  <li>{t("banner.endLines", { scope: `${name}, ${detector}` })}</li>
                </ul>
              </div>
              <div className="actions">
                <button type="button" className="action" disabled={pending} onClick={onEnd}>
                  {pending ? t("banner.ending") : t("banner.endConfirm")}
                </button>
                <button type="button" className="action secondary" disabled={pending} onClick={onKeep}>
                  {t("banner.endKeep")}
                </button>
              </div>
              <ChangeStatus outcome={outcome} />
            </div>
          </td>
        </tr>
      )}
    </>
  );
}
