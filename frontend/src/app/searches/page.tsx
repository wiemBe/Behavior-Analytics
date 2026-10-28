import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import Link from "next/link";

import { ScheduleLabel } from "@/components/searches/ScheduleLabel";
import { searchAccessFrom, type SearchAccess } from "@/components/searches/authoring";
import { versionTag } from "@/components/searches/searchesText";
import { PageHeader } from "@/components/ui/PageHeader";
import { TableScroll } from "@/components/ui/TableScroll";
import { enumLabel } from "@/i18n/enumLabels";
import { asTextTranslator } from "@/i18n/textTranslator";
import { api, sevTone, type ScheduledSearch } from "@/lib/api";
import { loadErrorMessage } from "@/lib/loadError";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("searches.meta");
  return { title: t("list") };
}

export default async function SearchCatalogPage() {
  const t = await getTranslations("searches.list");
  const say = asTextTranslator(await getTranslations());
  let searches: ScheduledSearch[] = [];
  let failed: string | null = null;
  let access: SearchAccess = "unknown";
  try {
    const [rows, caller] = await Promise.all([
      api.searches(),
      api.caller().then(searchAccessFrom).catch(() => "unknown" as const),
    ]);
    searches = rows;
    access = caller;
  } catch (err) {
    failed = loadErrorMessage(err, t("noun"), "read:searches", say);
    try {
      access = searchAccessFrom(await api.caller());
    } catch {
      access = "unknown";
    }
  }

  return (
    <>
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={
          access === "writer" ? (
            <Link className="action secondary" href="/searches/new">
              {t("newSearch")}
            </Link>
          ) : null
        }
      />
      {failed ? (
        <div className="notice" role="alert">
          <p>{failed}</p>
        </div>
      ) : searches.length === 0 ? (
        <div className="notice">
          <p>{t("empty")}</p>
          {access === "writer" ? (
            <p>{t("emptyWriter")}</p>
          ) : (
            <p>{t.rich("emptyReader", { code: (chunks) => <code>{chunks}</code> })}</p>
          )}
        </div>
      ) : (
        <TableScroll label={t("tableLabel")}>
          <table className="table-dense" aria-label={t("tableLabel")}>
            <thead>
              <tr>
                <th>{t("name")}</th>
                <th>{t("category")}</th>
                <th>{t("mitre")}</th>
                <th>{t("schedule")}</th>
                <th>{t("severity")}</th>
                <th>{t("version")}</th>
                <th>{t("enabled")}</th>
              </tr>
            </thead>
            <tbody>
              {searches.map((s) => (
                <tr key={s.id}>
                  <td>
                    <Link href={`/searches/${s.id}`}>{s.name}</Link>
                  </td>
                  <td>{s.category ?? "—"}</td>
                  <td>{s.mitre_techniques.join(", ") || "—"}</td>
                  <td>
                    <ScheduleLabel cron={s.schedule_cron} />
                  </td>
                  <td>
                    <span className={`pill ${sevTone(s.severity)}`}>
                      {enumLabel(say, "severityPill", s.severity)}
                    </span>
                  </td>
                  <td>{versionTag(s.query_version)}</td>
                  <td>{s.enabled ? t("yes") : <span className="pill warn">{t("no")}</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableScroll>
      )}
    </>
  );
}
