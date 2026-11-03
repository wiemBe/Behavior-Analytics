"use client";

// QRadar's own system notifications of one console over a range: one row per host and
// notification name, busiest first, with the groups below the cut counted on a final line. A
// range with no notification says so in words, but only when the range was looked at: no
// measured window is "not collected", never "nothing happened".

import { useTranslations } from "next-intl";

import { TableScroll } from "@/components/ui/TableScroll";
import { UtcTime } from "@/components/ui/UtcTime";
import type { QRadarConsoleLatest } from "@/lib/api";
import { formatCount } from "@/lib/format";
import { formatDuration } from "@/lib/time";

/** A range counts as covered when its measured windows span at least this share of it. */
const COVERED_SHARE = 0.9;

export function NotificationsTable({
  console_,
  hours,
}: {
  console_: QRadarConsoleLatest;
  hours: number;
}) {
  const t = useTranslations("siemLoad.detail.notices");
  const state = useTranslations("siemLoad.detail.health.state");
  const { notifications, notification_list: list, window } = console_;

  if (list === null) {
    return (
      <div className="notice" role="status">
        <p>
          <span className="sl-gap">
            <span className={`pill${notifications.state === "FAILED" ? " crit" : ""}`}>
              {state(notifications.state === "FAILED" ? "FAILED" : "NOT_COLLECTED")}
            </span>
            <span className="sl-reason">{notifications.reason ?? t("notRead")}</span>
          </span>
        </p>
      </div>
    );
  }

  const cadence = window?.cadence_seconds ?? null;
  const covered =
    cadence !== null && list.windows !== null ? list.windows * cadence : null;
  const short = covered !== null && covered < hours * 3600 * COVERED_SHARE;
  const newestProblem =
    notifications.state === "FAILED" || notifications.state === "NOT_COLLECTED";

  return (
    <>
      {notifications.state === "STALE" || notifications.state === "PARTIAL" ? (
        <p className="banner warn" role="status">
          {notifications.reason}
        </p>
      ) : null}
      {newestProblem ? (
        <p className="banner warn" role="status">
          {notifications.reason ? `${notifications.reason} ` : ""}
          {t("newestFailed")}
        </p>
      ) : null}
      {list.windows !== null && list.covered_from && list.covered_until ? (
        <p className="sl-asof">
          {t.rich("coverage", {
            windows: list.windows,
            cadence: cadence !== null ? formatDuration(cadence) : "—",
            from: () => <UtcTime value={list.covered_from} />,
            to: () => <UtcTime value={list.covered_until} />,
          })}
          {short && covered !== null ? ` ${t("shortCoverage", { covered: formatDuration(covered), hours })}` : null}
        </p>
      ) : null}
      {list.entries.length === 0 && list.remainder === null ? (
        <div className="notice" role="status">
          <p>
            <strong>{t("none", { hours })}</strong>
          </p>
        </div>
      ) : (
        <>
          <TableScroll label={t("label", { name: console_.instance_name })}>
            <table className="sl-table">
              <caption className="sr-only">{t("label", { name: console_.instance_name })}</caption>
              <thead>
                <tr>
                  <th scope="col">{t("qid")}</th>
                  <th scope="col">{t("host")}</th>
                  <th scope="col" className="num">
                    {t("events")}
                  </th>
                  <th scope="col" className="num">
                    {t("severity")}
                  </th>
                  <th scope="col" className="num">
                    {t("lastSeen")}
                  </th>
                </tr>
              </thead>
              <tbody>
                {list.entries.map((entry) => (
                  <tr key={`${entry.host}|${entry.qid_name}`}>
                    <th scope="row">{entry.qid_name}</th>
                    <td>
                      <span className="sl-host">{entry.host}</span>
                    </td>
                    <td className="num">{formatCount(entry.events)}</td>
                    <td className="num">{entry.max_severity === null ? "—" : entry.max_severity}</td>
                    <td className="num cell-when">
                      <UtcTime value={entry.last_seen} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableScroll>
          {list.remainder ? (
            <p className="sl-hint">
              {t("remainder", { events: list.remainder.events, groups: list.remainder.groups })}
            </p>
          ) : null}
        </>
      )}
    </>
  );
}
