"use client";

// Outage policy (UR.5.1): which outage policy applies, and how many overrides? The default
// leads; only the classes that depart from it, or hold source overrides, are listed.

import { MODE_LABELS, defaultGraceText } from "@/app/admin/outage-policy/outage";
import { useApiResource } from "@/hooks/useApiResource";
import { homeReads } from "@/lib/api";
import { WidgetFrame } from "@/components/home/WidgetFrame";
import { Unmeasured } from "@/components/home/WidgetParts";
import { TableScroll } from "@/components/ui/TableScroll";

import { outageView } from "./derive";

import "./catalog.css";

const n = (v: number) => v.toLocaleString("en-US");

export function OutagePolicy() {
  const read = homeReads.outageSummary("24h");
  const state = useApiResource(read.key, read.fetch);

  return (
    <WidgetFrame
      id="outage-policy"
      title="Outage policy"
      windowText="now"
      link={{ href: "/admin/outage-policy", label: "Policy" }}
      state={state}
      what="the outage policy"
      permission="read:fleet"
    >
      {({ data }) => {
        const view = outageView(data);
        const grace = defaultGraceText(data);
        const mode = MODE_LABELS[data.outage_default] ?? null;
        return (
          <>
            <p className="cw-figure">
              {mode ? (
                <>
                  {mode}
                  <span className="cw-unit">
                    by default
                    {grace ? `, silence grace ${grace}` : ""}
                  </span>
                </>
              ) : (
                <>
                  <Unmeasured reason="The default outage mode was not returned" />
                  <span className="cw-unit">The default outage mode was not returned</span>
                </>
              )}
            </p>
            {data.classes.length === 0 ? (
              <p className="tw-state">
                No source class exists yet, so there are no overrides to count.
              </p>
            ) : (
              <p className="tw-lead">
                <span className="num">{n(view.overrides)}</span> source{" "}
                {view.overrides === 1 ? "override" : "overrides"}
                <span className="muted">
                  {" · "}
                  {n(view.recordOnlyClasses)} {view.recordOnlyClasses === 1 ? "class" : "classes"}{" "}
                  record only
                </span>
              </p>
            )}
            {data.classes.length === 0 ? null : view.rows.length === 0 ? (
              <p className="tw-state">
                No class departs from the default and no source overrides it.
              </p>
            ) : (
              <TableScroll label="Classes that depart from the default policy">
                <table className="tw-table cw-table">
                  <colgroup>
                    <col />
                    <col className="tw-col-badge" />
                    <col className="tw-col-count" />
                  </colgroup>
                  <thead>
                    <tr>
                      <th scope="col">Class</th>
                      <th scope="col">Mode</th>
                      <th scope="col" className="num">
                        Overrides
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {view.rows.map((r) => (
                      <tr key={r.key}>
                        <td className="tw-name">{r.label}</td>
                        <td>{MODE_LABELS[r.mode]}</td>
                        <td className="num">{n(r.overrides)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableScroll>
            )}
          </>
        );
      }}
    </WidgetFrame>
  );
}
