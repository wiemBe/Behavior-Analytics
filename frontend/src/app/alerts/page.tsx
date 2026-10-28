import Link from "next/link";
import { getLocale, getTranslations } from "next-intl/server";
import { Pagination } from "@/components/Pagination";
import { PageHeader } from "@/components/ui/PageHeader";
import { guideFor } from "@/lib/guideRoutes";
import { TableScroll } from "@/components/ui/TableScroll";
import { api, sevTone, type Alert, type Page } from "@/lib/api";
import { formatCount } from "@/lib/format";
import { loadErrorMessage } from "@/lib/loadError";
import { UtcTime } from "@/components/ui/UtcTime";
import { enumLabel } from "@/i18n/enumLabels";
import { asTextTranslator } from "@/i18n/textTranslator";
import type { Locale } from "@/i18n/locale";

import "./alerts.css";

const PAGE_SIZE = 100;
const ALERT_STATUSES = ["OPEN", "ACKNOWLEDGED", "RESOLVED"] as const;

function statusTone(status: string): string {
  if (status === "OPEN") return "crit";
  if (status === "ACKNOWLEDGED") return "warn";
  return "ok";
}

function alertStatus(value: string | undefined): string {
  return ALERT_STATUSES.includes(value as (typeof ALERT_STATUSES)[number]) ? value! : "";
}

export async function generateMetadata() {
  const t = await getTranslations("alerts.meta");
  return { title: t("list") };
}

export default async function AlertsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const params = await searchParams;
  const offset = Math.max(0, Number(params.offset ?? 0) || 0);
  const status = alertStatus(params.status);
  const t = await getTranslations("alerts");
  const say = asTextTranslator(await getTranslations());
  const locale = (await getLocale()) as Locale;
  let page: Page<Alert> | null = null;
  let error: string | null = null;
  try {
    page = await api.alerts({
      status: status || undefined,
      limit: PAGE_SIZE,
      offset,
    });
  } catch (err) {
    error = loadErrorMessage(err, t("loadNoun"), "read:alerts", say);
  }
  const alerts = page?.items ?? [];

  return (
    <>
      <PageHeader
        guide={guideFor("/alerts")}
        title={t("title")}
        description={t("description")}
      />

      <form className="filters" method="get" action="/alerts">
        <label className="sr-only" htmlFor="alert-status">
          {t("filters.label")}
        </label>
        <select id="alert-status" name="status" defaultValue={status}>
          <option value="">{t("filters.all")}</option>
          <option value="OPEN">{t("filters.status.OPEN")}</option>
          <option value="ACKNOWLEDGED">{t("filters.status.ACKNOWLEDGED")}</option>
          <option value="RESOLVED">{t("filters.status.RESOLVED")}</option>
        </select>
        <button type="submit">{t("filters.filter")}</button>
        {status && <Link href="/alerts">{t("filters.clear")}</Link>}
      </form>

      {error ? (
        <div className="notice" role="alert">
          <p>{error}</p>
        </div>
      ) : alerts.length === 0 ? (
        <div className="notice">
          {status === "OPEN"
            ? t("empty.OPEN")
            : status === "ACKNOWLEDGED"
              ? t("empty.ACKNOWLEDGED")
              : status === "RESOLVED"
                ? t("empty.RESOLVED")
                : t("empty.all")}
        </div>
      ) : (
        <>
          {page && page.total > alerts.length && (
            <p className="subtitle">
              {t("showing", {
                shown: formatCount(alerts.length, locale),
                total: formatCount(page.total, locale),
              })}
            </p>
          )}
          <TableScroll label={t("table.label")}>
            <table className="table-dense" aria-label={t("table.label")}>
              <thead>
                <tr>
                  <th>{t("table.title")}</th>
                  <th>{t("table.severity")}</th>
                  <th>{t("table.status")}</th>
                  <th className="num">{t("table.occurrences")}</th>
                  <th>{t("table.firstSeen")}</th>
                  <th>{t("table.lastSeen")}</th>
                </tr>
              </thead>
              <tbody>
                {alerts.map((a) => (
                  <tr key={a.id}>
                    <td className="alert-title">
                      <Link href={`/alerts/${a.id}`}>{a.title}</Link>
                    </td>
                    <td><span className={`pill ${sevTone(a.severity)}`}>{enumLabel(say, "severityPill", a.severity)}</span></td>
                    <td><span className={`pill ${statusTone(a.status)}`}>{enumLabel(say, "alertStatusPill", a.status)}</span></td>
                    <td className="num">{formatCount(a.occurrence_count, locale)}</td>
                    <td className="nowrap">{<UtcTime value={a.first_seen_at} variant="compact" />}</td>
                    <td className="nowrap">{<UtcTime value={a.last_seen_at} variant="compact" />}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableScroll>
          <Pagination
            total={page?.total ?? 0}
            limit={PAGE_SIZE}
            offset={offset}
            basePath="/alerts"
            params={{ status }}
          />
        </>
      )}
    </>
  );
}
