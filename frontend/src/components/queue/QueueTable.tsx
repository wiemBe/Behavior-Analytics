// The queue's rows, in the server's order (severity, then the longest wait). No client re-sort:
// a re-sorted page of a longer list would pretend to be the head of the queue.

import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";

import { TableScroll } from "@/components/ui/TableScroll";
import { UtcTime } from "@/components/ui/UtcTime";
import { enumLabel } from "@/i18n/enumLabels";
import type { Locale } from "@/i18n/locale";
import { asTextTranslator } from "@/i18n/textTranslator";
import { sevTone, type QueueItem } from "@/lib/api";
import { alertTitleText } from "@/lib/alertTitle";
import { formatAge } from "@/lib/time";

export function QueueTable({ items }: { items: QueueItem[] }) {
  const t = useTranslations("queue");
  const say = asTextTranslator(useTranslations());
  const locale = useLocale() as Locale;

  const stateText = (state: string) => (say.has?.(`queue.state.${state}`) ? say(`queue.state.${state}`) : state);

  return (
    <TableScroll label={t("table.label")}>
      <table className="table-dense queue-table" aria-label={t("table.label")}>
        <thead>
          <tr>
            <th scope="col">{t("table.change")}</th>
            <th scope="col">{t("table.severity")}</th>
            <th scope="col">{t("table.kind")}</th>
            <th scope="col">{t("table.title")}</th>
            <th scope="col">{t("table.state")}</th>
            <th scope="col">{t("table.owner")}</th>
            <th scope="col" className="num">
              {t("table.waiting")}
            </th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => {
            const title = alertTitleText(item, locale, say);
            const kind = t(`sources.kind.${item.kind}`);
            const rowClass =
              item.change === "NEW" ? "queue-row-new" : item.change === "UPDATED" ? "queue-row-updated" : "";
            const owner =
              item.owner === null
                ? item.kind === "source_review" || item.kind === "catchall_sender"
                  ? null
                  : t("table.unassigned")
                : item.owner_kind === "qradar"
                  ? t("table.ownerQradar", { name: item.owner })
                  : item.owner;
            return (
              <tr key={`${item.kind}:${item.id}`} className={rowClass || undefined}>
                <td className="queue-change">
                  {item.change ? (
                    <span className="queue-change-label">{t(`change.${item.change}`)}</span>
                  ) : (
                    <span className="muted" aria-hidden="true">
                      —
                    </span>
                  )}
                </td>
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
                <td className="queue-kind">
                  {kind}
                  {item.family && <span className="queue-sub muted">{t(`filters.family_${item.family}`)}</span>}
                </td>
                <td className="queue-title">
                  <Link
                    href={item.link}
                    aria-label={
                      item.change === "NEW"
                        ? t("table.rowNew", { title })
                        : item.change === "UPDATED"
                          ? t("table.rowUpdated", { title })
                          : undefined
                    }
                  >
                    {title}
                  </Link>
                  {item.alert && (
                    <>
                      {" "}
                      <Link
                        href={`/alerts/${item.alert.id}`}
                        className="pill pill-quiet queue-alert-chip"
                        aria-label={t("table.alertChipLabel", { status: stateText(item.alert.status), title })}
                      >
                        {t("table.alertChip", { status: stateText(item.alert.status) })}
                      </Link>
                    </>
                  )}
                </td>
                <td>
                  {stateText(item.state)}
                  {item.triage && (
                    <span className="queue-sub muted">
                      {t("table.triage", {
                        status: say.has?.(`triage.status.${item.triage.status}`)
                          ? say(`triage.status.${item.triage.status}`)
                          : item.triage.status,
                      })}
                    </span>
                  )}
                </td>
                <td className="queue-owner">{owner ?? <span className="muted">—</span>}</td>
                <td className="num queue-waiting">
                  <UtcTime
                    value={item.waiting_since}
                    variant="compact"
                    age={t("table.ageAgo", { age: formatAge(item.age_seconds) })}
                    ageClassName="muted queue-age"
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
