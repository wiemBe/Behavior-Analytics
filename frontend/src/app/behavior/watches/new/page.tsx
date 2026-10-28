import type { Metadata } from "next";
import Link from "next/link";
import { getTranslations } from "next-intl/server";

import { CadenceLatency, canReadSearchCadence } from "@/components/cadence/CadenceLatency";
import { PropertyWatchWriteNotice } from "@/components/propertywatch/PropertyWatchWriteNotice";
import { WatchEditor } from "@/components/propertywatch/WatchEditor";
import {
  propertyWatchAccessFrom,
  type PropertyWatchAccess,
} from "@/components/propertywatch/authoring";
import { pwLatencyFrom } from "@/components/propertywatch/pwFormat";
import { PageHeader } from "@/components/ui/PageHeader";
import { guideFor } from "@/lib/guideRoutes";
import { ApiError, api, type CallerInfo, type PropertyWatchCatalog } from "@/lib/api";
import { getBehaviorI18n } from "@/lib/behaviorI18n.server";

import "@/components/ui/controls.css";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("propertyWatch.meta");
  return { title: t("new") };
}

export default async function NewPropertyWatchPage() {
  const t = await getTranslations("propertyWatch");
  const i18n = await getBehaviorI18n();
  let catalog: PropertyWatchCatalog | null = null;
  let access: PropertyWatchAccess = "unknown";
  let canLink = false;
  let forbidden = false;
  let failed = false;
  try {
    let caller: CallerInfo | null = null;
    [catalog, caller] = await Promise.all([
      api.propertyWatchCatalog(),
      api.caller().catch(() => null),
    ]);
    access = caller ? propertyWatchAccessFrom(caller) : "unknown";
    canLink = canReadSearchCadence(caller);
  } catch (error) {
    forbidden = error instanceof ApiError && error.status === 403;
    failed = !forbidden;
  }

  return (
    <>
      <PageHeader guide={guideFor("/behavior/watches/new")} title={t("new.title")} />
      {forbidden ? (
        <div className="notice" role="alert">
          <p><strong>{t("new.forbiddenTitle")}</strong></p>
          <p>
            {t.rich("new.forbiddenBody", { permission: (chunks) => <code>{chunks}</code> })}
          </p>
        </div>
      ) : failed || catalog === null ? (
        <div className="notice" role="alert">
          <p><strong>{t("new.failedTitle")}</strong></p>
          <p>
            {t("new.failedBody")}
          </p>
        </div>
      ) : access === "writer" ? (
        <>
          <CadenceLatency className="pw-latency" parts={pwLatencyFrom(catalog.cadence, i18n)} canLink={canLink} />
          <WatchEditor catalog={catalog} mode="create" />
        </>
      ) : (
        <>
          <PropertyWatchWriteNotice unknown={access === "unknown"} />
          <p className="field-hint"><Link href="/behavior/watches">{t("new.back")}</Link></p>
        </>
      )}
    </>
  );
}
