"use client";

// Recent offenses (UR.4.3): the most recently updated open offenses.
//
// Magnitude is a ten-segment bar and the number; QRadar's magnitude can be null,
// and then there is a dash and no bar at all (an empty bar would read as 0). With no
// snapshot the widget says NOT_COLLECTED, which is not "no open offenses".

import { useTranslations } from "next-intl";
import Link from "next/link";

import { TableScroll } from "@/components/ui/TableScroll";
import { useApiResource } from "@/hooks/useApiResource";
import { useEnumLabels } from "@/i18n/enumLabels";
import { asTextTranslator } from "@/i18n/textTranslator";
import { useMeasures } from "@/i18n/measures";
import { homeReads } from "@/lib/api";
import { MAGNITUDE_SEGMENTS, RECENT_OFFENSES_LIMIT, recentOffenses } from "@/lib/home/recentOffenses";

import { useExpanded } from "@/widgets/runtime";

import { secondary, WidgetFrame, WidgetLoading, WidgetQualifiers } from "./WidgetFrame";
import { Unmeasured, When } from "./WidgetParts";

/** QRadar's own offense number, "#1234": an identifier, not interface text. */
const offenseTag = (id: number) => `#${id}`;

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
  const t = useTranslations("home.offenses");
  const say = asTextTranslator(useTranslations());
  const measures = useMeasures();
  const { label } = useEnumLabels();
  const expanded = useExpanded();
  const openRead = homeReads.recentOffenses(RECENT_OFFENSES_LIMIT);
  const probeRead = homeReads.offenseProbe();
  const open = useApiResource(openRead.key, openRead.fetch);
  const probe = useApiResource(probeRead.key, probeRead.fetch);

  return (
    <WidgetFrame
      id="recent-offenses"
      title={t("title")}
      windowText={t("window")}
      link={{ href: "/offenses?status=OPEN", label: t("link") }}
      state={open}
      what={t("what")}
      permission="read:offenses"
    >
      {({ data }) => {
          const p = secondary(probe, say);
          // An empty list needs the probe to say why it is empty.
          if (data.items.length === 0 && p.pending) {
            return <WidgetLoading what={t("snapshotWhat")} />;
          }
          const view = recentOffenses(data, p.data);
          if (view.qualifiers.length > 0 && view.rows.length === 0) {
            return <WidgetQualifiers qualifiers={view.qualifiers} />;
          }
          if (view.empty !== null) return <p className="tw-state">{t("emptyOpen")}</p>;
          return (
            <>
              <TableScroll label={t("title")}>
                <table className="tw-table" aria-label={t("title")}>
                  <colgroup>
                    <col className="tw-col-time" />
                    <col />
                    <col className="tw-col-magnitude" />
                  </colgroup>
                  <thead>
                    <tr>
                      <th scope="col">{t("lastUpdated")}</th>
                      <th scope="col">{t("offense")}</th>
                      <th scope="col" className="num">
                        {t("magnitude")}
                        <span className="tw-sub tw-wrap">{t("outOfTen")}</span>
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
                            title={r.description ?? t("offenseId", { id: r.id })}
                          >
                            {r.description ?? t("offenseId", { id: r.id })}
                          </Link>
                          <span className="tw-sub">
                            {offenseTag(r.id)} · {label("offenseStatus", r.status)}
                          </span>
                        </td>
                        <td className="num">
                          {r.magnitude === null || r.segments === null ? (
                            <Unmeasured reason={t("noMagnitude")} />
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
                    ? t("showingExpanded", {
                        shown: view.rows.length,
                        total: measures.count(view.openTotal),
                        limit: RECENT_OFFENSES_LIMIT,
                      })
                    : t("showing", { shown: view.rows.length, total: measures.count(view.openTotal) })}
                </p>
              )}
            </>
          );
      }}
    </WidgetFrame>
  );
}
