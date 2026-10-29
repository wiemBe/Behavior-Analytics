// One WAF source: does its field mapping work, and what will collection read.
//
// A source with no profile opens the same editor in create mode, prefilled per
// docs/FIELD-MAPPING.md §FM.7 and saying where the prefill came from.

import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { ProfileEditor, type Prefill } from "@/app/admin/waf-sources/ProfileEditor";
import { readError } from "@/app/admin/waf-sources/readError";
import { PageHeader } from "@/components/ui/PageHeader";
import { guideFor } from "@/lib/guideRoutes";
import {
  ApiError,
  api,
  type LogSourceDetail,
  type PropertyCatalogOut,
  type WafProfileHistoryEntryOut,
  type WafProfileOut,
  type WafSourceStatus,
} from "@/lib/api";
import {
  draftFromDefaults,
  draftFromProfile,
  pickPrefillSource,
  resolveInstanceId,
} from "@/lib/fieldMapping";
import { getBehaviorI18n } from "@/lib/behaviorI18n.server";
import { withRouteMessages } from "@/i18n/RouteMessages";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("waf.meta");
  return { title: t("adminSource") };
}

function Problem({ title, text, pageTitle }: { title: string; text: string; pageTitle: string }) {
  return (
    <>
      <PageHeader guide={guideFor("/admin/waf-sources/[logSourceId]")} title={pageTitle} />
      <div className="notice" role="alert">
        <p>
          <strong>{title}</strong>
        </p>
        <p>{text}</p>
      </div>
    </>
  );
}

async function WafSourcePage({
  params,
  searchParams,
}: {
  params: Promise<{ logSourceId: string }>;
  /** `instance` is set by the picker from the candidate's `instance_id`. */
  searchParams?: Promise<{ instance?: string }>;
}) {
  const t = await getTranslations("waf.admin.page");
  const admin = await getTranslations("waf.admin.read");
  const i18n = await getBehaviorI18n();
  const { logSourceId } = await params;
  const hinted = (await searchParams)?.instance ?? null;

  let schema;
  try {
    schema = await api.wafMappingSchema();
  } catch (err) {
    return (
      <Problem
        pageTitle={t("title")}
        title={t("couldNotShow")}
        text={readError(err, admin("whatSchema"), i18n)}
      />
    );
  }

  let profile: WafProfileOut | null = null;
  try {
    profile = await api.wafProfile(logSourceId);
  } catch (err) {
    if (!(err instanceof ApiError && err.status === 404)) {
      return (
        <Problem
          pageTitle={t("title")}
          title={t("couldNotShow")}
          text={readError(err, admin("whatProfile"), i18n)}
        />
      );
    }
  }

  let logSource: LogSourceDetail | null = null;
  if (profile === null) {
    try {
      logSource = await api.logSource(logSourceId);
    } catch (err) {
      return err instanceof ApiError && err.status === 404 ? (
        <Problem
          pageTitle={t("title")}
          title={t("noLogSourceTitle")}
          text={t("noLogSourceText")}
        />
      ) : (
        <Problem
          pageTitle={t("title")}
          title={t("couldNotShow")}
          text={readError(err, admin("whatLogSource"), i18n)}
        />
      );
    }
  }

  const [catalog, history, siblings] = await Promise.all([
    api.wafPropertyCatalog(logSourceId).catch((): PropertyCatalogOut | null => null),
    profile
      ? api.wafProfileHistory(logSourceId).catch((): WafProfileHistoryEntryOut[] | null => null)
      : Promise.resolve([] as WafProfileHistoryEntryOut[]),
    api.wafSources().catch((): WafSourceStatus[] => []),
  ]);

  let prefill: Prefill | null = null;
  if (profile === null) {
    const instanceId = resolveInstanceId(hinted ?? logSource?.instance_id, siblings);
    const seed = pickPrefillSource(siblings, instanceId, logSourceId);
    let seeded: WafProfileOut | null = null;
    if (seed) {
      try {
        seeded = await api.wafProfile(seed.log_source_id);
      } catch {
        seeded = null;
      }
    }
    prefill = seeded
      ? {
          draft: { ...draftFromProfile(schema, seeded), productLabel: "" },
          origin: {
            kind: "seeded",
            name: seeded.log_source_name,
            version: seeded.profile_version,
          },
        }
      : {
          draft: draftFromDefaults(schema),
          origin: { kind: "defaults" },
        };
  }

  const source = {
    id: logSourceId,
    name: profile?.log_source_name ?? logSource?.name ?? logSourceId,
    qradarId: profile?.qradar_log_source_id ?? logSource?.qradar_id ?? 0,
    typeName: profile?.log_source_type_name ?? logSource?.type_name ?? null,
    instanceId: profile?.instance_id ?? hinted ?? logSource?.instance_id ?? null,
  };

  return (
    <ProfileEditor
      schema={schema}
      source={source}
      profile={profile}
      catalog={catalog}
      history={history}
      siblings={siblings}
      prefill={prefill}
    />
  );
}

export default withRouteMessages("/admin/waf-sources/[logSourceId]", WafSourcePage);
