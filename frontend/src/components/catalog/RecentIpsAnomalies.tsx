"use client";

// Recent IPS anomalies (UR.5.1): the latest active anomalies of IPS entities and of IPS sources,
// merged by detection time. Each row is in its detector's own unit (events per bucket, distinct
// ports, a share), never EPS: IPS figures are not log-source volume. An empty list is not a clean
// bill: an IPS subject without a reliable baseline is not judged.

import Link from "next/link";

import { WidgetFrame } from "@/components/home/WidgetFrame";
import { Unmeasured, When } from "@/components/home/WidgetParts";
import { TableScroll } from "@/components/ui/TableScroll";
import { statePillText } from "@/components/waf/wafLabels";
import { useApiResource } from "@/hooks/useApiResource";
import { homeReads, sevTone } from "@/lib/api";
import { stateTone } from "@/lib/behavior";
import { combine2 } from "@/lib/home/combine";
import { useExpanded } from "@/widgets/runtime";
import { UtcTime } from "@/components/ui/UtcTime";


import { IPS_ANOMALIES_LIMIT, mergeIpsAnomalies } from "./ipsAnomalies";

import "./catalog.css";

export const IPS_EMPTY =
  "No active IPS anomalies. An IPS entity or source without a reliable baseline is not judged, so this is not a clean bill.";

export function RecentIpsAnomalies({ id = "single" }: { id?: string } = {}) {
  const expanded = useExpanded();
  const entityRead = homeReads.activeIpsAnomalies("IPS_ENTITY", IPS_ANOMALIES_LIMIT);
  const sourceRead = homeReads.activeIpsAnomalies("IPS_SOURCE", IPS_ANOMALIES_LIMIT);
  const entities = useApiResource(entityRead.key, entityRead.fetch);
  const sources = useApiResource(sourceRead.key, sourceRead.fetch);
  const state = combine2(entities, sources);
  const view =
    state.status === "ok" ? mergeIpsAnomalies(state.data[0], state.data[1]) : null;

  return (
    <WidgetFrame
      id={`ips-anomalies-${id}`}
      title="Recent IPS anomalies"
      windowText="active now"
      badge={view?.badge}
      link={{
        href: "/anomalies?subject_kind=IPS_ENTITY&active_only=true",
        label: "Entities",
        ariaLabel: "View active IPS entity anomalies",
      }}
      state={state}
      what="the active IPS anomalies"
      permission="read:anomalies"
    >
      {() => {
        if (!view) return null;
        if (view.rows.length === 0) return <p className="tw-state">{IPS_EMPTY}</p>;
        return (
          <>
            <TableScroll label="Recent IPS anomalies">
              <table className="tw-table" aria-label="Recent IPS anomalies">
                <colgroup>
                  <col className="tw-col-time tw-unfold" />
                  <col />
                  <col className="tw-col-badge tw-unfold" />
                  <col className="cw-col-figures" />
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
                      Observed → expected
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
                        <Link href={r.href} title={r.subject}>
                          {r.subject}
                        </Link>
                        <span className="tw-sub" title={r.sourceName}>
                          {r.typeLabel} · {r.sourceName}
                        </span>
                        <span className="tw-sub tw-wrap tw-fold-line">
                          <span className={`tw-state-word ${sevTone(r.severity)}`}>
                            {r.severity}
                          </span>
                          {" · "}
                          <span className={`tw-state-word ${stateTone(r.state)}`}>
                            {statePillText(r.state)}
                          </span>
                          {" · "}
                          <UtcTime value={r.detectedAt} variant="compact" />
                        </span>
                      </td>
                      <td className="tw-unfold">
                        <span className={`pill ${sevTone(r.severity)}`}>{r.severity}</span>
                        <span className={`tw-sub tw-wrap tw-state-word ${stateTone(r.state)}`}>
                          {statePillText(r.state)}
                        </span>
                      </td>
                      <td className="num">
                        {r.observed === null ? (
                          <Unmeasured reason="The unit of this detector is not known to this build" />
                        ) : (
                          <>
                            {r.observed}
                            <span className="muted"> → </span>
                            {r.expected}
                            {r.unitWords ? <span className="tw-sub tw-wrap">{r.unitWords}</span> : null}
                          </>
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
            <p className="tw-note">
              {view.shown
                ? `${
                    expanded ? view.shown.replace("the latest ", "") : view.shown
                  }. ${
                    expanded
                      ? `The widget reads the latest ${IPS_ANOMALIES_LIMIT} of each kind; the Anomalies page lists the rest. `
                      : ""
                  }`
                : ""}
              <Link href="/anomalies?subject_kind=IPS_SOURCE&active_only=true" className="tw-link">
                Source-level anomalies
              </Link>
            </p>
          </>
        );
      }}
    </WidgetFrame>
  );
}
