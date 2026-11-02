// Where events come from.
//
// The question: from which countries does the traffic QRadar sees arrive, and how far can the
// map be trusted? The first screen is the map with its scale and the country table beside it;
// under them, the coverage of the range and what could not be attributed to a country. The
// window and the address (source or destination) are in the URL.

import type { Metadata } from "next";
import { getLocale, getTranslations } from "next-intl/server";

import { GeographyBody } from "@/components/geo/GeographyBody";
import { GeoFreshness } from "@/components/geo/GeoFreshness";
import { GeoSwitches } from "@/components/geo/GeoSwitches";
import { parseDirection } from "@/components/geo/geoModel";
import { PageHeader } from "@/components/ui/PageHeader";
import { UtcTime } from "@/components/ui/UtcTime";
import { withRouteMessages } from "@/i18n/RouteMessages";
import { asTextTranslator } from "@/i18n/textTranslator";
import { geoApi, type GeoCountries } from "@/lib/api";
import { parseWindow } from "@/lib/fleet";
import type { FormatLocale } from "@/lib/format";
import { guideFor } from "@/lib/guideRoutes";
import { loadErrorMessage } from "@/lib/loadError";

import "@/components/geo/geo.css";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("geo.page");
  return { title: t("meta") };
}

async function GeographyPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const params = await searchParams;
  const window = parseWindow(params.window);
  const direction = parseDirection(params.direction);
  const t = await getTranslations("geo.page");
  const units = await getTranslations("units.range");
  const say = asTextTranslator(await getTranslations());
  const locale = (await getLocale()) as FormatLocale;

  let data: GeoCountries | null = null;
  let error: string | null = null;
  try {
    data = await geoApi.countries(window, direction);
  } catch (err) {
    error = loadErrorMessage(err, t("loadNoun"), "read:geo", say);
  }

  return (
    <>
      <PageHeader
        guide={guideFor("/geography")}
        title={t("title")}
        description={t("description")}
        actions={<GeoSwitches window={window} direction={direction} />}
      />
      {error || !data ? (
        <div className="notice" role="alert">
          <p>
            <strong>{t("failedHeading")}</strong>
          </p>
          <p>{error}</p>
        </div>
      ) : (
        <section className="tw" aria-labelledby="geo-map-title">
          <header className="tw-head">
            <h2 className="tw-title" id="geo-map-title">
              {t("mapHeading")}
            </h2>
          </header>
          <p className="tw-note">
            {t.rich("windowLine", {
              window: units(window),
              start: () => <UtcTime value={data.since} />,
              end: () => <UtcTime value={data.until} />,
            })}
          </p>
          <GeoFreshness asOf={data.latest_bucket_end} />
          <GeographyBody data={data} locale={locale} limit={null} />
        </section>
      )}
    </>
  );
}

export default withRouteMessages("/geography", GeographyPage);
