// The usernames stored for one event ID over the window.
//
// Only stored rows: the top 20 per bucket and, for 4625 and 4740, names with at least 3 events in
// the bucket. A figure here is the sum of the stored counts, so it is at least what the name
// did, never its total; the header and the note say so. A username is attacker-controlled text,
// rendered as text. Each name opens the entity timeline.

import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";

import { TableScroll } from "@/components/ui/TableScroll";
import { entityHref } from "@/lib/entities";
import { formatCount, type FormatLocale } from "@/lib/format";

import { TOP_N, VOLUME_NAME_FLOOR, isVolumeEvent, type NameRow } from "./identityFormat";

import "@/components/ui/controls.css";
import "./identity.css";

/** Names listed at most; the rest are counted, not dropped silently. */
export const NAMES_SHOWN = 50;

export function IdentityNamesTable({
  eventId,
  names,
  incomplete,
}: {
  eventId: number;
  names: NameRow[];
  /** A bucket in the window had its name list cut or not collected. */
  incomplete: boolean;
}) {
  const t = useTranslations("identity.names");
  const locale = useLocale() as FormatLocale;
  if (names.length === 0) {
    return (
      <div className="notice" role="status">
        <p>
          <strong>{t("emptyTitle")}</strong>
        </p>
        <p>{incomplete ? t("emptyIncomplete") : t("emptyText")}</p>
      </div>
    );
  }
  const shown = names.slice(0, NAMES_SHOWN);
  return (
    <div className="stack-6">
      {incomplete && (
        <p className="banner warn" role="status">
          {t("incomplete")}
        </p>
      )}
      <TableScroll label={t("label")}>
        <table className="table-dense">
          <caption className="sr-only">{t("caption", { event: eventId })}</caption>
          <thead>
            <tr>
              <th scope="col">{t("username")}</th>
              <th scope="col" className="num">
                {t("stored")}
                <span className="cell-sub">{t("storedSub")}</span>
              </th>
              <th scope="col" className="num">
                {t("buckets")}
              </th>
            </tr>
          </thead>
          <tbody>
            {shown.map((n) => (
              <tr key={n.username}>
                <td className="cell-name identity-name">
                  <Link href={entityHref({ kind: "user", value: n.username })}>{n.username}</Link>
                  {n.cut && <span className="cell-sub">{t("cut")}</span>}
                </td>
                <td className="num">{formatCount(n.stored, locale)}</td>
                <td className="num">{formatCount(n.buckets, locale)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableScroll>
      {names.length > shown.length && (
        <p className="subtitle">
          {t("more", { shown: shown.length, total: formatCount(names.length, locale) })}
        </p>
      )}
      <p className="subtitle">
        {isVolumeEvent(eventId) ? t("noteVolume", { floor: VOLUME_NAME_FLOOR }) : t("noteRare")}{" "}
        {t("noteTop", { top: TOP_N })}
      </p>
    </div>
  );
}
