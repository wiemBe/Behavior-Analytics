// One statement of how complete the evidence is.
//
// The previous page said this three times — a KPI card, a status panel and a
// pill per dimension — with the full explanatory paragraph repeated above
// every table. Said once, calmly, at the point where it changes how the
// evidence below should be read.
//
// Four of the six statuses produce a page with no contributors, which looks
// identical to "we looked and nothing stood out". Only this banner
// distinguishes them, so it renders for every status except COMPLETE, where
// there is nothing to qualify.

import { useTranslations } from "next-intl";

import type { EvidenceStatus } from "@/lib/api";
import { evidenceTone } from "@/lib/behavior";
import { evidenceCode } from "@/lib/behaviorI18n";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";

/** The statuses that carry a sentence; COMPLETE has nothing to qualify. */
const WORDED = new Set<string>(["PARTIAL", "UNAVAILABLE", "FAILED", "PENDING", "NOT_REQUESTED"]);

export function EvidenceBanner({
  status,
  error,
  truncatedDimensions = [],
}: {
  status: EvidenceStatus;
  /** Sanitized collector error, if the backend recorded one. */
  error?: string | null;
  /** Labels of dimensions capped at the value limit. */
  truncatedDimensions?: string[];
}) {
  const t = useTranslations("anomalies.banner");
  const i18n = useBehaviorI18n();
  const message = WORDED.has(status) ? t(`status.${status}` as "status.PARTIAL") : null;
  const tone = evidenceTone(status);
  const lead = `${evidenceCode(i18n, status)}.`;

  return (
    <>
      {message && (
        <p className={`banner ${tone}`} role={status === "FAILED" ? "alert" : undefined}>
          <span>
            {/* The status word itself, so the banner is not colour-only. */}
            <strong>{lead}</strong> {message}
          </span>
        </p>
      )}

      {truncatedDimensions.length > 0 && (
        <p className="banner warn">
          <span>
            <strong>{t("truncatedLead")}</strong>{" "}
            {t("truncated", { dimensions: truncatedDimensions.join(", ") })}
          </span>
        </p>
      )}

      {error && (
        // Backend-sanitized: never a provider response body, never headers.
        // React escapes it again on the way into the DOM.
        <p className="banner crit" role="alert">
          <span>
            <strong>{t("errorLead")}</strong> {error}
          </span>
        </p>
      )}
    </>
  );
}
