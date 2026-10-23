import Link from "next/link";
// The WAF sources table: one row per profile, needing-attention first.

import { TableScroll } from "@/components/ui/TableScroll";
import type { MappingSchemaOut, WafSourceStatus } from "@/lib/api";
import { collectionState, sortByAttention, validationState } from "@/lib/fieldMapping";
import { UtcTime } from "@/components/ui/UtcTime";

import "@/components/ui/controls.css";

export function WafSourceTable({
  sources,
  schema,
}: {
  sources: WafSourceStatus[];
  schema: MappingSchemaOut | null;
}) {
  const rows = sortByAttention(sources);
  return (
    <>
      <TableScroll label="WAF sources">
        <table className="table-dense">
          <caption className="sr-only">
            WAF sources with their mapping version, validation result, collection state and when
            they were last validated. Sources that need attention are listed first. Times are UTC.
          </caption>
          <thead>
            <tr>
              <th scope="col">Source</th>
              <th scope="col">Product</th>
              <th scope="col" className="num">
                Mapping
                <span className="cell-sub">version</span>
              </th>
              <th scope="col">Validation</th>
              <th scope="col">Collection</th>
              <th scope="col" className="num">
                Last validated (UTC)
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((s) => {
              const state = { ...s, fields: s.validation_fields };
              const v = validationState(state, schema);
              const c = collectionState(state);
              return (
                <tr key={s.profile_id}>
                  <td className="cell-name">
                    <Link href={`/admin/waf-sources/${s.log_source_id}`}>{s.log_source_name}</Link>
                    <span className="cell-sub">QRadar id {s.qradar_log_source_id}</span>
                  </td>
                  <td className="cell-name">
                    {s.product_label ?? (
                      <span className="muted">
                        —<span className="sr-only"> no product label</span>
                      </span>
                    )}
                  </td>
                  <td className="num">v{s.profile_version}</td>
                  <td>
                    <span className={`fm-state ${v.tone}`}>
                      {v.label}
                    </span>
                  </td>
                  <td>
                    <span className={`fm-state ${c.collecting ? "ok" : "warn"}`}>
                      {c.label}
                    </span>
                  </td>
                  <td className="num">
                    {s.validated_at ? (
                      <>
                        <UtcTime value={s.validated_at} variant="compact" className="nowrap" />
                        {s.validated_profile_version !== s.profile_version &&
                          s.validated_profile_version !== null && (
                            <span className="cell-sub">of v{s.validated_profile_version}</span>
                          )}
                      </>
                    ) : (
                      <span className="muted">
                        —<span className="sr-only"> never validated</span>
                      </span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </TableScroll>
      <ul className="table-notes" aria-label="How to read the table">
        <li>
          A mapping change pauses collection from that source until the new version validates.
          “Version 5 not validated” therefore means the source is paused, not that it is unhealthy.
        </li>
        <li>
          A dash means the source has never been validated. It is not a failed validation.
        </li>
      </ul>
    </>
  );
}
