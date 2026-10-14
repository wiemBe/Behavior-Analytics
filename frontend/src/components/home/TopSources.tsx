"use client";

// Top log sources by EPS (UR.4.3): the five busiest sources of the widget's range (24 h by
// default).
//
// Everything is derived in `lib/home/topSources`; this draws it. Three things the
// derivation knows are said in words rather than left to a glyph: an EPS that is
// "at least" because its class's window has gaps, a deviation that is the latest
// bucket's and not the window's mean, and a state the behavior list could not give.

import Link from "next/link";

import { TableScroll } from "@/components/ui/TableScroll";
import { useApiResource } from "@/hooks/useApiResource";
import { homeReads } from "@/lib/api";
import { formatRatio, NOT_MEASURED, stateTone } from "@/lib/behavior";
import { formatEpsValue } from "@/lib/fleet";
import { deviationGapText, epsShares, topSources } from "@/lib/home/topSources";
import { stateLabel } from "@/components/waf/wafLabels";
import { RANGE_TEXT, useEffectiveRange, type RangedProps } from "@/widgets/runtime";

import { secondary, WidgetFrame, WidgetLoading } from "./WidgetFrame";
import { Unmeasured } from "./WidgetParts";

function EpsBar({ share }: { share: number | null }) {
  if (share === null) return null;
  return (
    <svg className="tw-bar" viewBox="0 0 100 8" preserveAspectRatio="none" aria-hidden="true" focusable="false">
      <rect className="tw-bar-track" x={0} y={0} width={100} height={8} />
      <rect className="tw-bar-fill" x={0} y={0} width={share * 100} height={8} />
    </svg>
  );
}

export function TopSources({ range }: RangedProps = {}) {
  const window = useEffectiveRange(range);
  const windowText = RANGE_TEXT[window];
  const fleetRead = homeReads.fleetSummary(window);
  const behaviorRead = homeReads.sourceBehaviors();
  const fleet = useApiResource(fleetRead.key, fleetRead.fetch);
  const behaviors = useApiResource(behaviorRead.key, behaviorRead.fetch);

  return (
    <WidgetFrame
      id="top-sources"
      title="Top log sources by EPS"
      windowText={windowText}
      link={{ href: "/behavior/sources", label: "View all" }}
      state={fleet}
      what="the fleet summary"
      permission="read:fleet"
    >
      {({ data }) => {
          const beh = secondary(behaviors);
          if (beh.pending) return <WidgetLoading what="source states" />;
          const rows = topSources(data, beh.data);
          if (rows.length === 0) {
            return (
              <p className="tw-state">
                No log source has a complete bucket in the last {windowText}, so none can be ranked
                by EPS.
              </p>
            );
          }
          const shares = epsShares(rows);
          return (
            <>
              <TableScroll label="Top log sources by EPS">
                <table className="tw-table">
                  <colgroup>
                    <col />
                    <col className="tw-col-eps" />
                    <col className="tw-col-ratio" />
                    <col className="tw-col-badge tw-unfold" />
                  </colgroup>
                  <thead>
                    <tr>
                      <th scope="col">Source</th>
                      <th scope="col" className="num">
                        Mean EPS
                      </th>
                      <th scope="col" className="num">
                        Deviation
                        <span className="tw-sub tw-wrap">{rows[0].deviationBasis}</span>
                      </th>
                      <th scope="col" className="tw-unfold">
                        State
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r, i) => (
                      <tr key={r.id}>
                        <td className="tw-name">
                          <Link href={`/behavior/sources/${r.id}`} title={r.name}>
                            {r.name}
                          </Link>
                          <span className="tw-sub tw-wrap">
                            {r.classLabel}
                            <span className="tw-fold">
                              {r.state === null ? (
                                "State unavailable"
                              ) : (
                                <span className={`tw-state-word ${stateTone(r.state)}`}>
                                  {stateLabel(r.state)}
                                </span>
                              )}
                            </span>
                          </span>
                        </td>
                        <td className="num">
                          <span className="tw-measure stack">
                            <span>
                              {formatEpsValue(r.eps) ?? NOT_MEASURED}
                              <span className="unit"> EPS</span>
                            </span>
                            <EpsBar share={shares[i]} />
                          </span>
                          {r.epsIsLowerBound && r.eps !== null && (
                            <span
                              className="tw-sub"
                              title={`The class's ${windowText} window has missing or partial intervals, so this is a floor.`}
                            >
                              at least
                            </span>
                          )}
                        </td>
                        <td className="num">
                          {r.deviation === null ? (
                            <Unmeasured reason={r.deviationGap ? deviationGapText(r.deviationGap) : null} />
                          ) : (
                            formatRatio(r.deviation)
                          )}
                        </td>
                        <td className="tw-unfold">
                          {r.state === null ? (
                            <Unmeasured reason="State unavailable" />
                          ) : (
                            <span className={`tw-state-word ${stateTone(r.state)}`}>
                              {stateLabel(r.state)}
                            </span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableScroll>
              {beh.data === null && (
                <p className="tw-note">
                  Deviation and state are unavailable: the source behavior list is not readable
                  ({beh.gap}).
                </p>
              )}
            </>
          );
      }}
    </WidgetFrame>
  );
}
