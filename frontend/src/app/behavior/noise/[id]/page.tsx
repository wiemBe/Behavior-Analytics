// Tune a noisy source: what it raised, and what would change if its detection values changed.
//
// First screen, in order: who the source is, what each detector raised in 7 and 28 days, then the
// replay form. The replay is a client block (the form and its preview); everything above it is
// rendered here from one read of the detail route. A read that fails says what is unknown and never
// draws the counts as zero.

import type { Metadata } from "next";
import Link from "next/link";
import { getTranslations } from "next-intl/server";

import { NoiseModelView } from "@/components/noise/NoiseModelView";
import { NoiseDetectorCounts } from "@/components/noise/NoiseDetectorCounts";
import { stripJudgedPrefix } from "@/components/noise/NoiseTable";
import { NoiseTuning } from "@/components/noise/NoiseTuning";
import { PageHeader } from "@/components/ui/PageHeader";
import { UtcTime } from "@/components/ui/UtcTime";
import { ApiError, api, rateLimitedText, type NoiseSourceDetail } from "@/lib/api";
import { guideFor } from "@/lib/guideRoutes";
import { withRouteMessages } from "@/i18n/RouteMessages";
import { enumLabel, type EnumTranslator } from "@/i18n/enumLabels";
import { asTextTranslator } from "@/i18n/textTranslator";
import { formatCount } from "@/lib/format";
import { isJudged, isNotMonitored, noiseHref, parseNoiseQuery, sourceAlertsHref, sourceHref } from "@/lib/noise";

import "@/components/noise/noise.css";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("noise.detail");
  return { title: t("meta") };
}

const asEnumTranslator = (t: unknown) => t as EnumTranslator;

async function NoiseDetailPageView({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { id } = await params;
  const query = parseNoiseQuery(await searchParams);
  const t = await getTranslations("noise.detail");
  const tn = await getTranslations("noise.table");
  const tt = await getTranslations("noise.tuning");
  const te = asEnumTranslator(await getTranslations("enums"));
  const say = asTextTranslator(await getTranslations());

  let detail: NoiseSourceDetail | null = null;
  let error: string | null = null;
  try {
    detail = await api.noiseSource(id);
  } catch (err) {
    error =
      err instanceof ApiError && err.status === 429
        ? rateLimitedText(err, say)
        : err instanceof ApiError && err.status === 403
          ? t("errors.forbidden")
          : err instanceof ApiError && err.status === 401
            ? t("errors.expired")
            : err instanceof ApiError && err.status === 404
              ? t("errors.notFound")
              : t("errors.failed");
  }

  const back = (
    <p className="noise-back">
      <Link href={noiseHref(query)}>{t("back")}</Link>
    </p>
  );

  if (!detail) {
    return (
      <>
        {back}
        <PageHeader guide={guideFor("/behavior/noise/[id]")} title={t("meta")} />
        <div className="notice" role="alert">
          <p>
            <strong>{t("errors.title")}</strong>
          </p>
          <p>{error}</p>
        </div>
      </>
    );
  }

  const off = isNotMonitored(detail);
  const judged = isJudged(detail);
  const critLabel = enumLabel(te, "severity", detail.criticality);
  const classLabel = detail.source_class ? enumLabel(te, "sourceClass", detail.source_class) : t("unclassified");

  return (
    <>
      {back}
      <PageHeader
        guide={guideFor("/behavior/noise/[id]")}
        title={detail.name}
        description={t("identity", { instance: detail.instance_name ?? "—", class: classLabel })}
        meta={
          <>
            <span className="pill pill-quiet">{t("criticality", { value: critLabel })}</span>
            {off ? (
              <span className="pill pill-quiet warn">{t("notMonitored")}</span>
            ) : judged ? null : (
              <span className="pill pill-quiet warn" title={detail.reason?.text}>
                {t("notJudged")}
              </span>
            )}
            {detail.current.maintenance_mode && (
              <span className="pill pill-quiet warn">
                {t("maintenance")}
                {detail.current.maintenance_until && (
                  <>
                    {" "}
                    {t("maintenanceUntil")} <UtcTime value={detail.current.maintenance_until} />
                  </>
                )}
              </span>
            )}
          </>
        }
        actions={
          <>
            <Link href={sourceHref(detail)}>{t("sourcePage")}</Link>
            {!off && <Link href={sourceAlertsHref(detail)}>{t("alertsLink")}</Link>}
          </>
        }
      />

      <section aria-labelledby="noise-counts-title">
        <h2 id="noise-counts-title" className="section-title">
          {t("countsTitle")}
        </h2>
        {off ? (
          <div className="notice" role="status">
            <p>{t("notMonitoredNotice")}</p>
          </div>
        ) : (
          <div className="stack-6">
            {!judged && (
              <div className="banner warn" role="status">
                <p>
                  <strong>{tn("notJudged")}.</strong>{" "}
                  {detail.reason
                    ? stripJudgedPrefix(detail.reason.text, tn("notJudged"))
                    : tn("notJudgedHint")}
                </p>
              </div>
            )}
            {detail.coverage.state === "PARTIAL" && (
              <div className="banner warn" role="status">
                <p>
                  <strong>{t("partialTitle")}</strong>{" "}
                  {detail.coverage.reason?.text ??
                    t("partialText", {
                      covered: formatCount(detail.coverage.covered_days),
                      window: formatCount(detail.coverage.window_days),
                    })}
                </p>
              </div>
            )}
            <NoiseDetectorCounts detail={detail} />
          </div>
        )}
      </section>

      <section aria-labelledby="noise-tuning-title">
        <h2 id="noise-tuning-title" className="section-title">
          {tt("title")}
        </h2>
        <NoiseTuning logSourceId={detail.log_source_id} initial={detail.current} />
      </section>

      {!off && <NoiseModelView logSourceId={detail.log_source_id} />}
    </>
  );
}

export default withRouteMessages("/behavior/noise/[id]", NoiseDetailPageView);
