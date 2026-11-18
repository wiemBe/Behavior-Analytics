"use client";

import { useTranslations } from "next-intl";
import { UtcTime } from "@/components/ui/UtcTime";
import type { PropertyCatalogOut } from "@/lib/api";

export function CatalogStatus({ catalog, typeName }: { catalog: PropertyCatalogOut | null; typeName?: string | null }) {
  const t = useTranslations("fieldMapping.form");
  // Keep the disabled, frozen Turkish rendering compatible without maintaining its catalog.
  if (!t.has("catalogAsOf")) return <p className="fm-catalog" role="status">
    {catalog?.status === "AVAILABLE" ? <>
      {t.rich("catalogFetched", { time: () => <UtcTime value={catalog.fetched_at} /> })}{" "}
      {catalog.filtered_by_source_type ? t("catalogApplies", { type: typeName ?? t("catalogThisType") }) : t("catalogUnfiltered")}
    </> : t("catalogDownTail", { lead: t("catalogDown") + (catalog?.detail ? `: ${catalog.detail}` : ".") })}
  </p>;
  const asOf = catalog?.status === "AVAILABLE" ? catalog.as_of ?? catalog.fetched_at : null;
  const time = asOf ? new Date(asOf).toISOString().slice(11, 16) + " UTC" : "";
  const state = catalog?.catalog_status;
  const cause = catalog?.failure?.kind ?? "unavailable";
  const line = state === "REFRESHING" ? t("catalogRefreshing")
    : (state === "FAILED" || (state === "PARTIAL" && catalog?.applicability_complete)) ? (asOf ? t("catalogFailedOld", { cause, time }) : t("catalogFailedEmpty", { cause }))
    : state === "PARTIAL" ? t("catalogPartial", { cause })
    : state === "NEVER" ? t("catalogNever")
    : state === "COMPLETE" && catalog?.property_count === 0 ? t("catalogEmpty", { time })
    : asOf ? t("catalogAsOf", { time }) : t("catalogUnavailable");
  return <div className="fm-catalog">
    <p role="status">{line}</p>
    {(catalog?.failure || catalog?.detail || catalog?.refresh_seconds) && <details>
      <summary>{t("catalogDetails")}</summary>
      {catalog.failure && <p>{t("catalogFailureDetails", {
        failureClass: catalog.failure.failure_class, endpoint: catalog.failure.endpoint,
        status: catalog.failure.http_status ?? "—", start: catalog.failure.page_start,
        end: catalog.failure.page_end, attempts: catalog.failure.attempts,
        seconds: catalog.failure.elapsed_seconds.toFixed(1),
      })}</p>}
      {catalog.detail && <p>{catalog.detail}</p>}
      {catalog.refresh_seconds && <p>{t("catalogCadence", { seconds: catalog.refresh_seconds })}</p>}
    </details>}
  </div>;
}
