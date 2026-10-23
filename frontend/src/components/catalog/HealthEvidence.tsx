"use client";

// Evidence health (UR.5.1): is evidence collection keeping up? Two counts of anomalies, from
// the anomaly summary. A failed count is a problem to look at, so it leads the drill-down.

import { useApiResource } from "@/hooks/useApiResource";
import { homeReads } from "@/lib/api";
import { WidgetFrame } from "@/components/home/WidgetFrame";
import { formatCount } from "@/lib/format";

import "./catalog.css";

const n = (v: number) => formatCount(v);

export function HealthEvidence() {
  const read = homeReads.anomalySummary();
  const state = useApiResource(read.key, read.fetch);

  return (
    <WidgetFrame
      id="health-evidence"
      title="Evidence collection"
      windowText="now"
      link={{ href: "/anomalies?evidence_status=FAILED", label: "Failed" }}
      state={state}
      what="the evidence counts"
      permission="read:anomalies"
    >
      {({ data }) => (
        <>
          <table className="tw-table cw-table">
            <caption className="sr-only">Anomalies by evidence collection status</caption>
            <colgroup>
              <col />
              <col className="tw-col-count" />
            </colgroup>
            <thead>
              <tr>
                <th scope="col">Evidence status</th>
                <th scope="col" className="num">
                  Anomalies
                </th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>Pending</td>
                <td className="num">{n(data.evidence_pending)}</td>
              </tr>
              <tr>
                <td>
                  <span className={data.evidence_failed > 0 ? "tw-state-word crit" : undefined}>
                    Failed
                  </span>
                </td>
                <td className="num">{n(data.evidence_failed)}</td>
              </tr>
            </tbody>
          </table>
          <p className="tw-note">
            {data.evidence_failed > 0
              ? "Evidence could not be collected for the failed anomalies; open them to see why."
              : "No evidence collection has failed."}
          </p>
        </>
      )}
    </WidgetFrame>
  );
}
