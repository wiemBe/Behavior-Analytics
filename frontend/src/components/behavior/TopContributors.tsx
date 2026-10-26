import Link from "next/link";
import { useTranslations } from "next-intl";
// "What changed?" — the three strongest pieces of evidence, above the fold.
//
// This replaces reading ten contributor tables to find out that the spike was
// denied SMB traffic from one host. The selection is dimension-diverse by
// construction (see lib/contributors.ts), so the three cards describe the
// change from three angles rather than restating one of them three times.
//
// Nothing here asserts cause. A contribution share says how much of the change
// a value accounts for, which is a measurement; "this caused it" is not.

import type { ExplanationPackage } from "@/lib/api";
import {
  contributorDisplayValue,
  selectHeadlineContributors,
} from "@/lib/contributors";
import { formatCount, formatDelta, formatShare } from "@/lib/behavior";
import { ARROW } from "@/lib/behaviorI18n";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";

export function TopContributors({
  packaged,
  anomalyType,
  evidenceStatus,
  onViewAllHref = "#evidence",
}: {
  packaged: ExplanationPackage | null;
  anomalyType: string;
  evidenceStatus: string;
  onViewAllHref?: string;
}) {
  const t = useTranslations("anomalies.top");
  const i18n = useBehaviorI18n();
  const { locale } = i18n;
  const picks = selectHeadlineContributors(packaged, 3, i18n);

  if (picks.length === 0) {
    return (
      <div className="notice">
        {anomalyType === "NO_EVENTS"
          ? t("noEvents")
          : evidenceStatus === "PENDING"
            ? t("pending")
            : evidenceStatus === "FAILED"
              ? t("failed")
              : t("unavailable")}
      </div>
    );
  }

  // A reduction is described as a reduction. The backend reports a negative
  // contribution share on a drop, and a card headed "contribution" showing a
  // negative delta reads as an increase that went wrong.
  const isDrop = anomalyType === "VOLUME_DROP";

  return (
    <>
      <ul className="contributor-cards">
        {picks.map((pick) => {
          const c = pick.contributor;
          return (
            <li key={`${pick.dimension}:${c.value}`} className="card contributor-card">
              <div className="k">{pick.dimensionLabel}</div>
              <p className="contributor-value">{contributorDisplayValue(c)}</p>
              <p className="contributor-change num">
                {formatCount(c.baseline_count, locale)} {ARROW} {formatCount(c.anomaly_count, locale)}
              </p>
              <p className="contributor-stats">
                {/* Magnitude: the share is signed, and "-95.9% contribution"
                    is not how an analyst describes a reduction. */}
                {t.rich(isDrop ? "statsReduction" : "statsIncrease", {
                  delta: formatDelta(c.absolute_delta, locale),
                  share: formatShare(
                    c.contribution_share == null ? null : Math.abs(c.contribution_share),
                    locale,
                  ),
                  deltaTag: (chunks) => (
                    <span className={`num ${isDrop ? "crit-text" : ""}`}>{chunks}</span>
                  ),
                  shareTag: (chunks) => <span className="num">{chunks}</span>,
                  muted: (chunks) => <span className="muted">{chunks}</span>,
                })}
              </p>
              {pick.truncated && (
                <p className="metric-note">{t("capped")}</p>
              )}
            </li>
          );
        })}
      </ul>

      <p className="subtitle">
        <Link href={onViewAllHref}>{t("viewAll")}</Link>
      </p>
    </>
  );
}
