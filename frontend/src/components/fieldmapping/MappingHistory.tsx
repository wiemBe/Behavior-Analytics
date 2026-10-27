// The audit trail of one profile: when, who, and what changed.

import { useTranslations } from "next-intl";

import { TableScroll } from "@/components/ui/TableScroll";
import type { MappingSchemaOut, WafProfileHistoryEntryOut } from "@/lib/api";
import { MISSING, historyActionLabel, historyChanges, reasonText } from "@/lib/fieldMapping";
import { UtcTime } from "@/components/ui/UtcTime";
import { versionLabel } from "@/lib/behaviorI18n";
import { plainCount } from "@/lib/figureText";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";

// Punctuation between parts of one line; the same in both languages.
const ARROW = " → ";
const MID = " · ";

function Change({ from, to }: { from: string | null; to: string | null }) {
  const t = useTranslations("fieldMapping.history");
  return (
    <>
      {from === null ? <span className="muted">{t("notMapped")}</span> : <code>{from}</code>}
      {ARROW}
      {to === null ? <span className="muted">{t("notMapped")}</span> : <code>{to}</code>}
    </>
  );
}

export function MappingHistory({
  schema,
  entries,
  actionLabel,
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
  const t = useTranslations("fieldMapping.history");
  const i18n = useBehaviorI18n();
  const labelOf = actionLabel ?? ((action: string) => historyActionLabel(action, i18n));
  if (entries.length === 0) {
    return <p className="fm-empty">{t("empty")}</p>;
  }
  return (
    <TableScroll label={t("tableLabel")}>
      <table className="table-dense">
        <caption className="sr-only">{t("caption")}</caption>
        <thead>
          <tr>
            <th scope="col">{t("time")}</th>
            <th scope="col">{t("actor")}</th>
            <th scope="col">{t("what")}</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((e, i) => {
            const changes = historyChanges(schema, e);
            return (
              <tr key={`${e.at}-${e.action}-${i}`}>
                <td className="nowrap">
                  <UtcTime value={e.at} />
                </td>
                <td className="cell-name">{e.actor || MISSING}</td>
                <td>
                  <strong className="fm-event">{labelOf(e.action)}</strong>
                  {e.profile_version != null && (
                    <span className="muted">
                      {MID}
                      {versionLabel(plainCount(e.profile_version, i18n.locale))}
                    </span>
                  )}
                  {e.outcome && (
                    <span className="cell-sub">
                      {outcomeNote?.(e) ?? t("outcome", { outcome: e.outcome.toLowerCase() })}
                    </span>
                  )}
                  {changes.length > 0 && (
                    <ul className="fm-changes" aria-label={t("changesLabel")}>
                      {changes.map((c) => (
                        <li key={c.key}>
                          {c.label}: <Change from={c.from} to={c.to} />
                        </li>
                      ))}
                    </ul>
                  )}
                  {e.reasons && e.reasons.length > 0 && (
                    <ul className="fm-changes" aria-label={t("reasonsLabel")}>
                      {e.reasons.map((r) => (
                        <li key={r}>
                          {reasonText(r, schema, schema.min_coverage_ratio, undefined, i18n)}
                        </li>
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
