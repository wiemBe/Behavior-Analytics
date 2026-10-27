"use client";

// Outage policy (UR.5.1): which outage policy applies, and how many overrides? The default
// leads; only the classes that depart from it, or hold source overrides, are listed.

import { useTranslations } from "next-intl";

import { useApiResource } from "@/hooks/useApiResource";
import { homeReads, type OutageFleetSummary, type OutageMode } from "@/lib/api";
import { useFleetText, type FleetText } from "@/lib/fleetText";
import { WidgetFrame } from "@/components/home/WidgetFrame";
import { Unmeasured } from "@/components/home/WidgetParts";
import { TableScroll } from "@/components/ui/TableScroll";
import { formatCount } from "@/lib/format";

import { outageView } from "./derive";

import "./catalog.css";

/** `1 bucket (5 min)`, or the buckets alone; null when the default grace is not reported. */
function graceText(summary: OutageFleetSummary, text: FleetText): string | null {
  const g = summary.default_silence_grace_buckets;
  if (g === null || g === undefined) return null;
  const buckets = text.say("catalog.outage.buckets", { count: g });
  if (summary.bucket_seconds === null) return buckets;
  const minutes = Math.round(((g * summary.bucket_seconds) / 60) * 10) / 10;
  const figure = Number.isInteger(minutes) ? String(minutes) : minutes.toFixed(1);
  const count = text.locale === "tr" ? figure.replace(".", ",") : figure;
  return `${buckets} (${text.say("units.minute", { count })})`;
}

export function OutagePolicy() {
  const t = useTranslations("catalog.outage");
  const text = useFleetText();
  const n = (v: number) => formatCount(v, text.locale);
  const now = useTranslations("catalog")("now");
  /** The mode in words; null for a mode the catalog does not know. */
  const modeLabel = (mode: OutageMode | null | undefined): string | null =>
    mode && text.say.has?.(`catalog.outage.mode.${mode}`)
      ? text.say(`catalog.outage.mode.${mode}`)
      : null;
  const read = homeReads.outageSummary("24h");
  const state = useApiResource(read.key, read.fetch);

  return (
    <WidgetFrame
      id="outage-policy"
      title={t("title")}
      windowText={now}
      link={{ href: "/admin/outage-policy", label: t("link") }}
      state={state}
      what={t("what")}
      permission="read:fleet"
    >
      {({ data }) => {
        const view = outageView(data, text);
        const grace = graceText(data, text);
        const mode = modeLabel(data.outage_default);
        return (
          <>
            <p className="cw-figure">
              {mode ? (
                <>
                  {mode}
                  <span className="cw-unit">
                    {grace ? t("byDefaultGrace", { grace }) : t("byDefault")}
                  </span>
                </>
              ) : (
                <>
                  <Unmeasured reason={t("noDefault")} />
                  <span className="cw-unit">{t("noDefault")}</span>
                </>
              )}
            </p>
            {data.classes.length === 0 ? (
              <p className="tw-state">{t("noClasses")}</p>
            ) : (
              <p className="tw-lead">
                {t.rich("overrides", {
                  count: view.overrides,
                  num: (chunks) => <span className="num">{chunks}</span>,
                })}
                <span className="muted">
                  {" · "}
                  {t("recordOnly", { count: view.recordOnlyClasses })}
                </span>
              </p>
            )}
            {data.classes.length === 0 ? null : view.rows.length === 0 ? (
              <p className="tw-state">{t("noDeparture")}</p>
            ) : (
              <TableScroll label={t("label")}>
                <table className="tw-table cw-table" aria-label={t("ariaLabel")}>
                  <colgroup>
                    <col />
                    <col className="tw-col-badge" />
                    <col className="tw-col-count" />
                  </colgroup>
                  <thead>
                    <tr>
                      <th scope="col">{t("class")}</th>
                      <th scope="col">{t("modeHeader")}</th>
                      <th scope="col" className="num">
                        {t("overridesHeader")}
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {view.rows.map((r) => (
                      <tr key={r.key}>
                        <td className="tw-name">{r.label}</td>
                        <td>{modeLabel(r.mode)}</td>
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
