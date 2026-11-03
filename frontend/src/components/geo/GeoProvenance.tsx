"use client";

// How the plotted buckets were collected (GEO-6), under the map. The line is built from the
// buckets of the range (`collected_with`), never from the current setting: a console switched
// from the lookup to a property still holds buckets the lookup stored, and the page must not
// say otherwise. Buckets stored before 0.1.11 did not record it; they are counted, never guessed.
//
//   one way             "Collected from the QRadar property “X”" / "Collected with QRadar's GEO::LOOKUP"
//   several ways        "Collected two ways in this range: …", each with its bucket count
//   unknown buckets     "N buckets were collected before 0.1.11 and do not record how."
//   setting differs     one more line says the current setting (and, with no property named, the
//                       `.env` key that names it)
//
// An answer with no `collected_with` at all (an older backend) keeps the old line, which reads the
// setting; there is nothing better to say.

import { useTranslations } from "next-intl";

import type { GeoCollectedWay, GeoCountries, GeoDirection } from "@/lib/api";

/** The `.env` key that names the property of each direction. */
const PROPERTY_KEY: Record<GeoDirection, string> = {
  SOURCE: "GEO_SOURCE_COUNTRY_PROPERTY",
  DESTINATION: "GEO_DESTINATION_COUNTRY_PROPERTY",
};

/** The setting is what the range shows: the same source and, for a property, the same name. */
function sameWay(data: GeoCountries, way: GeoCollectedWay): boolean {
  if (data.country_source !== way.country_source) return false;
  return way.country_source === "LOOKUP" || data.country_property === way.country_property;
}

export function GeoProvenance({ data }: { data: GeoCountries }) {
  const t = useTranslations("geo.facts");
  const names = useTranslations("geo.directionName");
  const collected = data.collected_with;

  if (collected === undefined) {
    // An older backend: the setting is all there is.
    const line =
      data.country_source === "PROPERTY"
        ? data.country_property
          ? t("fromProperty", { property: data.country_property })
          : t("fromPropertyUnnamed")
        : data.country_source === "LOOKUP"
          ? t("fromLookup")
          : null;
    return line ? <p className="tw-note">{line}</p> : null;
  }
  // Nothing was measured: no bucket, so nothing to say about how one was collected.
  if (collected === null) return null;

  const { ways, unknown_buckets: unknown } = collected;
  const way = (item: GeoCollectedWay) =>
    item.country_source === "LOOKUP"
      ? t("wayLookup")
      : item.country_property
        ? t("wayProperty", { property: item.country_property })
        : t("wayPropertyUnnamed");
  const counted = (item: GeoCollectedWay) =>
    t("wayBuckets", { way: way(item), count: item.buckets });

  let primary: string | null = null;
  if (ways.length === 1 && unknown === 0) {
    const only = ways[0];
    primary =
      only.country_source === "LOOKUP"
        ? t("collectedLookup")
        : only.country_property
          ? t("collectedProperty", { property: only.country_property })
          : t("collectedPropertyUnnamed");
  } else if (ways.length === 1) {
    primary = t("collectedFrom", { ways: counted(ways[0]) });
  } else if (ways.length > 1) {
    primary = t("collectedMany", { count: ways.length, ways: ways.map(counted).join("; ") });
  }

  const agrees = ways.length === 1 && unknown === 0 && sameWay(data, ways[0]);
  let setting: string | null = null;
  if (!agrees && data.country_source === "LOOKUP") setting = t("settingLookup");
  else if (!agrees && data.country_source === "PROPERTY") {
    setting = data.country_property
      ? t("settingProperty", { property: data.country_property })
      : t("settingUnnamed", {
          direction: names(data.direction),
          key: PROPERTY_KEY[data.direction],
        });
  }

  if (primary === null && unknown === 0 && setting === null) return null;
  return (
    <>
      {primary ? <p className="tw-note">{primary}</p> : null}
      {unknown > 0 ? (
        <p className="tw-note warn">
          {t("unknownBuckets", { count: unknown })}
        </p>
      ) : null}
      {setting ? <p className="tw-note">{setting}</p> : null}
    </>
  );
}
