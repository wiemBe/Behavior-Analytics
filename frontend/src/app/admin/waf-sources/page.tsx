// Which WAF sources are collected, and which need attention.

import type { Metadata } from "next";

import { AddSourcePicker } from "@/app/admin/waf-sources/AddSourcePicker";
import { WafSourceTable } from "@/app/admin/waf-sources/WafSourceTable";
import { readError } from "@/app/admin/waf-sources/readError";
import { PageHeader } from "@/components/ui/PageHeader";
import { api, type MappingSchemaOut, type WafSourceStatus } from "@/lib/api";

export const metadata: Metadata = {
  title: "WAF sources",
};

export default async function WafSourcesPage() {
  let sources: WafSourceStatus[] = [];
  let error: string | null = null;
  try {
    sources = await api.wafSources();
  } catch (err) {
    error = readError(err, "WAF sources");
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
        title="WAF sources"
        description="Which WAF sources are collected, and which need attention. Times are UTC."
      />

      {error ? (
        <div className="notice" role="alert">
          <p>
            <strong>WAF sources could not be shown.</strong>
          </p>
          <p>{error}</p>
        </div>
      ) : sources.length === 0 ? (
        <>
          <div className="notice" role="status">
            <p>
              <strong>No log source is configured as a WAF source.</strong>
            </p>
            <p>A log source becomes one when its fields are mapped.</p>
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
