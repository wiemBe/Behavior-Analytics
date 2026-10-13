// One IPS source: is its field mapping right, and may it be collected.
//
// A source with no profile opens the same editor in create mode, filled from
// the schema defaults; a preset is chosen in the editor.

import type { Metadata } from "next";

import { IpsProfileEditor } from "@/app/admin/ips-sources/IpsProfileEditor";
import { readError } from "@/app/admin/ips-sources/readError";
import { PageHeader } from "@/components/ui/PageHeader";
import {
  ApiError,
  api,
  ipsApi,
  type IpsMappingSchemaOut,
  type IpsProfileHistoryEntryOut,
  type IpsProfileOut,
  type LogSourceDetail,
  type PropertyCatalogOut,
} from "@/lib/api";

export const metadata: Metadata = {
  title: "IPS source",
};

function Problem({ title, text }: { title: string; text: string }) {
  return (
    <>
      <PageHeader title="IPS source" />
      <div className="notice" role="alert">
        <p>
          <strong>{title}</strong>
        </p>
        <p>{text}</p>
      </div>
    </>
  );
}

export default async function IpsSourcePage({
  params,
}: {
  params: Promise<{ logSourceId: string }>;
}) {
  const { logSourceId } = await params;

  let schema: IpsMappingSchemaOut;
  try {
    schema = await ipsApi.mappingSchema();
  } catch (err) {
    return (
      <Problem
        title="This IPS source could not be shown."
        text={readError(err, "the field-mapping schema")}
      />
    );
  }

  let profile: IpsProfileOut | null = null;
  try {
    profile = await ipsApi.profile(logSourceId);
  } catch (err) {
    if (!(err instanceof ApiError && err.status === 404)) {
      return (
        <Problem
          title="This IPS source could not be shown."
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
          text="No log source has this id, so there is nothing to configure. Choose one from the IPS sources list."
        />
      ) : (
        <Problem
          title="This IPS source could not be shown."
          text={readError(err, "this log source")}
        />
      );
    }
  }

  const [catalog, history] = await Promise.all([
    ipsApi.propertyCatalog(logSourceId).catch((): PropertyCatalogOut | null => null),
    profile
      ? ipsApi.profileHistory(logSourceId).catch((): IpsProfileHistoryEntryOut[] | null => null)
      : Promise.resolve([] as IpsProfileHistoryEntryOut[]),
  ]);

  const source = {
    id: logSourceId,
    name: profile?.log_source_name ?? logSource?.name ?? logSourceId,
    qradarId: profile?.qradar_log_source_id ?? logSource?.qradar_id ?? 0,
    typeName: profile?.log_source_type_name ?? logSource?.type_name ?? null,
  };

  return (
    <IpsProfileEditor
      schema={schema}
      source={source}
      profile={profile}
      catalog={catalog}
      history={history}
    />
  );
}
