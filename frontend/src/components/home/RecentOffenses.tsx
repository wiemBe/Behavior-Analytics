"use client";

// Recent offenses (UR.4.3): the most recently updated open offenses.
//
// Magnitude is a ten-segment bar and the number; QRadar's magnitude can be null,
// and then there is a dash and no bar at all (an empty bar would read as 0). With no
// snapshot the widget says NOT_COLLECTED, which is not "no open offenses".

import Link from "next/link";

import { TableScroll } from "@/components/ui/TableScroll";
import { useApiResource } from "@/hooks/useApiResource";
import { homeReads } from "@/lib/api";
import { MAGNITUDE_SEGMENTS, RECENT_OFFENSES_LIMIT, recentOffenses } from "@/lib/home/recentOffenses";

import { useExpanded } from "@/widgets/runtime";

import { secondary, WidgetFrame, WidgetLoading, WidgetQualifiers } from "./WidgetFrame";
import { Unmeasured, When } from "./WidgetParts";

function MagnitudeBar({ segments }: { segments: number }) {
  return (
    <svg className="tw-bar" viewBox="0 0 100 8" preserveAspectRatio="none" aria-hidden="true" focusable="false">
      {Array.from({ length: MAGNITUDE_SEGMENTS }, (_, i) => (
        <rect
          key={i}
          className={i < segments ? "tw-seg on" : "tw-seg"}
          x={i * 10}
          y={0}
          width={8}
          height={8}
        />
      ))}
    </svg>
  );
}

export function RecentOffenses() {
  const expanded = useExpanded();
  const openRead = homeReads.recentOffenses(RECENT_OFFENSES_LIMIT);
  const probeRead = homeReads.offenseProbe();
  const open = useApiResource(openRead.key, openRead.fetch);
  const probe = useApiResource(probeRead.key, probeRead.fetch);

  return (
    <WidgetFrame
      id="recent-offenses"
      title="Recent offenses"
      windowText="open, by last update"
      link={{ href: "/offenses", label: "View all" }}
      state={open}
      what="the open offenses"
      permission="read:offenses"
    >
      {({ data }) => {
          const p = secondary(probe);
          // An empty list needs the probe to say why it is empty.
          if (data.items.length === 0 && p.pending) {
            return <WidgetLoading what="the offense snapshot state" />;
          }
          const view = recentOffenses(data, p.data);
          if (view.qualifiers.length > 0 && view.rows.length === 0) {
            return <WidgetQualifiers qualifiers={view.qualifiers} />;
          }
          if (view.empty !== null) return <p className="tw-state">{view.empty}</p>;
          return (
            <>
              <TableScroll label="Recent offenses">
                <table className="tw-table" aria-label="Recent offenses">
                  <colgroup>
                    <col className="tw-col-time" />
                    <col />
                    <col className="tw-col-magnitude" />
                  </colgroup>
                  <thead>
                    <tr>
                      <th scope="col">Last updated</th>
                      <th scope="col">Offense</th>
                      <th scope="col" className="num">
                        Magnitude
                        <span className="tw-sub tw-wrap">of 10</span>
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {view.rows.map((r) => (
                      <tr key={r.id}>
                        <td>
                          <When iso={r.lastUpdated} />
                        </td>
                        <td className="tw-name">
                          <Link
                            href={r.href}
                            title={r.description ?? `Offense ${r.id}`}
                          >
                            {r.description ?? `Offense ${r.id}`}
                          </Link>
                          <span className="tw-sub">
                            #{r.id} · {r.status}
                          </span>
                        </td>
                        <td className="num">
                          {r.magnitude === null || r.segments === null ? (
                            <Unmeasured reason="Magnitude not recorded" />
                          ) : (
                            <span className="tw-measure">
                              <MagnitudeBar segments={r.segments} />
                              <span>{r.magnitude}</span>
                            </span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableScroll>
              {view.shown && (
                <p className="tw-note">
                  {expanded
                    ? `${view.shown.replace("the latest ", "")}. The widget reads the latest ${RECENT_OFFENSES_LIMIT}; the Offenses page lists the rest.`
                    : `${view.shown}.`}
                </p>
              )}
            </>
          );
      }}
    </WidgetFrame>
  );
}
