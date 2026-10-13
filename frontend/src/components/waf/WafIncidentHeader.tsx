// The WAF incident's identity: what kind of change, on which endpoint, of which
// source.
//
// The endpoint (`GET /portal/login`) is the page's <h1>; the detector is the
// largest text, as on a Phase A incident. When the source exposes no virtual
// host, the header says so in the one sentence design §27.15 fixes, because an
// endpoint without a host may combine traffic of unrelated applications.

import Link from "next/link";

import type { WafAnomalyDetail } from "@/lib/api";
import { sevTone } from "@/lib/api";
import { stateTone } from "@/lib/behavior";
import { formatDateTime } from "@/lib/health";

import { anomalyTypeLabel, endpointLabel, stateLabel } from "./wafLabels";

/** Design §27.15, verbatim. */
export const NO_VIRTUAL_HOST_SENTENCE =
  "This WAF source does not expose a virtual host, so this endpoint may combine traffic for different applications that share the path.";

function Interval({ anomaly }: { anomaly: WafAnomalyDetail }) {
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

export function WafIncidentHeader({ anomaly }: { anomaly: WafAnomalyDetail }) {
  const endpoint = anomaly.waf_endpoint;
  const sourceHref = `/behavior/waf/sources/${anomaly.log_source_id}`;
  return (
    <>
      <header className="page-header incident-header">
        <div>
          <p className="incident-back">
            <Link href="/anomalies">← All anomalies</Link>
          </p>

          <div className="row incident-identity">
            <span className="incident-detector">{anomalyTypeLabel(anomaly.anomaly_type)}</span>
            <span className={`pill pill-strong ${stateTone(anomaly.state)}`}>{stateLabel(anomaly.state)}</span>
            <span className={`pill pill-quiet ${sevTone(anomaly.severity)}`}>
              {anomaly.severity}
            </span>
            {anomaly.suppressed && <span className="pill pill-quiet warn">Suppressed</span>}
            <span className="incident-interval">
              <Interval anomaly={anomaly} />
            </span>
          </div>

          <h1 className="page-title incident-source waf-endpoint">{endpointLabel(endpoint)}</h1>
          <p className="waf-subject">
            <span className="muted">Source </span>
            <Link href={sourceHref}>{anomaly.log_source_name ?? anomaly.log_source_id}</Link>
            {endpoint.virtual_host ? (
              <>
                <span className="muted"> · Virtual host </span>
                {endpoint.virtual_host}
              </>
            ) : endpoint.virtual_host_available ? (
              <span className="muted"> · No virtual host on this endpoint</span>
            ) : null}
          </p>
        </div>

        <div className="page-header-actions">
          <Link href={sourceHref} className="action secondary button-link">
            View WAF source
          </Link>
        </div>
      </header>

      {!endpoint.virtual_host_available && <p className="banner">{NO_VIRTUAL_HOST_SENTENCE}</p>}
    </>
  );
}
