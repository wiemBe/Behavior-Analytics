// Property watches: which watches exist, and which need attention?
//
// One table, no KPI row. Watches that need an analyst (an open incident, a stale collection, a
// property that parses on nothing, a stale or oversized scope) sort first. The collection and
// alerting switches are said above the table, because a watch whose family is off is not being
// judged however the row reads.
//
// The New watch action and the editor are PW-8's: this page links to no route that does not
// exist. PW-8 adds its action to the `PageHeader` below.

import type { Metadata } from "next";
import Link from "next/link";
import { getTranslations } from "next-intl/server";

import { CadenceLatency, canReadSearchCadence } from "@/components/cadence/CadenceLatency";
import { PwStatus } from "@/components/propertywatch/PwStatus";
import {
  propertyWatchAccessFrom,
  type PropertyWatchAccess,
} from "@/components/propertywatch/authoring";
import {
  coverageText,
  pwLatencyFrom,
  propertiesText,
  relativeAge,
  scopeText,
  sortWatches,
} from "@/components/propertywatch/pwFormat";
import { PageHeader } from "@/components/ui/PageHeader";
import { TableScroll } from "@/components/ui/TableScroll";
import { ApiError, api, type PropertyWatch, type PropertyWatchCatalog } from "@/lib/api";
import { UtcTime } from "@/components/ui/UtcTime";
import { getBehaviorI18n } from "@/lib/behaviorI18n.server";
import { formatCount } from "@/lib/format";

import "@/components/ui/controls.css";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("propertyWatch.meta");
  return { title: t("list") };
}

export default async function PropertyWatchesPage() {
  const t = await getTranslations("propertyWatch");
  const i18n = await getBehaviorI18n();
  let watches: PropertyWatch[] = [];
  let catalog: PropertyWatchCatalog | null = null;
  let forbidden = false;
  let failed = false;
  let access: PropertyWatchAccess = "unknown";
  let canLink = false;
  try {
    watches = await api.propertyWatches();
  } catch (error) {
    forbidden = error instanceof ApiError && error.status === 403;
    failed = !forbidden;
  }
  if (!failed && !forbidden) {
    // The switches only qualify the list: their failure must not blank it, and it is said.
    catalog = await api.propertyWatchCatalog().catch(() => null);
    const caller = await api.caller().catch(() => null);
    access = caller ? propertyWatchAccessFrom(caller) : "unknown";
    canLink = canReadSearchCadence(caller);
  }
  const now = new Date();
  const rows = sortWatches(watches);

  return (
    <>
      <PageHeader
        title={t("list.title")}
        actions={
          access === "writer" ? (
            <Link className="action" href="/behavior/watches/new">{t("list.new")}</Link>
          ) : null
        }
      />

      {forbidden ? (
        <div className="notice" role="alert">
          <p>
            <strong>{t("list.forbiddenTitle")}</strong>
          </p>
          <p>
            {t.rich("list.forbiddenBody", { permission: (chunks) => <code>{chunks}</code> })}
          </p>
        </div>
      ) : failed ? (
        <div className="notice" role="alert">
          <p>
            <strong>{t("list.failedTitle")}</strong>
          </p>
          <p>
            {t("list.failedBody")}
          </p>
        </div>
      ) : (
        <>
          <div className="pw-notices">
            {catalog === null ? (
              <div className="banner warn" role="status">
                <span>
                  <strong>{t("list.switchesUnreadTitle")}</strong>{" "}{t("list.switchesUnreadBody")}
                </span>
              </div>
            ) : (
              <>
                {!catalog.collection_enabled && (
                  <div className="banner warn" role="status">
                    <span>
                      <strong>{t("list.collectionOffTitle")}</strong>{" "}
                      {t.rich("list.collectionOffBody", { setting: (chunks) => <code>{chunks}</code> })}
                    </span>
                  </div>
                )}
                {catalog.alerting_mode === "shadow" && (
                  <div className="banner" role="status">
                    <span>
                      <strong>{t("list.shadowTitle")}</strong>{" "}
                      {t.rich("list.shadowBody", { setting: (chunks) => <code>{chunks}</code> })}
                    </span>
                  </div>
                )}
              </>
            )}
            <CadenceLatency className="pw-latency" parts={pwLatencyFrom(catalog?.cadence, i18n)} canLink={canLink} />
            {access !== "writer" && (
              <p className="pw-write-note" role={access === "unknown" ? "alert" : undefined}>
                {access === "unknown" ? t("list.permissionsUnread") : ""}
                {t.rich("list.writePermission", { permission: (chunks) => <code>{chunks}</code> })}
              </p>
            )}
          </div>

          {rows.length === 0 ? (
            <div className="notice" role="status">
              <p>
                <strong>{t("list.emptyTitle")}</strong>
              </p>
              <p>
                {t("list.emptyBody")}
              </p>
            </div>
          ) : (
            <TableScroll label={t("list.tableLabel")}>
              <table className="table-dense pw-list" aria-label={t("list.tableLabel")}>
                <thead>
                  <tr>
                    <th scope="col">{t("list.name")}</th>
                    <th scope="col">{t("list.statusReason")}</th>
                    <th scope="col">{t("list.scope")}</th>
                    <th scope="col">{t("list.properties")}</th>
                    <th scope="col" className="num">
                      {t("list.coverage")}
                    </th>
                    <th scope="col" className="num">
                      {t("list.openIncidents")}
                    </th>
                    <th scope="col" className="num">
                      {t("list.lastBucketUtc")}
                    </th>
                    <th scope="col">{t("list.enabled")}</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((w) => (
                    <tr key={w.id}>
                      <td className="cell-name">
                        <Link href={`/behavior/watches/${w.id}`}>{w.name}</Link>
                      </td>
                      <td className="pw-status-cell">
                        <PwStatus status={w.status} reason={w.status_reason} history={w.history} />
                      </td>
                      <td className="pw-scope-cell">{scopeText(w, i18n)}</td>
                      <td className="pw-props-cell">{propertiesText(w)}</td>
                      <td className="num">{coverageText(w.coverage, i18n)}</td>
                      <td className="num">{formatCount(w.open_incidents, i18n.locale)}</td>
                      <td className="num cell-when nowrap">
                        {w.collected_through ? (
                          <>
                            <UtcTime value={w.collected_through} />
                            <span className="cell-sub">{relativeAge(w.collected_through, now, i18n)}</span>
                          </>
                        ) : (
                          <span className="muted">{t("list.noBucket")}</span>
                        )}
                      </td>
                      <td>{t(w.enabled ? "common.yes" : "common.no")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableScroll>
          )}
        </>
      )}
    </>
  );
}
