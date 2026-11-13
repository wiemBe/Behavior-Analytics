// The catalogue events of one source over the window, which also picks the event the chart and the
// names below show. Each row is a link, so the choice is in the URL. A count is the sum of the
// buckets measured whole; "at least" and the dash follow the rules in `identityFormat.ts`.

import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";

import { TableScroll } from "@/components/ui/TableScroll";
import { formatCount, type FormatLocale } from "@/lib/format";

import {
  MISSING,
  eventKey,
  identityHref,
  isVolumeEvent,
  type EventTotal,
  type IdentityRange,
} from "./identityFormat";

import "@/components/ui/controls.css";
import "./identity.css";

export function IdentityEventsTable({
  path,
  range,
  totals,
  selected,
}: {
  path: string;
  range: IdentityRange;
  totals: EventTotal[];
  selected: number | null;
}) {
  const t = useTranslations("identity.eventsTable");
  const events = useTranslations("identity.events");
  const locale = useLocale() as FormatLocale;
  return (
    <TableScroll label={t("label")}>
      <table className="table-dense">
        <caption className="sr-only">{t("caption")}</caption>
        <thead>
          <tr>
            <th scope="col">{t("event")}</th>
            <th scope="col" className="num">
              {t("count")}
              <span className="cell-sub">{t("countSub")}</span>
            </th>
            <th scope="col">{t("measured")}</th>
          </tr>
        </thead>
        <tbody>
          {totals.map((x) => (
            <tr key={x.eventId} aria-current={x.eventId === selected ? "true" : undefined}>
              <td className="cell-name">
                <Link
                  href={identityHref(path, { range, event: x.eventId })}
                  aria-current={x.eventId === selected ? "page" : undefined}
                >
                  <span className="num">{x.eventId}</span> {events(eventKey(x.eventId))}
                </Link>
                <span className="cell-sub">{isVolumeEvent(x.eventId) ? t("volume") : t("rare")}</span>
              </td>
              <td className="num">
                {x.total === null ? (
                  <span className="muted">
                    {MISSING}
                    <span className="sr-only"> {t("notMeasured")}</span>
                  </span>
                ) : x.atLeast ? (
                  <>
                    ≥ {formatCount(x.total, locale)}
                    <span className="sr-only"> {t("atLeast")}</span>
                  </>
                ) : (
                  formatCount(x.total, locale)
                )}
              </td>
              <td>
                {x.unmeasured === 0
                  ? t("allMeasured", { slots: formatCount(x.slots, locale) })
                  : t("someUnmeasured", {
                      missing: formatCount(x.unmeasured, locale),
                      slots: formatCount(x.slots, locale),
                    })}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableScroll>
  );
}
