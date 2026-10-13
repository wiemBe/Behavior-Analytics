// The audit trail of one profile: when, who, and what changed.

import { TableScroll } from "@/components/ui/TableScroll";
import type { MappingSchemaOut, WafProfileHistoryEntryOut } from "@/lib/api";
import { MISSING, historyActionLabel, historyChanges, reasonText } from "@/lib/fieldMapping";
import { formatDateTime } from "@/lib/health";

function Change({ from, to }: { from: string | null; to: string | null }) {
  return (
    <>
      {from === null ? <span className="muted">not mapped</span> : <code>{from}</code>}
      {" → "}
      {to === null ? <span className="muted">not mapped</span> : <code>{to}</code>}
    </>
  );
}

export function MappingHistory({
  schema,
  entries,
  actionLabel = historyActionLabel,
  outcomeNote,
}: {
  schema: MappingSchemaOut;
  /** Newest first, as served. */
  entries: WafProfileHistoryEntryOut[];
  /** A domain's words for its audit actions; the default covers `waf.profile.*`. */
  actionLabel?: (action: string) => string;
  /** Replaces the plain "Outcome: …" line for an outcome that needs a sentence. */
  outcomeNote?: (entry: WafProfileHistoryEntryOut) => string | null;
}) {
  if (entries.length === 0) {
    return (
      <p className="fm-empty">
        No changes are recorded for this profile yet. Creating it, changing its mapping,
        validating it and enabling it each add a line here.
      </p>
    );
  }
  return (
    <TableScroll label="Profile history">
      <table className="table-dense">
        <caption className="sr-only">
          Changes to this profile, newest first. Times are UTC.
        </caption>
        <thead>
          <tr>
            <th scope="col">Time (UTC)</th>
            <th scope="col">Actor</th>
            <th scope="col">What changed</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((e, i) => {
            const changes = historyChanges(schema, e);
            return (
              <tr key={`${e.at}-${e.action}-${i}`}>
                <td className="nowrap">
                  <time dateTime={e.at}>{formatDateTime(e.at)}</time>
                </td>
                <td className="cell-name">{e.actor || MISSING}</td>
                <td>
                  <strong className="fm-event">{actionLabel(e.action)}</strong>
                  {e.profile_version != null && <span className="muted"> · v{e.profile_version}</span>}
                  {e.outcome && (
                    <span className="cell-sub">
                      {outcomeNote?.(e) ?? `Outcome: ${e.outcome.toLowerCase()}`}
                    </span>
                  )}
                  {changes.length > 0 && (
                    <ul className="fm-changes" aria-label="Mapping changes">
                      {changes.map((c) => (
                        <li key={c.key}>
                          {c.label}: <Change from={c.from} to={c.to} />
                        </li>
                      ))}
                    </ul>
                  )}
                  {e.reasons && e.reasons.length > 0 && (
                    <ul className="fm-changes" aria-label="Reasons">
                      {e.reasons.map((r) => (
                        <li key={r}>{reasonText(r, schema, schema.min_coverage_ratio)}</li>
                      ))}
                    </ul>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </TableScroll>
  );
}
