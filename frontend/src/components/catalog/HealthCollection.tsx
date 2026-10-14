"use client";

// Collection health (UR.5.1): are the scheduled searches running on time? One row per search
// family: its cadence, when it last ran and how late that run is against its cadence. A family
// that has not run since the upgrade has no lateness; it is an em dash, not "on time".

import { useApiResource } from "@/hooks/useApiResource";
import { homeReads } from "@/lib/api";
import { WidgetFrame } from "@/components/home/WidgetFrame";
import { Unmeasured, When } from "@/components/home/WidgetParts";

import { cadenceRows, lateText } from "./derive";

import "./catalog.css";

export function HealthCollection() {
  const read = homeReads.searchCadence();
  const state = useApiResource(read.key, read.fetch);

  return (
    <WidgetFrame
      id="health-collection"
      title="Scheduled searches"
      windowText="latest run"
      link={{ href: "/admin/search-cadence", label: "Cadence" }}
      state={state}
      what="the search cadence"
      permission="read:search_cadence"
    >
      {({ data }) => {
        if (data.length === 0) {
          return (
            <p className="tw-state">
              No search family was returned. The cadence migration seeds them, so check that the
              database migrations have been applied.
            </p>
          );
        }
        const rows = cadenceRows(data, Date.now());
        const late = rows.filter((r) => r.late).length;
        return (
          <>
            <p className="tw-lead">
              <span className="num">
                {late.toLocaleString("en-US")} of {rows.length.toLocaleString("en-US")}
              </span>{" "}
              families late
              {rows.some((r) => r.lateSeconds === null) ? (
                <span className="muted"> · some have not run since the upgrade</span>
              ) : null}
            </p>
            <table className="tw-table cw-table">
              <caption className="sr-only">Search families, cadence and last run</caption>
              <colgroup>
                <col />
                <col className="tw-col-badge" />
                <col className="tw-col-time" />
                <col className="tw-col-magnitude" />
              </colgroup>
              <thead>
                <tr>
                  <th scope="col">Family</th>
                  <th scope="col" className="num">
                    Cadence
                  </th>
                  <th scope="col">Last run</th>
                  <th scope="col" className="num">
                    Late by
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.family}>
                    <td className="tw-name cw-wrap">
                      <span title={r.name}>{r.name}</span>
                    </td>
                    <td className="num cw-nowrap">{r.cadence}</td>
                    <td>
                      <When iso={r.lastRunAt} />
                    </td>
                    <td className="num cw-nowrap">
                      {r.lateSeconds === null ? (
                        <Unmeasured reason="This family has not run since the upgrade" />
                      ) : (
                        <span className={r.late ? "tw-state-word warn" : "tw-state-word"}>
                          {lateText(r)}
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        );
      }}
    </WidgetFrame>
  );
}
