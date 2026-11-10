// The merged timeline: one list, newest first, in the server's order. A row says when (absolute
// UTC, then the age), which lane stored it, what the entity was in that record, the record's own
// title as a link to the page that owns it, its severity as the owning list shows it, and how the
// mention was measured when that is not exact. No re-sort on the client: a re-sorted page of a
// longer list would pretend to be the head of it.

import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";

import { TableScroll } from "@/components/ui/TableScroll";
import { UtcTime } from "@/components/ui/UtcTime";
import { renderBackendCode } from "@/i18n/backendMessage";
import { enumLabel } from "@/i18n/enumLabels";
import type { Locale } from "@/i18n/locale";
import { asTextTranslator } from "@/i18n/textTranslator";
import { sevTone, type EntityTimelineItem } from "@/lib/api";
import { isSameOriginPath } from "@/lib/entities";
import { formatAge } from "@/lib/time";

const ROLES = ["SOURCE_IP", "DESTINATION_IP", "HOST", "USER"];
const BASES = ["EXACT", "TOP_N", "ADMITTED"];

/** The record's title: its message code and parameters when it has them, else the stored sentence. */
export function itemTitle(
  item: Pick<EntityTimelineItem, "title" | "title_code" | "title_params">,
  locale: Locale,
  say: ReturnType<typeof asTextTranslator>,
): string {
  if (item.title_code) {
    const params = item.title_params ?? {};
    const text = renderBackendCode({ code: item.title_code, params }, locale, say);
    if (text) return text;
  }
  return item.title;
}

export function TimelineTable({
  items,
  nowMs,
}: {
  items: EntityTimelineItem[];
  /** The instant the ages count from. */
  nowMs: number;
}) {
  const t = useTranslations("entities");
  const say = asTextTranslator(useTranslations());
  const locale = useLocale() as Locale;

  return (
    <TableScroll label={t("items.label")}>
      <table className="table-dense entity-table" aria-label={t("items.label")}>
        <thead>
          <tr>
            <th scope="col">{t("items.time")}</th>
            <th scope="col">{t("items.lane")}</th>
            <th scope="col">{t("items.role")}</th>
            <th scope="col">{t("items.what")}</th>
            <th scope="col" className="num" title={t("items.severityTitle")}>
              {t("items.severity")}
            </th>
            <th scope="col">{t("items.basis")}</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item, index) => {
            const title = itemTitle(item, locale, say);
            const age = (nowMs - Date.parse(item.occurred_at)) / 1000;
            const basisKey = item.measurement;
            return (
              <tr key={`${index}:${item.source}:${item.occurred_at}:${item.role}`}>
                <td className="entity-time">
                  <UtcTime
                    value={item.occurred_at}
                    variant="compact"
                    age={Number.isFinite(age) && age >= 0 ? t("items.age", { age: formatAge(age) }) : null}
                    ageClassName="muted entity-age"
                  />
                  {item.occurred_end && (
                    <span className="cell-sub">
                      {t.rich("items.until", {
                        time: () => <UtcTime value={item.occurred_end} variant="compact" />,
                      })}
                    </span>
                  )}
                </td>
                <td>
                  <span className="pill pill-wrap">{t(`lanes.name.${item.source}`)}</span>
                </td>
                <td className="entity-role">{ROLES.includes(item.role) ? say(`entities.role.${item.role}`) : t("role.unknown")}</td>
                <td className="entity-title">
                  {/* Text, never markup: the title comes from stored QRadar data. */}
                  {isSameOriginPath(item.href) ? <Link href={item.href}>{title}</Link> : title}
                </td>
                <td className="num">
                  {typeof item.severity === "number" ? (
                    <span title={t("items.numericSeverity", { value: item.severity })}>{item.severity}</span>
                  ) : item.severity ? (
                    <span className={`pill ${sevTone(item.severity)}`}>
                      {enumLabel(say, "severityPill", item.severity)}
                    </span>
                  ) : (
                    <span className="muted">—</span>
                  )}
                </td>
                <td>
                  {basisKey === null ? (
                    <span className="muted">—</span>
                  ) : BASES.includes(basisKey) ? (
                    <span
                      className={basisKey === "EXACT" ? "muted" : "pill pill-quiet warn"}
                      title={basisKey === "EXACT" ? undefined : say(`entities.basis.${basisKey}_TITLE`)}
                    >
                      {say(`entities.basis.${basisKey}`)}
                    </span>
                  ) : (
                    <span className="pill pill-quiet warn" title={t("basis.unknownTitle")}>
                      {t("basis.unknown")}
                    </span>
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
