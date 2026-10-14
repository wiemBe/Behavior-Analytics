"use client";

// Source classes (UR.5.1): how does each class fare? Sources, mean EPS over 24 h and open
// silence incidents, one row per class that has sources. A class with no complete bucket has
// no EPS (an em dash), and a class with no monitored source has no silence figure.

import { useApiResource } from "@/hooks/useApiResource";
import { homeReads } from "@/lib/api";
import { formatEpsValue } from "@/lib/fleet";
import { partial } from "@/lib/home/qualifiers";
import { WidgetFrame, WidgetQualifiers } from "@/components/home/WidgetFrame";
import { Unmeasured } from "@/components/home/WidgetParts";
import { TableScroll } from "@/components/ui/TableScroll";

import { classRows } from "./derive";

import "./catalog.css";

export function FleetClasses() {
  const read = homeReads.fleetSummary("24h");
  const state = useApiResource(read.key, read.fetch);

  return (
    <WidgetFrame
      id="fleet-classes"
      title="Source classes"
      windowText="24 h"
      link={{ href: "/fleet", label: "Fleet" }}
      state={state}
      what="the fleet summary"
      permission="read:fleet"
    >
      {({ data }) => {
        const { rows, hidden, incomplete } = classRows(data);
        if (rows.length === 0) {
          return (
            <p className="tw-state">
              No source class has a source yet. Sync the log sources to classify them.
            </p>
          );
        }
        const expected = data.classes.reduce((s, c) => s + c.buckets.expected, 0);
        const complete = data.classes.reduce((s, c) => s + c.buckets.complete, 0);
        return (
          <>
            <TableScroll label="Source classes">
              <table className="tw-table cw-table">
                <colgroup>
                  <col />
                  <col className="tw-col-count" />
                  <col className="tw-col-eps" />
                  <col className="tw-col-count" />
                </colgroup>
                <thead>
                  <tr>
                    <th scope="col">Class</th>
                    <th scope="col" className="num">
                      Sources
                    </th>
                    <th scope="col" className="num">
                      Mean EPS
                    </th>
                    <th scope="col" className="num">
                      Silent
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.key}>
                      <td className="tw-name">{r.label}</td>
                      <td className="num">{r.sources.toLocaleString("en-US")}</td>
                      <td className="num">
                        {r.meanEps === null ? (
                          <Unmeasured reason="No complete bucket in this window" />
                        ) : (
                          formatEpsValue(r.meanEps)
                        )}
                      </td>
                      <td className="num">
                        {r.silent === null ? (
                          <Unmeasured reason="No monitored source in this class" />
                        ) : (
                          <span className={r.silent > 0 ? "tw-state-word crit" : undefined}>
                            {r.silent.toLocaleString("en-US")}
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableScroll>
            {incomplete ? (
              <WidgetQualifiers qualifiers={[partial(complete, expected, "intervals")]} />
            ) : null}
            {hidden > 0 ? (
              <p className="tw-note">
                Showing the {rows.length} busiest of {rows.length + hidden} classes.
              </p>
            ) : null}
          </>
        );
      }}
    </WidgetFrame>
  );
}
