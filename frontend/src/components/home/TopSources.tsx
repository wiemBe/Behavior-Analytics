"use client";

// Top log sources by EPS (UR.4.3): the five busiest sources of the widget's range (24 h by
// default).
//
// Everything is derived in `lib/home/topSources`; this draws it. Three things the
// derivation knows are said in words rather than left to a glyph: an EPS that is
// "at least" because its class's window has gaps, a deviation that is the latest
// bucket's and not the window's mean, and a state the behavior list could not give.

import { useTranslations } from "next-intl";
import Link from "next/link";

import { TableScroll } from "@/components/ui/TableScroll";
import { useApiResource } from "@/hooks/useApiResource";
import { useEnumLabels } from "@/i18n/enumLabels";
import { asTextTranslator } from "@/i18n/textTranslator";
import { useMeasures } from "@/i18n/measures";
import { homeReads } from "@/lib/api";
import { stateTone } from "@/lib/behavior";
import { epsShares, topSources } from "@/lib/home/topSources";
import { useWidgetCopy } from "@/widgets/copy";
import { useEffectiveRange, useExpanded, type RangedProps } from "@/widgets/runtime";

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

export function TopSources({ range, expanded: expandedProp }: RangedProps = {}) {
  const t = useTranslations("home.topSources");
  const units = useTranslations("units");
  const say = asTextTranslator(useTranslations());
  const measures = useMeasures();
  const { label, statePill } = useEnumLabels();
  const copy = useWidgetCopy();
  const expanded = useExpanded(expandedProp);
  const window = useEffectiveRange(range);
  const windowText = copy.range(window);
  const fleetRead = homeReads.fleetSummary(window);
  const behaviorRead = homeReads.sourceBehaviors();
  const fleet = useApiResource(fleetRead.key, fleetRead.fetch);
  const behaviors = useApiResource(behaviorRead.key, behaviorRead.fetch);

  return (
    <WidgetFrame
      id="top-sources"
      title={t("title")}
      windowText={windowText}
      link={{ href: "/behavior/sources", label: t("link") }}
      state={fleet}
      what={t("what")}
      permission="read:fleet"
    >
      {({ data }) => {
          const beh = secondary(behaviors, say);
          if (beh.pending) return <WidgetLoading what={t("statesWhat")} />;
          const rows = topSources(data, beh.data, expanded ? Infinity : undefined);
          if (rows.length === 0) {
            return (
              <p className="tw-state">{t("empty", { window: windowText })}</p>
            );
          }
          const shares = epsShares(rows);
          return (
            <>
              <TableScroll label={t("title")}>
                <table className="tw-table" aria-label={t("title")}>
                  <colgroup>
                    <col />
                    <col className="tw-col-eps" />
                    <col className="tw-col-ratio" />
                    <col className="tw-col-badge tw-unfold" />
                  </colgroup>
                  <thead>
                    <tr>
                      <th scope="col">{t("source")}</th>
                      <th scope="col" className="num">
                        {t("meanEps")}
                      </th>
                      <th scope="col" className="num">
                        {t("deviation")}
                        <span className="tw-sub tw-wrap">{t("latestBucket")}</span>
                      </th>
                      <th scope="col" className="tw-unfold">
                        {t("state")}
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
                            {label("sourceClass", r.sourceClass)}
                            <span className="tw-fold">
                              {r.state === null ? (
                                t("stateUnavailable")
                              ) : (
                                <span className={`tw-state-word ${stateTone(r.state)}`}>
                                  {statePill(r.state)}
                                </span>
                              )}
                            </span>
                          </span>
                        </td>
                        <td className="num">
                          <span className="tw-measure stack">
                            <span>
                              {measures.eps(r.eps)}
                              <span className="unit">{measures.gap}{units("eps")}</span>
                            </span>
                            <EpsBar share={shares[i]} />
                          </span>
                          {r.epsIsLowerBound && r.eps !== null && (
                            <span
                              className="tw-sub"
                              title={t("floorTitle", { window: windowText })}
                            >
                              {t("atLeast")}
                            </span>
                          )}
                        </td>
                        <td className="num">
                          {r.deviation === null ? (
                            <Unmeasured reason={r.deviationGap ? t(`gap.${r.deviationGap}`) : null} />
                          ) : (
                            measures.ratio(r.deviation)
                          )}
                        </td>
                        <td className="tw-unfold">
                          {r.state === null ? (
                            <Unmeasured reason={t("stateUnavailable")} />
                          ) : (
                            <span className={`tw-state-word ${stateTone(r.state)}`}>
                              {statePill(r.state)}
                            </span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableScroll>
              {expanded && (
                <p className="tw-note">
                  {t("expandedNote", {
                    shown: measures.count(rows.length),
                    total: measures.count(rows.length),
                  })}
                </p>
              )}
              {beh.data === null && (
                <p className="tw-note">{t("noBehavior", { gap: beh.gap ?? "" })}</p>
              )}
            </>
          );
      }}
    </WidgetFrame>
  );
}
