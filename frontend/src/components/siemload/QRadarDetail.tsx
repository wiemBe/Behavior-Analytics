"use client";

// QRadar in detail, one section per console: the managed hosts (with HA pairs), their disk, system
// and pipeline, QRadar's own system notifications of the last 24 h, and the trends. The hosts come
// from the page's own read (`/admin/ariel-load/current`); the Health Metrics, notifications and
// trends are stored by the QRADAR_HEALTH search family and read from two further routes, each with
// its own loading, forbidden and error state, so one failing read leaves the others on screen.

import Link from "next/link";
import { useTranslations } from "next-intl";
import { useMemo } from "react";

import { useApiResource } from "@/hooks/useApiResource";
import { LoadingState } from "@/components/ui/RouteState";
import { UtcTime } from "@/components/ui/UtcTime";
import {
  qradarHealthApi,
  type QRadarConsoleHealth,
  type QRadarConsoleLatest,
  type QRadarHealthHistory,
  type QRadarHealthLatest,
} from "@/lib/api";

import { HealthTable } from "./HealthTable";
import { HostsTable } from "./HostsTable";
import { NotificationsTable } from "./NotificationsTable";
import { joinHealth } from "./qradarDetail";
import {
  rangeHref,
  TREND_RANGES,
  trendHours,
  type SummaryRangeId,
  type TrendRangeId,
} from "./siemFormat";
import { TrendCharts } from "./TrendCharts";
import type { ResourceState } from "@/hooks/useApiResource";

/** The notifications span this many hours. */
export const NOTIFICATION_HOURS = 24;

const TREND_LABEL = { "24h": "range24h", "7d": "range7d" } as const;

function Problem({
  resource,
  what,
}: {
  resource: ResourceState<unknown>;
  what: "detail" | "trend";
}) {
  const detail = useTranslations("siemLoad.detail");
  const trend = useTranslations("siemLoad.detail.trend");
  const t = what === "trend" ? trend : detail;
  const state = useTranslations("siemLoad.state");
  const errors = useTranslations("errors");
  if (resource.status === "forbidden") {
    return (
      <div className="notice" role="alert">
        <p>
          <strong>{t("couldNotRead")}</strong>
        </p>
        <p>{t.rich("forbidden", { perm: (chunks) => <code className="perm">{chunks}</code> })}</p>
      </div>
    );
  }
  if (resource.status !== "error") return null;
  return (
    <div className="notice" role="alert">
      <p>
        <strong>{t("couldNotRead")}</strong>
      </p>
      <p>
        {resource.httpStatus === 429
          ? resource.retryAfter == null
            ? errors("rateLimited")
            : errors("rateLimitedAfter", { seconds: resource.retryAfter })
          : resource.httpStatus === null
            ? state("noAnswer")
            : state("answeredWith", { status: resource.httpStatus })}
      </p>
      <p>{state("advice")}</p>
    </div>
  );
}

function TrendControl({ range, trend }: { range: SummaryRangeId; trend: TrendRangeId }) {
  const t = useTranslations("siemLoad.detail.trend");
  return (
    <nav className="range-selector" aria-label={t("rangeLabel")}>
      {TREND_RANGES.map((choice) => (
        <Link
          key={choice.id}
          href={rangeHref(range, choice.id)}
          className={`range-option${choice.id === trend ? " range-option-active" : ""}`}
          aria-current={choice.id === trend ? "page" : undefined}
        >
          {t(TREND_LABEL[choice.id])}
        </Link>
      ))}
    </nav>
  );
}

function ConsoleDetail({
  health,
  latest,
  latestState,
  history,
  historyState,
  range,
  trend,
}: {
  health: QRadarConsoleHealth;
  latest: QRadarConsoleLatest | undefined;
  latestState: ResourceState<QRadarHealthLatest>;
  history: QRadarHealthHistory | undefined;
  historyState: ResourceState<QRadarHealthHistory>;
  range: SummaryRangeId;
  trend: TrendRangeId;
}) {
  const t = useTranslations("siemLoad.detail");
  const trendT = useTranslations("siemLoad.detail.trend");
  const healthT = useTranslations("siemLoad.detail.health");
  const collectionOff = latestState.status === "ok" && latestState.data.collection_enabled === false;
  const hosts = health.hosts;
  const join = useMemo(
    () =>
      joinHealth(hosts?.state === "OK" && hosts.value ? hosts.value.items : [], latest?.hosts ?? []),
    [hosts, latest],
  );
  const id = `sl-detail-${health.instance_id}`;
  const noticeHours = NOTIFICATION_HOURS;

  return (
    <section
      className="sl-section sl-detail"
      aria-labelledby={`${id}-h`}
    >
      <h2 className="sl-h" id={`${id}-h`}>
        {t("consoleHeading", { name: health.instance_name })}
      </h2>

      <div className="sl-block">
        <h3 className="sl-sub">{t("hosts.heading")}</h3>
        <HostsTable name={health.instance_name} rows={join.rows} hosts={hosts} />
      </div>

      {collectionOff ? (
        <p className="banner warn" role="status">
          <span>
            {latest?.window
              ? healthT.rich("collectionOff", { time: () => <UtcTime value={latest.window?.end} /> })
              : healthT("collectionOffNoWindow")}
          </span>
        </p>
      ) : null}

      <div className="sl-block">
        <h3 className="sl-sub">{t("health.heading")}</h3>
        {latestState.status === "ok" ? (
          latest ? (
            <HealthTable console_={latest} join={join} hostsRead={hosts?.state === "OK"} />
          ) : (
            <p className="sl-hint">{t("health.notRead")}</p>
          )
        ) : latestState.status === "loading" || latestState.status === "idle" ? (
          <LoadingState what={t("loadingHealth")} />
        ) : (
          <Problem resource={latestState} what="detail" />
        )}
      </div>

      <div className="sl-block">
        <h3 className="sl-sub">{t("notices.heading", { hours: noticeHours })}</h3>
        {latestState.status === "ok" ? (
          latest ? (
            <NotificationsTable console_={latest} hours={noticeHours} />
          ) : (
            <p className="sl-hint">{t("notices.notRead")}</p>
          )
        ) : latestState.status === "loading" || latestState.status === "idle" ? (
          <LoadingState what={t("loadingNotices")} />
        ) : (
          <Problem resource={latestState} what="detail" />
        )}
      </div>

      <div className="sl-block">
        <div className="sl-section-head">
          <h3 className="sl-sub">{trendT("heading")}</h3>
          <TrendControl range={range} trend={trend} />
        </div>
        {historyState.status === "ok" && history ? (
          <TrendCharts
            history={history}
            console_={history.consoles.find((c) => c.instance_id === health.instance_id)}
          />
        ) : historyState.status === "loading" || historyState.status === "idle" ? (
          <LoadingState what={trendT("loadingWhat")} />
        ) : (
          <Problem resource={historyState} what="trend" />
        )}
      </div>
    </section>
  );
}

export function QRadarDetail({
  consoles,
  range,
  trend,
}: {
  consoles: QRadarConsoleHealth[];
  range: SummaryRangeId;
  trend: TrendRangeId;
}) {
  const hours = trendHours(trend);
  const latestState = useApiResource(
    `/admin/qradar-health/latest?notifications_hours=${NOTIFICATION_HOURS}`,
    (signal) => qradarHealthApi.latest(NOTIFICATION_HOURS, signal),
  );
  const historyState = useApiResource(`/admin/qradar-health/history?hours=${hours}`, (signal) =>
    qradarHealthApi.history(hours, signal),
  );
  const latest = latestState.status === "ok" ? latestState.data : undefined;
  const history = historyState.status === "ok" ? historyState.data : undefined;
  const t = useTranslations("siemLoad.detail");
  // A backend that sends no host table and has not yet answered the stored read has nothing to
  // draw a heading for: say that it is being read, and nothing more.
  const waiting =
    (latestState.status === "loading" || latestState.status === "idle") &&
    consoles.every((health) => health.hosts === undefined);
  if (waiting) return <LoadingState what={t("loadingWhat")} />;

  return (
    <>
      {consoles.map((health) => (
        <ConsoleDetail
          key={health.instance_id}
          health={health}
          latest={latest?.consoles.find((c) => c.instance_id === health.instance_id)}
          latestState={latestState}
          history={history}
          historyState={historyState}
          range={range}
          trend={trend}
        />
      ))}
    </>
  );
}
