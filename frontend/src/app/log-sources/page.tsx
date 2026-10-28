import { getLocale, getTranslations } from "next-intl/server";
import Link from "next/link";
import { enumLabel } from "@/i18n/enumLabels";
import { asTextTranslator } from "@/i18n/textTranslator";
import { plainCount } from "@/lib/fleetText";
import type { FormatLocale } from "@/lib/format";
import { PageHeader } from "@/components/ui/PageHeader";
import { TableScroll } from "@/components/ui/TableScroll";
import { api, type LogSourceSummary } from "@/lib/api";
import { loadErrorMessage } from "@/lib/loadError";
import { UtcTime } from "@/components/ui/UtcTime";


import "./log-sources.css";
import { qradarStatusLabel, qradarStatusTone } from "./qradarStatus";

export default async function LogSourcesPage() {
  const t = await getTranslations("logSources");
  const say = asTextTranslator(await getTranslations());
  const locale = (await getLocale()) as FormatLocale;
  let sources: LogSourceSummary[] = [];
  let error: string | null = null;
  try {
    sources = await api.logSources();
  } catch (err) {
    error = loadErrorMessage(err, t("loadNoun"), "read:log_sources", say);
  }

  return (
    <>
      <PageHeader
        title={t("title")}
        description={
          // A failed read has no count: "0 sources" would claim an empty inventory.
          <>
            {error ? "" : `${t("countLead", { count: plainCount(sources.length, locale) })} `}
            {t("description")}
          </>
        }
      />

      {error ? (
        <div className="notice" role="alert">
          {error}
        </div>
      ) : sources.length === 0 ? (
        <div className="notice">
          {t("empty")}
        </div>
      ) : (
        <TableScroll label={t("table.label")}>
          <table className="table-dense log-sources-table" aria-label={t("table.label")}>
            <thead>
              <tr>
                <th>{t("table.name")}</th>
                <th>{t("table.type")}</th>
                <th>{t("table.criticality")}</th>
                <th>{t("table.status")}</th>
                <th className="num">{t("table.anomalies")}</th>
                <th>{t("table.lastEvent")}</th>
              </tr>
            </thead>
            <tbody>
              {sources.map((s) => (
                <tr key={s.id}>
                  <td className="cell-name">
                    <Link href={`/log-sources/${s.id}`}>{s.name}</Link>
                    {s.maintenance_mode && (
                      <span className="pill warn log-source-maint"> {t("table.maint")}</span>
                    )}
                  </td>
                  <td>{s.type_name ?? "—"}</td>
                  <td>{enumLabel(say, "severityPill", s.criticality)}</td>
                  <td>
                    <span className={`pill ${qradarStatusTone(s.qradar_status)}`}>
                      {qradarStatusLabel(s.qradar_status, say)}
                    </span>
                  </td>
                  <td className="num">
                    {s.open_anomaly_count > 0 ? (
                      <span className="pill warn">{plainCount(s.open_anomaly_count, locale)}</span>
                    ) : (
                      "0"
                    )}
                  </td>
                  <td className="nowrap">
                    {s.last_event_time ? (
                      <UtcTime value={s.last_event_time} variant="compact" />
                    ) : (
                      t("table.never")
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableScroll>
      )}
    </>
  );
}
