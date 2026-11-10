// The active incidents of one IPS source, as the list's cell and the source page's header line.
//
// `null` is not zero: the server sends null for a source it does not judge, so the cell says
// "Not judged" and never "None". A number above zero links to the Anomalies page filtered to this
// source's IPS incidents that are still active.

import { useTranslations } from "next-intl";
import Link from "next/link";

import { useEnumLabels } from "@/i18n/enumLabels";
import { sevTone, type IpsSourceBehavior } from "@/lib/api";
import { formatCount } from "@/lib/behavior";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";

import { activeAnomaliesHref } from "./ipsSourceHealth";

type Fields = Pick<
  IpsSourceBehavior,
  "log_source_id" | "active_anomaly_count" | "worst_active_severity"
>;

export function ActiveAnomaliesCell({ s }: { s: Fields }) {
  const t = useTranslations("ips.behavior");
  const { severityPill } = useEnumLabels();
  const { locale } = useBehaviorI18n();
  const count = s.active_anomaly_count;
  if (count == null) {
    return (
      <span className="muted">
        {t("notJudged")}
        <span className="sr-only"> {t("notJudgedMeaning")}</span>
      </span>
    );
  }
  if (count === 0) return <span>{t("activeNone")}</span>;
  const severity = s.worst_active_severity;
  return (
    <>
      <Link href={activeAnomaliesHref(s.log_source_id)}>
        {formatCount(count, locale)}
        <span className="sr-only"> {t("activeLinkMeaning")}</span>
      </Link>
      {severity && (
        <span className="cell-sub">
          <span className={`pill pill-quiet ${sevTone(severity)}`}>{severityPill(severity)}</span>
          <span className="sr-only"> {t("worstSeverity")}</span>
        </span>
      )}
    </>
  );
}

/** One short line under the source page's header; nothing unless there is an active incident. */
export function ActiveAnomaliesLine({ s }: { s: Fields }) {
  const t = useTranslations("ips.bsource");
  const { severityPill } = useEnumLabels();
  const { locale } = useBehaviorI18n();
  const count = s.active_anomaly_count;
  if (count == null || count <= 0) return null;
  const severity = s.worst_active_severity;
  return (
    <p className="ips-lead">
      {t.rich("activeLine", {
        count: formatCount(count, locale),
        plural: count,
        link: (chunks) => <Link href={activeAnomaliesHref(s.log_source_id)}>{chunks}</Link>,
        has: severity ? "yes" : "no",
        word: severity ? severityPill(severity) : "",
        sev: (chunks) => (
          <span className={`pill pill-quiet ${sevTone(severity ?? "")}`}>{chunks}</span>
        ),
      })}
    </p>
  );
}
