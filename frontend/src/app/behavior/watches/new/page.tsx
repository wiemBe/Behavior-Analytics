import type { Metadata } from "next";
import Link from "next/link";

import { CadenceLatency, canReadSearchCadence } from "@/components/cadence/CadenceLatency";
import { PropertyWatchWriteNotice } from "@/components/propertywatch/PropertyWatchWriteNotice";
import { WatchEditor } from "@/components/propertywatch/WatchEditor";
import {
  propertyWatchAccessFrom,
  type PropertyWatchAccess,
} from "@/components/propertywatch/authoring";
import { pwLatencyFrom } from "@/components/propertywatch/pwFormat";
import { PageHeader } from "@/components/ui/PageHeader";
import { ApiError, api, type CallerInfo, type PropertyWatchCatalog } from "@/lib/api";

import "@/components/ui/controls.css";

export const metadata: Metadata = { title: "New property watch" };

export default async function NewPropertyWatchPage() {
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
      <PageHeader title="New property watch" />
      {forbidden ? (
        <div className="notice" role="alert">
          <p><strong>The property-watch catalog is forbidden for this account.</strong></p>
          <p>
            Reading it needs <code>read:property_watches</code>. Ask an admin to grant a role that
            includes it. Nothing was changed.
          </p>
        </div>
      ) : failed || catalog === null ? (
        <div className="notice" role="alert">
          <p><strong>The property-watch editor could not be loaded.</strong></p>
          <p>
            The catalog of switches and bounds is unavailable. Check the backend, then reload the
            page. Nothing was changed.
          </p>
        </div>
      ) : access === "writer" ? (
        <>
          <CadenceLatency className="pw-latency" parts={pwLatencyFrom(catalog.cadence)} canLink={canLink} />
          <WatchEditor catalog={catalog} mode="create" />
        </>
      ) : (
        <>
          <PropertyWatchWriteNotice unknown={access === "unknown"} />
          <p className="field-hint"><Link href="/behavior/watches">Back to property watches</Link></p>
        </>
      )}
    </>
  );
}
