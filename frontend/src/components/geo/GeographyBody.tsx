"use client";

// What the Geography widget and the Geography page both show for one `GET /geo/countries` answer:
// the map, its legend and the country table. The page keeps the full measurement facts; the
// widget uses the component's compact or expanded density so it can disclose less without
// disagreeing about any state.
//
// The states, each a sentence and never an empty map:
//   NOT_COLLECTED     nothing was measured for this direction and range: no map, no zero
//   INSUFFICIENT_DATA buckets were measured but no address resolved to a country: no map
//   PARTIAL           the map is drawn, and the coverage says how much of the range it covers
//   OK                the map is drawn
// A LOWER_BOUND measurement reads "at least" in the tooltip, the legend and the table; the
// remainders (unresolved, over the cap, unparseable) are listed apart and never added to a share.

import { useTranslations } from "next-intl";
import { useId, useMemo, type ReactNode } from "react";

import { Disclosure } from "@/components/ui/Disclosure";
import type { GeoCountries } from "@/lib/api";
import { formatCount, formatShare, type FormatLocale } from "@/lib/format";

import { GeoMap } from "./GeoMap";
import { GeoProvenance } from "./GeoProvenance";
import { countryName, rowsOf, scaleClasses, undrawn, type ScaleClass } from "./geoModel";
import { useWorld } from "./world";

import "@/components/home/tables.css";
import "./geo.css";

/** The countries named in the map's text alternative. */
const ARIA_COUNTRIES = 3;
/** The undrawn countries named in the note; the rest are counted. */
const UNDRAWN_NAMES = 6;

export type GeographyDensity = "full" | "compact" | "expanded";

export function GeographyBody({
  data,
  locale,
  limit,
  density = "full",
}: {
  data: GeoCountries;
  locale: FormatLocale;
  /** How many table rows; null lists every country the answer holds. */
  limit: number | null;
  /** The page is full by default; widgets choose compact or expanded disclosure. */
  density?: GeographyDensity;
}) {
  const t = useTranslations("geo");
  const states = useTranslations("states");
  const drawsMap = data.status === "OK" || data.status === "PARTIAL";
  const world = useWorld(drawsMap);
  const directionName = t(`directionName.${data.direction}`);
  const atLeast = data.measurement === "LOWER_BOUND";
  const showsFacts = density !== "compact";

  const classes = useMemo(() => scaleClasses(data.countries.map((c) => c.events)), [data]);
  const rows = useMemo(() => rowsOf(data, locale), [data, locale]);

  if (data.status === "NOT_COLLECTED") {
    return (
      <div className="geo-hollow">
        <div
          className={
            density === "compact" ? "geo-hollow-body geo-plot-state" : "geo-hollow-body"
          }
        >
          <p className="tw-state">
            <strong>{t("state.notCollectedLead")}</strong>
            {density === "compact" ? <br /> : " "}
            {t("state.notCollected", { direction: directionName })}
          </p>
          {showsFacts ? <Coverage data={data} locale={locale} /> : null}
        </div>
      </div>
    );
  }

  const shown = limit === null ? rows : rows.slice(0, limit);
  const top = rows
    .slice(0, ARIA_COUNTRIES)
    .map((r) => t("map.topItem", { name: r.name, count: formatCount(r.events, locale) }))
    .join(", ");
  const missing =
    world.status === "ok" ? undrawn(data.countries, world.world.codes) : [];

  return (
    <div className={drawsMap ? "geo" : "geo geo-hollow"}>
      <div className="geo-grid">
        {data.status === "PARTIAL" || atLeast ? (
          <ul className="tw-qual geo-qual">
            {data.status === "PARTIAL" ? (
              <li className="warn">
                <span className="kind">{states("partial")}</span>: {t("facts.partial")}
              </li>
            ) : null}
            {atLeast ? (
              <li className="warn">
                <span className="kind">{t("facts.atLeastKind")}</span>: {t("facts.lowerBound")}
              </li>
            ) : null}
          </ul>
        ) : null}
        {drawsMap ? (
          <div className="geo-main">
            <GeoMap
              data={data}
              world={world}
              locale={locale}
              ariaLabel={
                top
                  ? t("map.aria", { direction: directionName, top })
                  : t("map.ariaNone")
              }
            />
            <Legend classes={classes} atLeast={atLeast} locale={locale} />
            {missing.length > 0 ? <Undrawn missing={missing} locale={locale} /> : null}
          </div>
        ) : null}
        {drawsMap ? (
          <div className="geo-side">
            <CountryTable
              rows={shown}
              total={rows.length}
              atLeast={atLeast}
              locale={locale}
              limited={limit !== null && rows.length > limit}
            />
          </div>
        ) : (
          <div className={density === "compact" ? "geo-main geo-plot-state" : "geo-main"}>
            <p className="tw-state">
              <strong>{t("state.insufficientLead")}</strong>
              {density === "compact" ? <br /> : " "}
              {t("state.insufficient", { direction: directionName })}
            </p>
          </div>
        )}
        {showsFacts ? (
          <div className="geo-facts">
            <Facts
              data={data}
              locale={locale}
              atLeast={atLeast}
              explainRemainders={density === "full"}
            />
          </div>
        ) : null}
      </div>
    </div>
  );
}

/** The legend: the classes of the scale with their unit, and the "no data" fill. */
function Legend({
  classes,
  atLeast,
  locale,
}: {
  classes: readonly ScaleClass[];
  atLeast: boolean;
  locale: FormatLocale;
}) {
  const t = useTranslations("geo.legend");
  const titleId = useId();
  return (
    <div className="geo-legend-row">
      <p className="geo-legend-title" id={titleId}>
        {atLeast ? t("titleAtLeast") : t("title")}
      </p>
      <ul className="geo-legend" aria-labelledby={titleId}>
        {classes.map((c) => (
          <li key={c.from}>
            <span className={`geo-swatch geo-ramp-${c.ramp}`} aria-hidden="true" />
            {c.to === null
              ? t("from", { from: formatCount(c.from, locale) })
              : t("between", {
                  from: formatCount(c.from, locale),
                  to: formatCount(c.to - 1, locale),
                })}
          </li>
        ))}
        <li>
          <span className="geo-swatch geo-ramp-none" aria-hidden="true" />
          {t("noData")}
        </li>
      </ul>
    </div>
  );
}

/** Countries in the answer that the outlines hold no polygon for: counted, named, still in the table. */
function Undrawn({
  missing,
  locale,
}: {
  missing: ReadonlyArray<{ country_code: string; events: number }>;
  locale: FormatLocale;
}) {
  const t = useTranslations("geo.table");
  const names = missing.slice(0, UNDRAWN_NAMES).map((c) => countryName(c.country_code, locale));
  const more = missing.length - names.length;
  const events = missing.reduce((sum, c) => sum + c.events, 0);
  return (
    <p className="tw-note">
      {t("undrawn", {
        count: missing.length,
        names: more > 0 ? `${names.join(", ")} +${formatCount(more, locale)}` : names.join(", "),
        events: formatCount(events, locale),
      })}
    </p>
  );
}

function CountryTable({
  rows,
  total,
  atLeast,
  locale,
  limited,
}: {
  rows: ReturnType<typeof rowsOf>;
  total: number;
  atLeast: boolean;
  locale: FormatLocale;
  limited: boolean;
}) {
  const t = useTranslations("geo.table");
  return (
    <>
      {/* One scroll container, focusable and named, so a list longer than the map can be read with
          the keyboard: it is `.table-scroll` that scrolls in both directions. */}
      <div className="table-scroll geo-table-wrap" role="region" aria-label={t("label")} tabIndex={0}>
        <table className="tw-table geo-table" aria-label={t("label")}>
          <colgroup>
            <col />
            <col className="tw-col-eps" />
            <col className="tw-col-ratio" />
          </colgroup>
          <thead>
            <tr>
              <th scope="col">{t("country")}</th>
              <th scope="col" className="num geo-th-num">
                {t("events")}
              </th>
              <th scope="col" className="num geo-th-num">
                {t("share")}
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.code}>
                <td className="tw-name">
                  {r.name}
                  {r.name !== r.code ? <span className="geo-code">{r.code}</span> : null}
                </td>
                <td className="num">
                  {atLeast ? <span className="sr-only">{t("atLeast")} </span> : null}
                  {atLeast ? "≥ " : ""}
                  {formatCount(r.events, locale)}
                </td>
                <td className="num">{formatShare(r.share, locale)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="tw-note">
        {limited
          ? t("top", { shown: formatCount(rows.length, locale), total: formatCount(total, locale) })
          : t("all", { total })}
      </p>
    </>
  );
}

/** One term and its figure. */
function Fact({ term, children }: { term: string; children: ReactNode }) {
  return (
    <div>
      <dt>{term}</dt>
      <dd>{children}</dd>
    </div>
  );
}

function Coverage({ data, locale }: { data: GeoCountries; locale: FormatLocale }) {
  const t = useTranslations("geo.facts");
  return (
    <div className="geo-facts">
      <dl>
        <Fact term={t("coverage")}>
          {t("coverageValue", {
            measured: formatCount(data.coverage.measured_buckets, locale),
            expected: formatCount(data.coverage.expected_buckets, locale),
          })}
        </Fact>
      </dl>
    </div>
  );
}

/** Coverage, the remainders, where the country comes from, and what the remainders mean. */
function Facts({
  data,
  locale,
  atLeast,
  explainRemainders,
}: {
  data: GeoCountries;
  locale: FormatLocale;
  atLeast: boolean;
  explainRemainders: boolean;
}) {
  const t = useTranslations("geo.facts");
  const figure = (n: number) =>
    `${atLeast ? "≥ " : ""}${t("eventsUnit", { count: formatCount(n, locale) })}`;
  const remainders = data.remainders;
  const note =
    locale === "en" && data.unresolved_note ? data.unresolved_note : t("unresolvedNote");
  return (
    <>
      <dl>
        <Fact term={t("coverage")}>
          {t("coverageValue", {
            measured: formatCount(data.coverage.measured_buckets, locale),
            expected: formatCount(data.coverage.expected_buckets, locale),
          })}
        </Fact>
        {remainders ? (
          <>
            <Fact term={t("unresolved")}>{figure(remainders.UNRESOLVED)}</Fact>
            <Fact term={t("overCap")}>{figure(remainders.OVER_CAP)}</Fact>
            <Fact term={t("unparseable")}>{figure(remainders.UNPARSEABLE)}</Fact>
          </>
        ) : null}
      </dl>
      {remainders && explainRemainders ? (
        <Disclosure summary={t("explainSummary")}>
          <p>{note}</p>
          <p>{t("overCapNote")}</p>
          <p>{t("unparseableNote")}</p>
        </Disclosure>
      ) : null}
      <GeoProvenance data={data} />
    </>
  );
}
