// Filters for the source-behavior inventory.
//
// A plain GET form, matching the anomaly filters: no client JavaScript, and the
// resulting URL is the shareable description of the view. Submitting drops
// `offset` simply by not carrying it, which resets paging — an offset into the
// previous result set means nothing in the new one.
//
// Every control has a real <label> associated by `htmlFor`. They are visually
// hidden rather than absent, so the form stays compact without the controls
// becoming unlabelled to a screen reader.

import Link from "next/link";
import { useTranslations } from "next-intl";

import { useEnumLabels } from "@/i18n/enumLabels";
import { isFiltered, type SourceQuery } from "@/lib/sourceInventory";

const STATES = [
  "OPEN",
  "CANDIDATE",
  "RECOVERING",
  "NORMAL",
  "RESOLVED",
  "INSUFFICIENT_DATA",
  "SUPPRESSED",
];

export function SourceFilters({ query }: { query: SourceQuery }) {
  const t = useTranslations("behavior.filters");
  const { label } = useEnumLabels();
  return (
    <form className="filters source-filters" method="get" action="/behavior/sources">
      <label className="sr-only" htmlFor="source-q">
        {t("searchLabel")}
      </label>
      <input
        id="source-q"
        type="search"
        name="q"
        defaultValue={query.q}
        placeholder={t("searchPlaceholder")}
      />

      <label className="sr-only" htmlFor="source-state">
        {t("stateLabel")}
      </label>
      <select id="source-state" name="state" defaultValue={query.state}>
        <option value="">{t("allStates")}</option>
        {STATES.map((s) => (
          <option key={s} value={s}>
            {label("anomalyState", s)}
          </option>
        ))}
      </select>

      <label className="sr-only" htmlFor="source-anomaly">
        {t("anomalyLabel")}
      </label>
      <select id="source-anomaly" name="anomaly" defaultValue={query.anomaly}>
        <option value="">{t("anyAnomaly")}</option>
        <option value="active">{t("hasActive")}</option>
        <option value="none">{t("noActive")}</option>
      </select>

      <label className="sr-only" htmlFor="source-data">
        {t("baselineLabel")}
      </label>
      <select id="source-data" name="data" defaultValue={query.data}>
        <option value="">{t("anyBaseline")}</option>
        <option value="insufficient">{t("insufficientOnly")}</option>
        <option value="adequate">{t("baselinedOnly")}</option>
      </select>

      {/* Carried so that filtering does not silently discard the chosen
          column order. */}
      <input type="hidden" name="sort" value={query.sort} />
      <input type="hidden" name="dir" value={query.dir} />

      <button type="submit">{t("submit")}</button>
      {isFiltered(query) && <Link href="/behavior/sources">{t("clear")}</Link>}
    </form>
  );
}
