import Link from "next/link";
// The IPS sources table: one row per profile, needing-attention first.

import { TableScroll } from "@/components/ui/TableScroll";
import type { IpsSourceStatus } from "@/lib/api";
import { collectionState, sortByAttention, validationState } from "@/lib/fieldMapping";
import { UtcTime } from "@/components/ui/UtcTime";

import "@/components/ui/controls.css";

export function IpsSourceTable({ sources }: { sources: IpsSourceStatus[] }) {
  const rows = sortByAttention(sources);
  return (
    <>
      <TableScroll label="IPS sources">
        <table className="table-dense">
          <caption className="sr-only">
            IPS sources with their QRadar type, profile version, validation result for that
            version, whether collection is enabled and running, and when they were last
            validated. Sources that need attention are listed first. Times are UTC.
          </caption>
          <thead>
            <tr>
              <th scope="col">Source</th>
              <th scope="col" className="num">
                Profile
                <span className="cell-sub">version</span>
              </th>
              <th scope="col">Validation</th>
              <th scope="col">Enabled</th>
              <th scope="col">Collection</th>
              <th scope="col" className="num">
                Last validated (UTC)
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((s) => {
              const v = validationState(s);
              const c = collectionState(s);
              return (
                <tr key={s.profile_id}>
                  <td className="cell-name ips-source-cell">
                    <Link href={`/admin/ips-sources/${s.log_source_id}`}>{s.log_source_name}</Link>
                    <span className="cell-sub">
                      QRadar id {s.qradar_log_source_id}
                      {s.log_source_type_name ? ` · ${s.log_source_type_name}` : ""}
                    </span>
                  </td>
                  <td className="num">v{s.profile_version}</td>
                  <td>
                    <span className={`fm-state ips-status ${v.tone}`}>{v.label}</span>
                  </td>
                  <td>{s.enabled ? "Enabled" : "Disabled"}</td>
                  <td>
                    <span className={`fm-state ${c.collecting ? "ok" : "warn"}`}>{c.label}</span>
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
          A profile change pauses collection from that source until the new version validates.
          “Version 5 not validated” therefore means the source is paused, not that it is unhealthy.
          A validation older than the current version is shown with the version it checked.
        </li>
        <li>A dash means the source has never been validated. It is not a failed validation.</li>
      </ul>
    </>
  );
}
