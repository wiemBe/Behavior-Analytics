// The incident's identity, in one block.
//
// Everything an analyst needs to know they are looking at the right thing:
// what kind of change, how it is currently classified, when it ran, and which
// source. The identity line is the primary text; the source name is the page's
// <h1> and appears exactly once, at a size that survives a host name of
// unbounded length.

import Link from "next/link";
import { useTranslations } from "next-intl";

import { useEnumLabels } from "@/i18n/enumLabels";
import type { AnomalyDetail } from "@/lib/api";
import { sevTone } from "@/lib/api";
import { ARROW } from "@/lib/behaviorI18n";
import { detectorLabel, stateTone } from "@/lib/behavior";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";
import { UtcTime } from "@/components/ui/UtcTime";


export { detectorLabel };

/**
 * The anomaly's interval, or an explicit statement that it has not ended.
 *
 * Each end is a `time` element held on one line, so the interval can only wrap
 * at the arrow rather than through a timestamp.
 */
function Interval({ anomaly }: { anomaly: AnomalyDetail }) {
  const t = useTranslations("anomalies.incident");
  if (!anomaly.anomaly_start) return <>{t("intervalNotRecorded")}</>;
  return (
    <>
      <UtcTime value={anomaly.anomaly_start} />
      {` ${ARROW} `}
      {anomaly.anomaly_end ? (
        <UtcTime value={anomaly.anomaly_end} />
      ) : (
        // Still running: no end exists, and inventing one would report a
        // closed incident that is still open.
        t("stillRunning")
      )}
    </>
  );
}

export function IncidentHeader({ anomaly }: { anomaly: AnomalyDetail }) {
  const t = useTranslations("anomalies.incident");
  const i18n = useBehaviorI18n();
  const { statePill, severityPill } = useEnumLabels();
  return (
    <header className="page-header incident-header">
      <div>
        <p className="incident-back">
          <Link href="/anomalies">{t("allAnomalies")}</Link>
        </p>

        <div className="row incident-identity">
          <span className="incident-detector">
            {detectorLabel(anomaly.anomaly_type, i18n)}
          </span>
          {/* Lifecycle state is the dominant status: it is the fact that
              decides whether this needs work now. */}
          <span className={`pill pill-strong ${stateTone(anomaly.state)}`}>
            {statePill(anomaly.state)}
          </span>
          {/* Severity is real but secondary, so it is quiet by default. */}
          <span className={`pill pill-quiet ${sevTone(anomaly.severity)}`}>
            {severityPill(anomaly.severity)}
          </span>
          {anomaly.suppressed && (
            <span className="pill pill-quiet warn">{t("suppressed")}</span>
          )}
          <span className="incident-interval">
            <Interval anomaly={anomaly} />
          </span>
        </div>

        <h1 className="page-title incident-source">
          {anomaly.log_source_name ?? t("unknownSource")}
        </h1>
      </div>

      <div className="page-header-actions">
        <Link
          href={`/behavior/sources/${anomaly.log_source_id}`}
          className="action secondary button-link"
        >
          {t("viewSource")}
        </Link>
      </div>
    </header>
  );
}
