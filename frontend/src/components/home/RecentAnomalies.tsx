"use client";

// Recent anomalies (UR.4.3): the latest active anomalies and how many are active.
//
// The badge is the response's `total`, worded "n active". An empty list is not
// "all clear": it says how many sources are not being judged. The unified list
// includes WAF anomalies, which are named by their endpoint.

import Link from "next/link";

import { TableScroll } from "@/components/ui/TableScroll";
import { stateLabel } from "@/components/waf/wafLabels";
import { useApiResource } from "@/hooks/useApiResource";
import { homeReads, sevTone, type SubjectKind } from "@/lib/api";
import { formatRatio, stateTone } from "@/lib/behavior";
import { formatDateTime, formatDateTimeCompact } from "@/lib/health";
import { RECENT_ANOMALIES_LIMIT, recentAnomalies } from "@/lib/home/recentAnomalies";

import { useExpanded } from "@/widgets/runtime";

import { secondary, WidgetFrame, WidgetLoading } from "./WidgetFrame";
import { Unmeasured, When } from "./WidgetParts";

/** Why an empty WAF list is not a clean bill: the sources without a valid profile are not read. */
export const WAF_EMPTY =
  "No active WAF endpoint anomalies. WAF sources without a valid profile are not collected, so they are not judged.";

/**
 * `subject` narrows the list to one kind of subject (the WAF endpoint widget). The default
 * is the Home's list of every active anomaly. The not-judged count is about log sources, so a
 * WAF list neither reads it nor quotes it.
 */
export function RecentAnomalies({ subject }: { subject?: SubjectKind } = {}) {
  const waf = subject === "WAF_ENDPOINT";
  const expanded = useExpanded();
  const listRead = waf
    ? homeReads.activeWafAnomalies(RECENT_ANOMALIES_LIMIT)
    : homeReads.activeAnomalies(RECENT_ANOMALIES_LIMIT);
  const summaryRead = homeReads.anomalySummary();
  const list = useApiResource(listRead.key, listRead.fetch);
  const summary = useApiResource(waf ? null : summaryRead.key, summaryRead.fetch);
  const ok = list.status === "ok" ? recentAnomalies(list.data, secondary(summary).data) : null;

  return (
    <WidgetFrame
      id={waf ? "recent-waf-anomalies" : "recent-anomalies"}
      title={waf ? "Recent WAF anomalies" : "Recent anomalies"}
      windowText="active now"
      badge={ok ? ok.badge : undefined}
      link={
        waf
          ? { href: "/anomalies?subject_kind=WAF_ENDPOINT&active_only=true", label: "View all" }
          : { href: "/anomalies", label: "View all" }
      }
      state={list}
      what={waf ? "the active WAF anomalies" : "the active anomalies"}
      permission="read:anomalies"
    >
      {({ data }) => {
          const sum = secondary(summary);
          // The empty state quotes the not-judged count, so wait for it.
          if (!waf && data.items.length === 0 && sum.pending) {
            return <WidgetLoading what="the not-judged source count" />;
          }
          const view = recentAnomalies(data, sum.data);
          if (waf && data.items.length === 0) return <p className="tw-state">{WAF_EMPTY}</p>;
          if (view.empty !== null) return <p className="tw-state">{view.empty}</p>;
          return (
            <>
              <TableScroll label={waf ? "Recent WAF anomalies" : "Recent anomalies"}>
                <table
                  className="tw-table"
                  aria-label={waf ? "Recent WAF anomalies" : "Recent anomalies"}
                >
                  <colgroup>
                    <col className="tw-col-time tw-unfold" />
                    <col />
                    <col className="tw-col-badge tw-unfold" />
                    <col className="tw-col-ratio" />
                    <col className="tw-col-action" />
                  </colgroup>
                  <thead>
                    <tr>
                      <th scope="col" className="tw-unfold">
                        Detected
                      </th>
                      <th scope="col" className="tw-lead-col">
                        Anomaly
                      </th>
                      <th scope="col" className="tw-unfold">
                        Severity, state
                      </th>
                      <th scope="col" className="num">
                        Deviation
                        <span className="tw-sub tw-wrap">vs expected</span>
                      </th>
                      <th scope="col">
                        <span className="sr-only">Investigate</span>
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {view.rows.map((r) => (
                      <tr key={r.id}>
                        <td className="tw-unfold">
                          <When iso={r.detectedAt} />
                        </td>
                        <td className="tw-name tw-lead-col">
                          <Link href={r.sourceHref} title={r.subject}>
                            {r.subject}
                          </Link>
                          <span className="tw-sub" title={r.sourceName ?? undefined}>
                            {r.typeLabel}
                            {r.sourceName !== null && ` · ${r.sourceName}`}
                          </span>
                          {/* Below the widget's breakpoint the Detected and Severity columns
                              fold into this line; above it the line is hidden. */}
                          <span className="tw-sub tw-wrap tw-fold-line">
                            <span className={`tw-state-word ${sevTone(r.severity)}`}>
                              {r.severity}
                            </span>
                            {" · "}
                            <span className={`tw-state-word ${stateTone(r.state)}`}>
                              {stateLabel(r.state)}
                            </span>
                            {" · "}
                            <time dateTime={r.detectedAt} title={formatDateTime(r.detectedAt)}>
                              {formatDateTimeCompact(r.detectedAt)}
                            </time>
                          </span>
                        </td>
                        <td className="tw-unfold">
                          <span className={`pill ${sevTone(r.severity)}`}>{r.severity}</span>
                          <span className={`tw-sub tw-wrap tw-state-word ${stateTone(r.state)}`}>
                            {stateLabel(r.state)}
                          </span>
                        </td>
                        <td className="num">
                          {r.deviation === null ? (
                            <Unmeasured reason="No deviation ratio" />
                          ) : (
                            formatRatio(r.deviation)
                          )}
                        </td>
                        <td>
                          <Link
                            className="tw-link"
                            href={r.href}
                            aria-label={`Investigate ${r.subject}`}
                          >
                            Investigate
                          </Link>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableScroll>
              {view.shown && (
                <p className="tw-note">
                  {expanded
                    ? `${view.shown.replace("the latest ", "")}. The widget reads the latest ${RECENT_ANOMALIES_LIMIT}; the Anomalies page lists the rest.`
                    : `${view.shown}.`}
                </p>
              )}
            </>
          );
      }}
    </WidgetFrame>
  );
}
