// Which IPS sources are configured, and which are collected right now.

import type { Metadata } from "next";

import { AddIpsSourcePicker } from "@/app/admin/ips-sources/AddIpsSourcePicker";
import { IpsSourceTable } from "@/app/admin/ips-sources/IpsSourceTable";
import { readError } from "@/app/admin/ips-sources/readError";
import { PageHeader } from "@/components/ui/PageHeader";
import { fleetApi, ipsApi, type IpsSourceStatus } from "@/lib/api";

export const metadata: Metadata = {
  title: "IPS sources",
};

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
  let sources: IpsSourceStatus[] = [];
  let error: string | null = null;
  try {
    sources = await ipsApi.sources();
  } catch (err) {
    error = readError(err, "IPS sources");
  }
  const instanceId = error ? null : await instanceFor(sources);

  return (
    <>
      <PageHeader
        title="IPS sources"
        description="Which IPS sources are configured, and which are collected right now. Times are UTC."
      />

      {error ? (
        <div className="notice" role="alert">
          <p>
            <strong>IPS sources could not be shown.</strong>
          </p>
          <p>{error}</p>
        </div>
      ) : sources.length === 0 ? (
        <>
          <div className="notice" role="status">
            <p>
              <strong>No IPS source has a profile yet.</strong>
            </p>
            <p>
              Adding one starts from a preset for Cisco Firepower Threat Defense or Palo Alto
              THREAT. The preset only fills in defaults; the profile still has to validate before
              anything is collected.
            </p>
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
