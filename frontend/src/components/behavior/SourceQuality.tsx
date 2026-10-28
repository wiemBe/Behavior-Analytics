// Baseline quality and collection health, side by side.
//
// Two questions an analyst must be able to answer before trusting anything
// above: is the expectation this source is judged against any good, and is the
// data behind the chart complete? Both are interpretive — a status word and a
// sentence — rather than a dump of the figures they were derived from. The
// figures stay available in the advanced disclosures below.
//
// Neither section presents an absence of collection as source silence, and
// neither presents a zero MAD as an error.

import { useTranslations } from "next-intl";

import {
  seasonalCellLabel,
  type BaselineQuality,
  type CollectionHealth,
} from "@/lib/sourceDetail";
import { formatMetric } from "@/lib/behavior";
import { MAD_EPS } from "@/lib/behaviorI18n";
import { plainCount } from "@/lib/figureText";
import { formatShare } from "@/lib/format";
import { relativeAgeFromSeconds, type AgeWords } from "@/lib/time";
import { qradarStatusLabel } from "@/app/log-sources/qradarStatus";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";
import { UtcTime } from "@/components/ui/UtcTime";

export function SourceQuality({
  baseline,
  collection,
  collectionUnknown = false,
  expectedLow,
  expectedHigh,
  lastBucketAt,
  qradarStatus,
}: {
  baseline: BaselineQuality;
  collection: CollectionHealth;
  /** The metric history could not be read, so collection cannot be judged. */
  collectionUnknown?: boolean;
  expectedLow: number | null;
  expectedHigh: number | null;
  lastBucketAt: string | null;
  /** The log source's QRadar-reported status, when the inventory call worked. */
  qradarStatus: string | null;
}) {
  const t = useTranslations("behavior.quality");
  const age = useTranslations("format") as unknown as AgeWords;
  const common = useTranslations("common");
  const i18n = useBehaviorI18n();
  const metric = (value: number | null | undefined, digits = 2) =>
    formatMetric(value, digits, i18n.locale);
  return (
    <div className="quality-grid">
      <section className="panel">
        <div className="row dimension-panel-head">
          <h3>{t("baselineTitle")}</h3>
          <span className={`pill ${baseline.tone}`}>{baseline.label}</span>
        </div>
        <p className="subtitle">{baseline.explanation}</p>

        <dl className="dimension-meta">
          {baseline.cell && (
            <>
              <div>
                <dt>{t("samples")}</dt>
                <dd className="num">{baseline.cell.sample_count}</dd>
              </div>
              <div>
                <dt>{t("seasonalCell")}</dt>
                <dd className="num">
                  {seasonalCellLabel(baseline.cell.weekday, baseline.cell.hour, i18n)}
                </dd>
              </div>
              <div>
                <dt>{t("cellCompleteness")}</dt>
                <dd className="num">
                  {formatShare(baseline.cell.completeness, i18n.locale)}
                </dd>
              </div>
              <div>
                <dt>{MAD_EPS}</dt>
                <dd className="num">{metric(baseline.cell.mad, 3)}</dd>
              </div>
            </>
          )}
          <div>
            <dt>{t("expectedRange")}</dt>
            <dd className="num">
              {/* No band exists without a baseline. An em dash says so; a
                  "0.00 – 0.00" would be an expectation we never formed. */}
              {expectedLow == null || expectedHigh == null ? (
                <span className="muted">—</span>
              ) : (
                `${metric(expectedLow)} – ${metric(expectedHigh)}`
              )}
            </dd>
          </div>
        </dl>
      </section>

      <section className="panel">
        <div className="row dimension-panel-head">
          <h3>{t("collectionTitle")}</h3>
          {collectionUnknown ? (
            <span className="pill">{common("unknown")}</span>
          ) : (
            <span className={`pill ${collection.tone}`}>{collection.label}</span>
          )}
        </div>
        <p className="subtitle">
          {collectionUnknown ? t("collectionUnknown") : collection.explanation}
        </p>

        <dl className="dimension-meta">
          <div>
            <dt>{t("latestInterval")}</dt>
            <dd className="num">
              {lastBucketAt ? (
                <UtcTime value={lastBucketAt} />
              ) : (
                <span className="muted">—</span>
              )}
            </dd>
          </div>
          {collectionUnknown ? (
            <>
              <div>
                <dt>{t("lag")}</dt>
                <dd className="num">
                  <span className="muted">—</span>
                </dd>
              </div>
              <div>
                <dt>{t("intervals")}</dt>
                <dd className="num">
                  <span className="muted">—</span>
                </dd>
              </div>
            </>
          ) : (
            <>
              <div>
                <dt>{t("lag")}</dt>
                <dd className="num">{relativeAgeFromSeconds(collection.lagSeconds, age)}</dd>
              </div>
              <div>
                <dt>{t("intervals")}</dt>
                {/* The window's edges can both hold a bucket, so the count of
                    stored buckets may exceed the nominal count; the
                    denominator never reads lower than the numerator. */}
                <dd className="num">
                  {t("intervalsObserved", {
                    observed: plainCount(collection.total - collection.incomplete, i18n.locale),
                    total: plainCount(Math.max(collection.expected, collection.total), i18n.locale),
                  })}
                </dd>
              </div>
            </>
          )}
          <div>
            <dt>{t("qradarStatus")}</dt>
            {/* Reported by QRadar about the log source. Not a statement about
                whether our collector ran, which the API does not expose. */}
            <dd>
              {qradarStatus != null ? (
                qradarStatusLabel(qradarStatus, i18n.t)
              ) : (
                <span className="muted">{t("unavailable")}</span>
              )}
            </dd>
          </div>
        </dl>
      </section>
    </div>
  );
}
