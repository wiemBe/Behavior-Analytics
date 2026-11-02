"use client";

// The Geography widget (GEO-4, decision D-10): where events come from, by country. One read,
// `GET /geo/countries`, for the range the widget follows and the direction the analyst chose
// (the source or the destination address). The body is `GeographyBody`, the same component the
// Geography page renders, so the two cannot disagree about a state.
//
// The response says how many buckets were measured but not when the newest one ended, so the
// frame has no data time to judge staleness from and never calls this widget stale; a refresh
// that fails keeps the previous answer on screen with a note, like the other widgets.

import { useLocale, useTranslations } from "next-intl";

import { WidgetFrame } from "@/components/home/WidgetFrame";
import { GeographyBody } from "@/components/geo/GeographyBody";
import { useApiResource } from "@/hooks/useApiResource";
import { homeReads, type GeoDirection } from "@/lib/api";
import { DIRECTIONS } from "@/components/geo/geoModel";
import type { FormatLocale } from "@/lib/format";
import { useWidgetCopy } from "@/widgets/copy";
import { useEffectiveRange, useExpanded, type RangedProps } from "@/widgets/runtime";

import "./catalog.css";

/** The rows the widget lists; the page and the expanded widget list every country. */
export const GEO_WIDGET_ROWS = 5;

export interface GeographyConfig {
  direction: GeoDirection;
}

/** A stored direction this version knows, else null: the widget then reads SOURCE and says so. */
export function knownDirection(value: unknown): GeoDirection | null {
  return DIRECTIONS.find((d) => d === value) ?? null;
}

export function GeographyMap({
  id = "geography",
  config,
  range,
}: { id?: string; config?: Partial<GeographyConfig> } & RangedProps) {
  const t = useTranslations("geo");
  const locale = useLocale() as FormatLocale;
  const copy = useWidgetCopy();
  const expanded = useExpanded();
  const window = useEffectiveRange(range);
  const known = knownDirection(config?.direction);
  const direction: GeoDirection = known ?? "SOURCE";
  const read = homeReads.geoCountries(window, direction);
  const state = useApiResource(read.key, read.fetch);

  return (
    <WidgetFrame
      id={`geography-${id}`}
      title={t("widget.title")}
      windowText={t("widget.windowText", {
        direction: t(`directionName.${direction}`),
        range: copy.range(window),
      })}
      link={{
        href: `/geography?window=${window}&direction=${direction}`,
        label: t("widget.link"),
        ariaLabel: t("widget.linkAria"),
      }}
      state={state}
      dataTo={{ at: null, family: "GEO_COLLECTION" }}
      what={t("widget.what")}
      permission="read:geo"
    >
      {({ data }) => (
        <>
          {config?.direction !== undefined && known === null ? (
            <p className="tw-note warn">{t("widget.unsupportedDirection")}</p>
          ) : null}
          <GeographyBody data={data} locale={locale} limit={expanded ? null : GEO_WIDGET_ROWS} />
        </>
      )}
    </WidgetFrame>
  );
}
