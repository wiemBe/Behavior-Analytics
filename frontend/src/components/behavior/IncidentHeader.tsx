// The incident's identity, in one block.
//
// Everything an analyst needs to know they are looking at the right thing:
// what kind of change, how it is currently classified, when it ran, and which
// source. The identity line is the primary text; the source name is the page's
// <h1> and appears exactly once, at a size that survives a host name of
// unbounded length.

import Link from "next/link";

import type { AnomalyDetail } from "@/lib/api";
import { sevTone } from "@/lib/api";
import { detectorLabel, stateTone } from "@/lib/behavior";
import { formatDateTime } from "@/lib/health";

export { detectorLabel };

/**
 * The anomaly's interval, or an explicit statement that it has not ended.
 *
 * Each end is a `time` element held on one line, so the interval can only wrap
 * at the arrow rather than through a timestamp.
 */
function Interval({ anomaly }: { anomaly: AnomalyDetail }) {
  if (!anomaly.anomaly_start) return <>Interval not recorded</>;
  return (
    <>
      <time dateTime={anomaly.anomaly_start}>{formatDateTime(anomaly.anomaly_start)}</time>
      {" → "}
      {anomaly.anomaly_end ? (
        <time dateTime={anomaly.anomaly_end}>{formatDateTime(anomaly.anomaly_end)}</time>
      ) : (
        // Still running: no end exists, and inventing one would report a
        // closed incident that is still open.
        "still running"
      )}
    </>
  );
}

export function IncidentHeader({ anomaly }: { anomaly: AnomalyDetail }) {
  return (
    <header className="page-header incident-header">
      <div>
        <p className="incident-back">
          <Link href="/anomalies">← All anomalies</Link>
        </p>

        <div className="row incident-identity">
          <span className="incident-detector">
            {detectorLabel(anomaly.anomaly_type)}
          </span>
          {/* Lifecycle state is the dominant status: it is the fact that
              decides whether this needs work now. */}
          <span className={`pill pill-strong ${stateTone(anomaly.state)}`}>
            {anomaly.state}
          </span>
          {/* Severity is real but secondary, so it is quiet by default. */}
          <span className={`pill pill-quiet ${sevTone(anomaly.severity)}`}>
            {anomaly.severity}
          </span>
          {anomaly.suppressed && (
            <span className="pill pill-quiet warn">Suppressed</span>
          )}
          <span className="incident-interval">
            <Interval anomaly={anomaly} />
          </span>
        </div>

        <h1 className="page-title incident-source">
          {anomaly.log_source_name ?? "Unknown source"}
        </h1>
      </div>

      <div className="page-header-actions">
        <Link
          href={`/behavior/sources/${anomaly.log_source_id}`}
          className="action secondary button-link"
        >
          View source behavior
        </Link>
      </div>
    </header>
  );
}
