// Search + status filter for the offense list.
//
// A plain GET form rather than a controlled client component: it needs no
// JavaScript, the resulting URL is shareable, and submitting resets paging
// naturally because `offset` is simply absent from the new query string.

import Link from "next/link";
import { useTranslations } from "next-intl";

export function OffenseFilters({
  search,
  status,
}: {
  search: string;
  status: string;
}) {
  const t = useTranslations("offenses.filters");
  return (
    <form className="filters" method="get" action="/offenses">
      <input
        type="search"
        name="search"
        defaultValue={search}
        placeholder={t("placeholder")}
        aria-label={t("search")}
      />
      <select name="status" defaultValue={status} aria-label={t("status")}>
        <option value="">{t("all")}</option>
        <option value="OPEN">{t("values.OPEN")}</option>
        <option value="HIDDEN">{t("values.HIDDEN")}</option>
        <option value="CLOSED">{t("values.CLOSED")}</option>
      </select>
      <button type="submit">{t("filter")}</button>
      {(search || status) && <Link href="/offenses">{t("clear")}</Link>}
    </form>
  );
}
