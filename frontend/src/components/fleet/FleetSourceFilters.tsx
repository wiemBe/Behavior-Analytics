// Filters for the fleet source list: a plain GET form, like the other lists.
// The URL is the shareable description of the view, and submitting drops
// `offset` by not carrying it. Every control has a real label, visually hidden
// so the row stays compact.

import { useTranslations } from "next-intl";
import Link from "next/link";

import {
  ALL_CLASSES,
  BASIS_OPTIONS,
  basisLabel,
  classLabel,
  isFleetFiltered,
  reviewLabel,
  type FleetSourceQuery,
} from "@/lib/fleet";
import { useFleetText } from "@/lib/fleetText";

export function FleetSourceFilters({ query }: { query: FleetSourceQuery }) {
  const t = useTranslations("fleet.filters");
  const text = useFleetText();
  return (
    <form className="filters" method="get" action="/fleet/sources">
      <label className="sr-only" htmlFor="fleet-class">
        {t("class")}
      </label>
      <select id="fleet-class" name="class" defaultValue={query.class}>
        <option value="">{t("anyClass")}</option>
        {ALL_CLASSES.map((c) => (
          <option key={c} value={c}>
            {classLabel(c, text)}
          </option>
        ))}
      </select>

      <label className="sr-only" htmlFor="fleet-review">
        {t("review")}
      </label>
      <select id="fleet-review" name="review_status" defaultValue={query.review_status}>
        <option value="">{t("anyReview")}</option>
        <option value="NEW">{reviewLabel("NEW", text)}</option>
        <option value="REVIEWED">{reviewLabel("REVIEWED", text)}</option>
      </select>

      <label className="sr-only" htmlFor="fleet-conflict">
        {t("conflicts")}
      </label>
      <select id="fleet-conflict" name="has_conflict" defaultValue={query.has_conflict}>
        <option value="">{t("conflictOrNot")}</option>
        <option value="true">{t("conflictOnly")}</option>
        <option value="false">{t("noConflict")}</option>
      </select>

      <label className="sr-only" htmlFor="fleet-basis">
        {t("basis")}
      </label>
      <select id="fleet-basis" name="basis" defaultValue={query.basis}>
        <option value="">{t("anyBasis")}</option>
        {BASIS_OPTIONS.map((b) => (
          <option key={b} value={b}>
            {basisLabel(b, text)}
          </option>
        ))}
      </select>

      <button type="submit">{t("filter")}</button>
      {isFleetFiltered(query) && <Link href="/fleet/sources">{t("clear")}</Link>}
    </form>
  );
}
