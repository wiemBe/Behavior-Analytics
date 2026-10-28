// Which IPS sources are configured, and which are collected right now.

import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { AddIpsSourcePicker } from "@/app/admin/ips-sources/AddIpsSourcePicker";
import { IpsSourceTable } from "@/app/admin/ips-sources/IpsSourceTable";
import { readError } from "@/app/admin/ips-sources/readError";
import { PageHeader } from "@/components/ui/PageHeader";
import { guideFor } from "@/lib/guideRoutes";
import { fleetApi, ipsApi, type IpsSourceStatus } from "@/lib/api";
import { getBehaviorI18n } from "@/lib/behaviorI18n.server";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("ips.page");
  return { title: t("metaTitle") };
}

/** The candidates route needs the instance. A profile names it; else the fleet does. */
async function instanceFor(sources: IpsSourceStatus[]): Promise<string | null> {
  if (sources.length > 0) return sources[0].instance_id;
  try {
    return (await fleetApi.sources({ limit: 1 })).items[0]?.instance_id ?? null;
  } catch {
    return null;
  }
}

export default async function IpsSourcesPage() {
  const t = await getTranslations("ips.page");
  const i18n = await getBehaviorI18n();
  let sources: IpsSourceStatus[] = [];
  let error: string | null = null;
  try {
    sources = await ipsApi.sources();
  } catch (err) {
    error = readError(err, i18n.t("ips.read.nouns.sources"), i18n);
  }
  const instanceId = error ? null : await instanceFor(sources);

  return (
    <>
      <PageHeader guide={guideFor("/admin/ips-sources")} title={t("title")} description={t("description")} />

      {error ? (
        <div className="notice" role="alert">
          <p>
            <strong>{t("loadFailedTitle")}</strong>
          </p>
          <p>{error}</p>
        </div>
      ) : sources.length === 0 ? (
        <>
          <div className="notice" role="status">
            <p>
              <strong>{t("emptyTitle")}</strong>
            </p>
            <p>{t("emptyBody")}</p>
          </div>
          <AddIpsSourcePicker instanceId={instanceId} />
        </>
      ) : (
        <div className="stack-6">
          <AddIpsSourcePicker instanceId={instanceId} />
          <IpsSourceTable sources={sources} />
        </div>
      )}
    </>
  );
}
