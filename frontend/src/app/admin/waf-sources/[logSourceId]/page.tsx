// One WAF source: does its field mapping work, and what will collection read.
//
// A source with no profile opens the same editor in create mode, prefilled per
// docs/FIELD-MAPPING.md §FM.7 and saying where the prefill came from.

import type { Metadata } from "next";

import { ProfileEditor, type Prefill } from "@/app/admin/waf-sources/ProfileEditor";
import { readError } from "@/app/admin/waf-sources/readError";
import { PageHeader } from "@/components/ui/PageHeader";
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

export const metadata: Metadata = {
  title: "WAF source",
};

function Problem({ title, text }: { title: string; text: string }) {
  return (
    <>
      <PageHeader title="WAF source" />
      <div className="notice" role="alert">
        <p>
          <strong>{title}</strong>
        </p>
        <p>{text}</p>
      </div>
    </>
  );
}

export default async function WafSourcePage({
  params,
  searchParams,
}: {
  params: Promise<{ logSourceId: string }>;
  /** `instance` is set by the picker from the candidate's `instance_id`. */
  searchParams?: Promise<{ instance?: string }>;
}) {
  const { logSourceId } = await params;
  const hinted = (await searchParams)?.instance ?? null;

  let schema;
  try {
    schema = await api.wafMappingSchema();
  } catch (err) {
    return (
      <Problem
        title="This WAF source could not be shown."
        text={readError(err, "the field-mapping schema")}
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
          title="This WAF source could not be shown."
          text={readError(err, "this source’s profile")}
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
          title="This log source does not exist."
          text="No log source has this id, so there is nothing to configure. Choose one from the WAF sources list."
        />
      ) : (
        <Problem
          title="This WAF source could not be shown."
          text={readError(err, "this log source")}
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
          origin: `Prefilled from ${seeded.log_source_name} (version ${seeded.profile_version}), the most recently validated profile on this console.`,
        }
      : {
          draft: draftFromDefaults(schema),
          origin:
            "Prefilled from the schema defaults, because no validated profile on this console could seed it.",
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
