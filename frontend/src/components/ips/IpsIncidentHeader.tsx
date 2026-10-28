// The IPS incident's identity: what kind of change, about what, on which IPS source.
//
// The subject (the entity, or "Source-level") is the page's <h1>; the detector is
// the largest text, as on a Phase A incident. The IPS source links to its
// administration page only when the caller may read it: a link the caller cannot
// follow would lead to a permission error, so without the grant it is plain text.
// Times carry their timezone.

import Link from "next/link";
import { useTranslations } from "next-intl";

import { ARROW, wordOrValue } from "@/lib/behaviorI18n";
import type { IpsAnomalyDetail } from "@/lib/api";
import { sevTone } from "@/lib/api";
import { stateTone } from "@/lib/behavior";
import { UtcTime } from "@/components/ui/UtcTime";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";

import "./ips.css";
import { dimensionNoun, ipsSubjectText, ipsTypeLabel } from "./ipsLabels";
import { GuideLink } from "@/components/guide/GuideLink";
import { guideFor } from "@/lib/guideRoutes";

function Interval({ anomaly }: { anomaly: IpsAnomalyDetail }) {
  const t = useTranslations("ips.header");
  if (!anomaly.anomaly_start) return <>{t("intervalNotRecorded")}</>;
  return (
    <>
      <UtcTime value={anomaly.anomaly_start} />
      {" "}
      {ARROW}
      {" "}
      {anomaly.anomaly_end ? (
        <UtcTime value={anomaly.anomaly_end} />
      ) : (
        // Still running: an end would report a closed incident that is open.
        t("stillRunning")
      )}
    </>
  );
}

function When({ label, iso }: { label: string; iso: string | null }) {
  const t = useTranslations("ips.header");
  return (
    <div>
      <dt>{label}</dt>
      <dd>
        {iso ? (
          <UtcTime value={iso} />
        ) : (
          <span className="muted">
            —<span className="sr-only"> {t("notRecorded")}</span>
          </span>
        )}
      </dd>
    </div>
  );
}

export function IpsIncidentHeader({
  anomaly,
  canOpenSource,
}: {
  anomaly: IpsAnomalyDetail;
  /** Whether the caller holds admin:ips, which the IPS source page requires. */
  canOpenSource: boolean;
}) {
  const t = useTranslations("ips.header");
  const subjectT = useTranslations("ips.subject");
  const i18n = useBehaviorI18n();
  const sourceName = anomaly.log_source_name ?? anomaly.log_source_id;
  const sourceHref = `/admin/ips-sources/${anomaly.log_source_id}`;
  const entity = anomaly.subject_kind === "IPS_ENTITY";
  return (
    <header className="page-header incident-header">
      <div>
        <p className="incident-back">
          <Link href="/anomalies">{t("allAnomalies")}</Link>
        </p>

        <div className="row incident-identity">
          <span className="incident-detector">{ipsTypeLabel(anomaly.anomaly_type, i18n)}</span>
          <span className={`pill pill-strong ${stateTone(anomaly.state)}`}>
            {wordOrValue(i18n, "enums.anomalyState", anomaly.state)}
          </span>
          <span className={`pill pill-quiet ${sevTone(anomaly.severity)}`}>{wordOrValue(i18n, "enums.severityPill", anomaly.severity)}</span>
          {anomaly.suppressed && <span className="pill pill-quiet warn">{t("suppressed")}</span>}
          <span className="incident-interval">
            <Interval anomaly={anomaly} />
          </span>
        </div>

        <h1 className="page-title incident-source">{ipsSubjectText(anomaly, i18n)}</h1>
        <p className="ips-subject">
          <span className="muted">
            {entity
              ? anomaly.ips_entity
                ? subjectT("entityDimension", {
                    dimension: dimensionNoun(anomaly.ips_entity.dimension, i18n),
                  })
                : subjectT("entity")
              : subjectT("sourceAll")}
          </span>
          <span className="muted">
            {" · "}
            {t("ipsSource")}{" "}
          </span>
          {canOpenSource ? <Link href={sourceHref}>{sourceName}</Link> : sourceName}
        </p>
        <dl className="dimension-meta ips-times">
          <When label={t("detected")} iso={anomaly.detected_at} />
          <When label={t("opened")} iso={anomaly.opened_at} />
          <When label={t("resolved")} iso={anomaly.resolved_at} />
        </dl>
      </div>
      <div className="page-header-actions">
        <GuideLink section={guideFor("/anomalies/[id]")} />
      </div>
    </header>
  );
}
