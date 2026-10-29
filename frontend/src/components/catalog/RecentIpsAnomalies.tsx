"use client";

// Recent IPS anomalies (UR.5.1): the latest active anomalies of IPS entities and of IPS sources,
// merged by detection time. Each row is in its detector's own unit (events per bucket, distinct
// ports, a share), never EPS: IPS figures are not log-source volume. An empty list is not a clean
// bill: an IPS subject without a reliable baseline is not judged.

import Link from "next/link";
import { useTranslations } from "next-intl";

import { WidgetFrame } from "@/components/home/WidgetFrame";
import { Unmeasured, When } from "@/components/home/WidgetParts";
import { TableScroll } from "@/components/ui/TableScroll";
import { statePillText } from "@/components/waf/wafLabels";
import { useApiResource, type ResourceState } from "@/hooks/useApiResource";
import { homeReads, sevTone, type AnomalySummary, type Page } from "@/lib/api";
import { stateTone } from "@/lib/behavior";
import { ARROW } from "@/lib/behaviorI18n";
import { combine2 } from "@/lib/home/combine";
import { useEnumLabels } from "@/i18n/enumLabels";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";
import { useExpanded } from "@/widgets/runtime";
import type { WidgetState } from "@/widgets/types";
import { UtcTime } from "@/components/ui/UtcTime";

import { IPS_ANOMALIES_LIMIT, mergeIpsAnomalies } from "./ipsAnomalies";

import "./catalog.css";

export function RecentIpsAnomalies({ id = "single" }: { id?: string } = {}) {
  const entityRead = homeReads.activeIpsAnomalies("IPS_ENTITY", IPS_ANOMALIES_LIMIT);
  const sourceRead = homeReads.activeIpsAnomalies("IPS_SOURCE", IPS_ANOMALIES_LIMIT);
  const entities = useApiResource(entityRead.key, entityRead.fetch);
  const sources = useApiResource(sourceRead.key, sourceRead.fetch);
  const state = combine2(entities, sources);
  return <RecentIpsAnomaliesView id={id} state={state} />;
}

type IpsPages = [Page<AnomalySummary>, Page<AnomalySummary>];

export function RecentIpsAnomaliesView({
  id = "single",
  state,
}: {
  id?: string;
  state: ResourceState<IpsPages> | WidgetState<IpsPages>;
}) {
  const expanded = useExpanded();
  const t = useTranslations("ips.recent");
  const i18n = useBehaviorI18n();
  const { severityPill } = useEnumLabels();
  const pages =
    "status" in state
      ? state.status === "ok"
        ? state.data
        : null
      : state.kind === "ok" || state.kind === "stale"
        ? state.data
        : null;
  const view = pages ? mergeIpsAnomalies(pages[0], pages[1], IPS_ANOMALIES_LIMIT, i18n) : null;

  return (
    <WidgetFrame
      id={`ips-anomalies-${id}`}
      title={t("title")}
      windowText={t("windowText")}
      badge={view?.badge}
      link={{ href: "/anomalies?subject_kind=IPS&active_only=true", label: t("viewAll"), ariaLabel: t("viewAllAria") }}
      state={state}
      what={t("what")}
      permission="read:anomalies"
    >
      {() => {
        if (!view) return null;
        if (view.rows.length === 0) return <p className="tw-state">{t("empty")}</p>;
        return (
          <>
            <TableScroll label={t("tableLabel")}>
              <table className="tw-table" aria-label={t("tableLabel")}>
                <colgroup>
                  <col className="tw-col-time tw-unfold" />
                  <col />
                  <col className="tw-col-badge tw-unfold" />
                  <col className="cw-col-figures" />
                  <col className="tw-col-action" />
                </colgroup>
                <thead>
                  <tr>
                    <th scope="col" className="tw-unfold">
                      {t("detected")}
                    </th>
                    <th scope="col" className="tw-lead-col">
                      {t("anomaly")}
                    </th>
                    <th scope="col" className="tw-unfold">
                      {t("severityState")}
                    </th>
                    <th scope="col" className="num">
                      {t("observedExpected")}
                    </th>
                    <th scope="col">
                      <span className="sr-only">{t("investigateSr")}</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {view.rows.map((r) => (
                    <tr key={r.id}>
                      <td className="tw-unfold">
                        <When iso={r.detectedAt} />
                      </td>
                      <td className="tw-name tw-lead-col cw-ips-name">
                        <Link href={r.href}>
                          {r.subject}
                        </Link>
                        <span className="tw-sub">
                          {r.typeLabel} · {r.sourceName}
                        </span>
                        <span className="tw-sub tw-wrap tw-fold-line">
                          <span className={`tw-state-word ${sevTone(r.severity)}`}>
                            {severityPill(r.severity)}
                          </span>
                          {" · "}
                          <span className={`tw-state-word ${stateTone(r.state)}`}>
                            {statePillText(r.state, i18n)}
                          </span>
                          {" · "}
                          <UtcTime value={r.detectedAt} variant="compact" />
                        </span>
                      </td>
                      <td className="tw-unfold">
                        <span className={`pill ${sevTone(r.severity)}`}>{severityPill(r.severity)}</span>
                        <span className={`tw-sub tw-wrap tw-state-word ${stateTone(r.state)}`}>
                          {statePillText(r.state, i18n)}
                        </span>
                      </td>
                      <td className="num">
                        {r.observed === null ? (
                          <Unmeasured reason={t("unitUnknown")} />
                        ) : (
                          <>
                            {r.observed}
                            <span className="muted"> {ARROW} </span>
                            {r.expected}
                            {r.unitWords ? <span className="tw-sub tw-wrap">{r.unitWords}</span> : null}
                          </>
                        )}
                      </td>
                      <td>
                        <Link
                          className="tw-link"
                          href={r.href}
                          aria-label={t("investigateAria", { subject: r.subject })}
                        >
                          {t("investigate")}
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableScroll>
            {view.shown ? (
              <p className="tw-note">
                {(expanded ? view.shownShort : view.shown) + "."}
                {expanded ? ` ${t("shownNote", { limit: IPS_ANOMALIES_LIMIT })}` : ""}
              </p>
            ) : null}
          </>
        );
      }}
    </WidgetFrame>
  );
}
