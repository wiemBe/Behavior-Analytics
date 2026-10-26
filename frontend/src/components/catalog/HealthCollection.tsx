"use client";

// Collection health (UR.5.1): are the scheduled searches running on time? One row per search
// family: its cadence, when it last ran and how late that run is against its cadence. A family
// that has not run since the upgrade has no lateness; it is an em dash, not "on time".

import { useTranslations } from "next-intl";

import { useApiResource } from "@/hooks/useApiResource";
import { homeReads } from "@/lib/api";
import { useFleetText } from "@/lib/fleetText";
import { formatCount } from "@/lib/format";
import { WidgetFrame } from "@/components/home/WidgetFrame";
import { Unmeasured, When } from "@/components/home/WidgetParts";

import { cadenceRows, lateText } from "./derive";

import "./catalog.css";

export function HealthCollection() {
  const t = useTranslations("catalog.collection");
  const text = useFleetText();
  const read = homeReads.searchCadence();
  const state = useApiResource(read.key, read.fetch);

  return (
    <WidgetFrame
      id="health-collection"
      title={t("title")}
      windowText={t("windowText")}
      link={{ href: "/admin/search-cadence", label: t("link") }}
      state={state}
      what={t("what")}
      permission="read:search_cadence"
    >
      {({ data }) => {
        if (data.length === 0) {
          return (
            <p className="tw-state">{t("empty")}</p>
          );
        }
        const rows = cadenceRows(data, Date.now(), text);
        const late = rows.filter((r) => r.late).length;
        return (
          <>
            <p className="tw-lead">
              {t.rich("late", {
                late: formatCount(late, text.locale),
                total: formatCount(rows.length, text.locale),
                num: (chunks) => <span className="num">{chunks}</span>,
              })}
              {rows.some((r) => r.lateSeconds === null) ? (
                <span className="muted"> · {t("someNotRun")}</span>
              ) : null}
            </p>
            <table className="tw-table cw-table">
              <caption className="sr-only">{t("caption")}</caption>
              <colgroup>
                <col />
                <col className="tw-col-badge" />
                <col className="tw-col-time" />
                <col className="tw-col-magnitude" />
              </colgroup>
              <thead>
                <tr>
                  <th scope="col">{t("family")}</th>
                  <th scope="col" className="num">
                    {t("cadence")}
                  </th>
                  <th scope="col">{t("lastRun")}</th>
                  <th scope="col" className="num">
                    {t("lateBy")}
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
                        <Unmeasured reason={t("neverRan")} />
                      ) : (
                        <span className={r.late ? "tw-state-word warn" : "tw-state-word"}>
                          {lateText(r, text)}
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
