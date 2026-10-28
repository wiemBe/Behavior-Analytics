// Which WAF sources are collected, and which need attention.

import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { AddSourcePicker } from "@/app/admin/waf-sources/AddSourcePicker";
import { WafSourceTable } from "@/app/admin/waf-sources/WafSourceTable";
import { readError } from "@/app/admin/waf-sources/readError";
import { PageHeader } from "@/components/ui/PageHeader";
import { guideFor } from "@/lib/guideRoutes";
import { api, type MappingSchemaOut, type WafSourceStatus } from "@/lib/api";
import { getBehaviorI18n } from "@/lib/behaviorI18n.server";
import { withRouteMessages } from "@/i18n/RouteMessages";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("waf.meta");
  return { title: t("admin") };
}

async function WafSourcesPage() {
  const t = await getTranslations("waf.admin");
  const i18n = await getBehaviorI18n();
  let sources: WafSourceStatus[] = [];
  let error: string | null = null;
  try {
    sources = await api.wafSources();
  } catch (err) {
    error = readError(err, t("read.whatSources"), i18n);
  }

  // The schema only supplies labels for the reason after "Invalid:". Without
  // it the field keys are used, so a failure here does not hide the table.
  let schema: MappingSchemaOut | null = null;
  if (!error) {
    try {
      schema = await api.wafMappingSchema();
    } catch {
      schema = null;
    }
  }

  return (
    <>
      <PageHeader
        guide={guideFor("/admin/waf-sources")}
        title={t("title")}
        description={t("description")}
      />

      {error ? (
        <div className="notice" role="alert">
          <p>
            <strong>{t("errorTitle")}</strong>
          </p>
          <p>{error}</p>
        </div>
      ) : sources.length === 0 ? (
        <>
          <div className="notice" role="status">
            <p>
              <strong>{t("emptyTitle")}</strong>
            </p>
            <p>{t("emptyText")}</p>
          </div>
          <AddSourcePicker />
        </>
      ) : (
        <div className="stack-6">
          <AddSourcePicker />
          <WafSourceTable sources={sources} schema={schema} />
        </div>
      )}
    </>
  );
}

export default withRouteMessages("/admin/waf-sources", WafSourcesPage);
