"use client";

// Source classes (UR.5.1): how does each class fare? Sources, mean EPS over the widget's range and open
// silence incidents, one row per class that has sources. A class with no complete bucket has
// no EPS (an em dash), and a class with no monitored source has no silence figure.

import { useTranslations } from "next-intl";

import { useApiResource } from "@/hooks/useApiResource";
import { homeReads } from "@/lib/api";
import { fleetDrillHref, formatEpsValue } from "@/lib/fleet";
import { plainCount, useFleetText } from "@/lib/fleetText";
import { formatCount } from "@/lib/format";
import { partial } from "@/lib/home/qualifiers";
import { useWidgetCopy } from "@/widgets/copy";
import { useEffectiveRange, useExpanded, type RangedProps } from "@/widgets/runtime";
import { WidgetFrame, WidgetQualifiers } from "@/components/home/WidgetFrame";
import { Unmeasured } from "@/components/home/WidgetParts";
import { TableScroll } from "@/components/ui/TableScroll";

import { classRows } from "./derive";

import "./catalog.css";

export function FleetClasses({ range, expanded: expandedProp }: RangedProps = {}) {
  const t = useTranslations("catalog.fleetClasses");
  const copy = useWidgetCopy();
  const text = useFleetText();
  const { locale } = text;
  const expanded = useExpanded(expandedProp);
  const window = useEffectiveRange(range);
  const read = homeReads.fleetSummary(window);
  const state = useApiResource(read.key, read.fetch);

  return (
    <WidgetFrame
      id="fleet-classes"
      title={t("title")}
      windowText={copy.range(window)}
      link={{ href: fleetDrillHref(window), label: t("link") }}
      state={state}
      what={t("what")}
      permission="read:fleet"
    >
      {({ data }) => {
        const { rows, hidden, incomplete } = classRows(data, expanded ? Infinity : undefined, text);
        if (rows.length === 0) {
          return (
            <p className="tw-state">{t("empty")}</p>
          );
        }
        const expected = data.classes.reduce((s, c) => s + c.buckets.expected, 0);
        const complete = data.classes.reduce((s, c) => s + c.buckets.complete, 0);
        return (
          <>
            <TableScroll label={t("label")}>
              <table className="tw-table cw-table" aria-label={t("label")}>
                <colgroup>
                  <col />
                  <col className="tw-col-count" />
                  <col className="tw-col-eps" />
                  <col className="tw-col-count" />
                </colgroup>
                <thead>
                  <tr>
                    <th scope="col">{t("class")}</th>
                    <th scope="col" className="num">
                      {t("sources")}
                    </th>
                    <th scope="col" className="num">
                      {t("meanEps")}
                    </th>
                    <th scope="col" className="num">
                      {t("silent")}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.key}>
                      <td className="tw-name">{r.label}</td>
                      <td className="num">{formatCount(r.sources, locale)}</td>
                      <td className="num">
                        {r.meanEps === null ? (
                          <Unmeasured reason={t("noCompleteBucket")} />
                        ) : (
                          formatEpsValue(r.meanEps, locale)
                        )}
                      </td>
                      <td className="num">
                        {r.silent === null ? (
                          <Unmeasured reason={t("noMonitored")} />
                        ) : (
                          <span className={r.silent > 0 ? "tw-state-word crit" : undefined}>
                            {formatCount(r.silent, locale)}
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
            {expanded ? (
              <p className="tw-note">
                {t("showingAll", {
                  shown: plainCount(rows.length, locale),
                  total: plainCount(rows.length + hidden, locale),
                })}
              </p>
            ) : null}
            {!expanded && hidden > 0 ? (
              <p className="tw-note">
                {t("showingBusiest", {
                  shown: plainCount(rows.length, locale),
                  total: plainCount(rows.length + hidden, locale),
                })}
              </p>
            ) : null}
          </>
        );
      }}
    </WidgetFrame>
  );
}
