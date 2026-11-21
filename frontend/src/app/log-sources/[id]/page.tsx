import { getLocale, getTranslations } from "next-intl/server";
import Link from "next/link";
import { Suspense } from "react";
import { enumLabel } from "@/i18n/enumLabels";
import { asTextTranslator } from "@/i18n/textTranslator";
import { plainCount } from "@/lib/fleetText";
import type { FormatLocale } from "@/lib/format";
import { PivotBlock } from "@/components/pivot/PivotBlock";
import { pivotReads } from "@/lib/pivot";
import { PageHeader } from "@/components/ui/PageHeader";
import { guideFor } from "@/lib/guideRoutes";
import {
  ApiError,
  JDBC_PROTOCOL_IDS,
  api,
  fleetApi,
  rateLimitedText,
  type FleetSourceRow,
  type JdbcProtocolId,
  type LogSourceDetail,
} from "@/lib/api";
import { JdbcDetails, type JdbcDetailsProps } from "@/components/logsources/JdbcDetails";
import { SourceClassRow } from "@/components/fleet/SourceClassRow";
import { UtcTime } from "@/components/ui/UtcTime";
import { withRouteMessages } from "@/i18n/RouteMessages";

import { qradarStatusLabel, qradarStatusTone } from "../qradarStatus";

import "./log-source-detail.css";

/** The backend sends `protocol_type_id`; the shared type does not declare it yet. */
type WithProtocol = LogSourceDetail & { protocol_type_id?: number | null };

function isJdbc(d: WithProtocol): boolean {
  const protocol = d.protocol_type_id;
  return protocol != null && JDBC_PROTOCOL_IDS.includes(protocol as JdbcProtocolId);
}

/** The JDBC block is read on its own: its failure never touches the source metadata. */
async function JdbcBlock({ id, guide }: { id: string; guide: string }) {
  let props: JdbcDetailsProps;
  try {
    props = { data: await api.jdbcSource(id) };
  } catch (err) {
    const status = err instanceof ApiError ? err.status : 0;
    props = { failure: status === 403 ? "forbidden" : status === 404 ? "not-found" : "error" };
  }
  return (
    <>
      <JdbcDetails {...props} />
      <p className="jdbc-guide">
        <Link href="/guide/log-sources">{guide}</Link>
      </p>
    </>
  );
}

async function LogSourceDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const t = await getTranslations("logSources.detail");
  const say = asTextTranslator(await getTranslations());
  const locale = (await getLocale()) as FormatLocale;
  let d: LogSourceDetail | null = null;
  let status = 0;
  let limited: string | null = null;
  try {
    d = await api.logSource(id);
  } catch (err) {
    status = err instanceof ApiError ? err.status : 0;
    if (err instanceof ApiError && err.status === 429) limited = rateLimitedText(err, say);
  }
  if (!d) {
    const message =
      limited ??
      (status === 401
        ? say("errors.sessionExpired")
        : status === 403
          ? t("forbidden")
          : status === 404
            ? t("notFound", { id })
            : t("unreachable"));
    return (
      <>
        <PageHeader guide={guideFor("/log-sources/[id]")} title={t("title")} />
        <div className="notice" role="alert">
          <p>{message}</p>
        </div>
      </>
    );
  }

  const pivotRead = await pivotReads.logSource(id);
  let classRow: FleetSourceRow | null = null;
  let classFailed = false;
  try {
    classRow = await fleetApi.sourceClass(id);
  } catch {
    classFailed = true;
  }

  return (
    <>
      <PageHeader
        guide={guideFor("/log-sources/[id]")}
        title={d.name}
        description={
          t("summary", {
            type: d.type_name ?? t("unknownType"),
            criticality: enumLabel(say, "severityPill", d.criticality),
            owner: d.owner ?? "—",
          })
        }
      />

      <div className="card detail-card">
        <div className="k">{t("configuration")}</div>
        <SourceClassRow id={d.id} initial={classRow} failed={classFailed} />
        <p>{t("description", { value: d.description ?? "—" })}</p>
        <p>{t("businessHours", { value: d.business_hours_only ? t("yes") : t("no") })}</p>
        <p>
          {t("interval", {
            value:
              d.expected_interval_seconds == null
                ? "—"
                : t("seconds", { count: plainCount(d.expected_interval_seconds, locale) }),
          })}
        </p>
        <p>
          {d.last_event_time
            ? t.rich("lastEvent", { time: () => <UtcTime value={d.last_event_time} /> })
            : t("lastEventNever")}
        </p>
        <p>
          {t("qradarStatus")}{" "}
          <span className={`pill ${qradarStatusTone(d.qradar_status)}`}>
            {qradarStatusLabel(d.qradar_status, say)}
          </span>
        </p>
      </div>

      <p className="subtitle detail-note">
        {t.rich("note", {
          link: (chunks) => <Link href={`/behavior/sources/${d.id}`}>{chunks}</Link>,
        })}
      </p>

      {isJdbc(d) && (
        <section className="jdbc-source" aria-label={t("jdbcSection")}>
          <Suspense fallback={<JdbcDetails failure="loading" />}>
            <JdbcBlock id={d.id} guide={t("jdbcGuide")} />
          </Suspense>
        </section>
      )}

      {pivotRead && <PivotBlock pivot={pivotRead.pivot} failed={pivotRead.failed} />}
    </>
  );
}

export default withRouteMessages("/log-sources/[id]", LogSourceDetailPage);
