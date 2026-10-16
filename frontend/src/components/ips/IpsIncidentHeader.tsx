// The IPS incident's identity: what kind of change, about what, on which IPS source.
//
// The subject (the entity, or "Source-level") is the page's <h1>; the detector is
// the largest text, as on a Phase A incident. The IPS source links to its
// administration page only when the caller may read it: a link the caller cannot
// follow would lead to a permission error, so without the grant it is plain text.
// Times carry their timezone.

import { statePillText } from "@/components/waf/wafLabels";
import Link from "next/link";

import type { IpsAnomalyDetail } from "@/lib/api";
import { sevTone } from "@/lib/api";
import { stateTone } from "@/lib/behavior";
import { formatDateTime } from "@/lib/health";

import "./ips.css";
import { dimensionNoun, ipsSubjectText, ipsTypeLabel, SOURCE_LEVEL } from "./ipsLabels";

function Interval({ anomaly }: { anomaly: IpsAnomalyDetail }) {
  if (!anomaly.anomaly_start) return <>Interval not recorded</>;
  return (
    <>
      <time dateTime={anomaly.anomaly_start}>{formatDateTime(anomaly.anomaly_start)}</time>
      {" → "}
      {anomaly.anomaly_end ? (
        <time dateTime={anomaly.anomaly_end}>{formatDateTime(anomaly.anomaly_end)}</time>
      ) : (
        // Still running: an end would report a closed incident that is open.
        "still running"
      )}
    </>
  );
}

function When({ label, iso }: { label: string; iso: string | null }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>
        {iso ? (
          <time dateTime={iso}>{formatDateTime(iso)}</time>
        ) : (
          <span className="muted">
            —<span className="sr-only"> not recorded</span>
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
  const sourceName = anomaly.log_source_name ?? anomaly.log_source_id;
  const sourceHref = `/admin/ips-sources/${anomaly.log_source_id}`;
  const entity = anomaly.subject_kind === "IPS_ENTITY";
  return (
    <header className="page-header incident-header">
      <div>
        <p className="incident-back">
          <Link href="/anomalies">← All anomalies</Link>
        </p>

        <div className="row incident-identity">
          <span className="incident-detector">{ipsTypeLabel(anomaly.anomaly_type)}</span>
          <span className={`pill pill-strong ${stateTone(anomaly.state)}`}>
            {statePillText(anomaly.state)}
          </span>
          <span className={`pill pill-quiet ${sevTone(anomaly.severity)}`}>{anomaly.severity}</span>
          {anomaly.suppressed && <span className="pill pill-quiet warn">Suppressed</span>}
          <span className="incident-interval">
            <Interval anomaly={anomaly} />
          </span>
        </div>

        <h1 className="page-title incident-source">{ipsSubjectText(anomaly)}</h1>
        <p className="ips-subject">
          <span className="muted">
            {entity
              ? anomaly.ips_entity
                ? `Entity · ${dimensionNoun(anomaly.ips_entity.dimension)}`
                : "Entity"
              : `${SOURCE_LEVEL} · all traffic of the IPS source`}
          </span>
          <span className="muted"> · IPS source </span>
          {canOpenSource ? <Link href={sourceHref}>{sourceName}</Link> : sourceName}
        </p>
        <dl className="dimension-meta ips-times">
          <When label="Detected" iso={anomaly.detected_at} />
          <When label="Opened" iso={anomaly.opened_at} />
          <When label="Resolved" iso={anomaly.resolved_at} />
        </dl>
      </div>
    </header>
  );
}
