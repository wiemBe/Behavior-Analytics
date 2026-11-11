// The frozen open rows of a handover, in the order they were frozen (the queue's order). Used by
// the record and by nothing that can change a row: a handover is a record, not a live list.

import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";

import { TableScroll } from "@/components/ui/TableScroll";
import { UtcTime } from "@/components/ui/UtcTime";
import { enumLabel } from "@/i18n/enumLabels";
import type { Locale } from "@/i18n/locale";
import { asTextTranslator } from "@/i18n/textTranslator";
import { sevTone, type HandoverItem } from "@/lib/api";
import { alertTitleText } from "@/lib/alertTitle";
import { rowFlags } from "@/lib/handover";
import { formatAge } from "@/lib/time";

/** Compose only: a Watch checkbox in front of each row. */
export interface ItemSelect {
  checked: (item: HandoverItem) => boolean;
  disabled: (item: HandoverItem) => boolean;
  onToggle: (item: HandoverItem) => void;
}

export function ItemsTable({
  items,
  label,
  select,
  preview = false,
}: {
  items: HandoverItem[];
  label: string;
  select?: ItemSelect;
  /** The rows are not written yet: their age is as of now, not as of a record. */
  preview?: boolean;
}) {
  const c = useTranslations("handover.compose");
  const t = useTranslations("handover.record");
  const q = useTranslations("queue");
  const say = asTextTranslator(useTranslations());
  const locale = useLocale() as Locale;

  const stateText = (state: string) => (say.has?.(`queue.state.${state}`) ? say(`queue.state.${state}`) : state);

  return (
    <TableScroll label={label}>
      <table className="table-dense ho-table" aria-label={label}>
        <thead>
          <tr>
            {select && <th scope="col">{c("watchColumn")}</th>}
            <th scope="col">{t("table.severity")}</th>
            <th scope="col">{t("table.item")}</th>
            <th scope="col">{t("table.state")}</th>
            <th scope="col">{t("table.owner")}</th>
            <th scope="col">{t("table.flags")}</th>
            <th scope="col" className="num">
              {t("table.waiting")}
            </th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => {
            const title = alertTitleText(item, locale, say);
            const flags = rowFlags(item);
            return (
              <tr key={`${item.kind}:${item.id}`}>
                {select && (
                  <td className="ho-check">
                    <input
                      type="checkbox"
                      checked={select.checked(item)}
                      disabled={select.disabled(item)}
                      onChange={() => select.onToggle(item)}
                      aria-label={c("watchRow", { title })}
                    />
                  </td>
                )}
                <td>
                  {item.severity ? (
                    <span className={`pill ${sevTone(item.severity)}`}>
                      {enumLabel(say, "severityPill", item.severity)}
                    </span>
                  ) : (
                    <span className="muted" title={t("table.noSeverity")}>
                      —
                    </span>
                  )}
                </td>
                <td className="ho-title">
                  <Link href={item.link}>{title}</Link>
                  <span className="ho-sub muted">{q(`sources.kind.${item.kind}`)}</span>
                </td>
                <td>{stateText(item.state)}</td>
                <td className="ho-owner">
                  {item.owner === null ? (
                    <span className="muted">—</span>
                  ) : item.owner_kind === "qradar" ? (
                    q("table.ownerQradar", { name: item.owner })
                  ) : (
                    item.owner
                  )}
                </td>
                <td className="ho-flags">
                  {flags.length === 0 ? (
                    <span className="muted" aria-hidden="true">
                      —
                    </span>
                  ) : (
                    flags.map((f) => (
                      <span key={f} className={`ho-flag ho-flag-${f}`}>
                        {t(`flag.${f}`)}
                      </span>
                    ))
                  )}
                </td>
                <td className="num ho-waiting">
                  <UtcTime
                    value={item.waiting_since}
                    variant="compact"
                    age={t(preview ? "table.waitingNow" : "table.waitedAt", { age: formatAge(item.age_seconds) })}
                    ageClassName="muted ho-age"
                  />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </TableScroll>
  );
}
