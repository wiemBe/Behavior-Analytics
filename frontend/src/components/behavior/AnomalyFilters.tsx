// Filters for the anomaly list.
//
// A plain GET form: no client JavaScript, the resulting URL is shareable, and
// submitting resets paging naturally because `offset` is simply absent from
// the new query string.
//
// The common filters are visible and three are behind a disclosure. The split is by
// how often each is used from this page rather than by importance — an analyst
// arriving at the list narrows by state, detector, severity and time; source
// and instance are usually already chosen by the link that brought them here,
// and evidence status is an auditing filter. The disclosure opens itself when
// one of the filters inside it is active, so a filtered view never hides the
// control responsible for it.

import Link from "next/link";

import type { SourceBehavior } from "@/lib/api";
import { subjectKindLabel, SUBJECT_KINDS } from "@/lib/anomalyQuery";
import { IPS_TYPES, isIpsType, ipsTypeLabel } from "@/components/ips/ipsLabels";
import { PW_TYPES, pwTypeLabel, isPwType } from "@/components/propertywatch/pwFormat";
import { WAF_TYPES, anomalyTypeLabel } from "@/components/waf/wafLabels";
import { UtcTime, utcTimeText } from "@/components/ui/UtcTime";


const DETECTOR_TYPES = ["VOLUME_SPIKE", "VOLUME_DROP", "NO_EVENTS"];
const STATES = [
  "CANDIDATE",
  "OPEN",
  "RECOVERING",
  "RESOLVED",
  "SUPPRESSED",
  "NORMAL",
  "INSUFFICIENT_DATA",
];
const SEVERITIES = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"];
const EVIDENCE = [
  "NOT_REQUESTED",
  "PENDING",
  "COMPLETE",
  "PARTIAL",
  "UNAVAILABLE",
  "FAILED",
];

export interface AnomalyFilterValues {
  /** What the anomaly is about, including URL-only IPS for entities and sources together. */
  subject_kind?: string;
  /** One IPS entity, from a link on an entity view. Not editable here; a chip clears it. */
  ips_entity_id?: string;
  /** One property watch, from a link on its page. Not editable here; a chip clears it. */
  property_watch_id?: string;
  active_only: boolean;
  log_source_id: string;
  instance_id: string;
  anomaly_type: string;
  state: string;
  severity: string;
  evidence_status: string;
  since: string;
  until: string;
}

/** Human labels for the active-filter chips. */
export function chipLabel(
  key: keyof AnomalyFilterValues,
  value: string | boolean,
  sources: SourceBehavior[],
): string {
  const text = String(value);
  switch (key) {
    case "active_only":
      return "Active only";
    case "subject_kind":
      return `Subject: ${subjectKindLabel(text)}`;
    case "ips_entity_id":
      return `IPS entity: ${text.slice(0, 8)}…`;
    case "property_watch_id":
      return `Property watch: ${text.slice(0, 8)}…`;
    case "log_source_id": {
      const match = sources.find((s) => s.log_source_id === text);
      return `Source: ${match?.name ?? text}`;
    }
    case "instance_id":
      return `Instance: ${text}`;
    case "anomaly_type":
      return `Detector: ${isIpsType(text) ? ipsTypeLabel(text) : isPwType(text) ? pwTypeLabel(text) : anomalyTypeLabel(text)}`;
    case "state":
      return `State: ${text}`;
    case "severity":
      return `Severity: ${text}`;
    case "evidence_status":
      return `Evidence: ${text}`;
    case "since":
      return `From: ${utcTimeText(text)}`;
    case "until":
      return `To: ${utcTimeText(text)}`;
    default:
      return `${key}: ${text}`;
  }
}

/** The URL with one filter removed, so each chip can clear just itself. */
export function withoutFilter(
  values: AnomalyFilterValues,
  drop: keyof AnomalyFilterValues,
): string {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(values)) {
    if (v && k !== drop) qs.set(k, String(v));
  }
  const s = qs.toString();
  return s ? `/anomalies?${s}` : "/anomalies";
}

/**
 * An ISO instant as a `datetime-local` value.
 *
 * The page hands this component the normalised ISO instant
 * (`2026-10-12T09:15:00.000Z`), which a datetime-local input cannot display:
 * it needs `YYYY-MM-DDTHH:mm`. Without this the field came back blank after the
 * very filter it had just applied.
 */
export function toInputValue(iso: string): string {
  return /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(iso) ? iso.slice(0, 16) : "";
}

export function AnomalyFilters({
  values,
  sources,
  sourcesFailed = false,
}: {
  values: AnomalyFilterValues;
  sources: SourceBehavior[];
  /** The source read failed. The select is omitted; the anomaly list is unaffected. */
  sourcesFailed?: boolean;
}) {
  const advancedActive = Boolean(
    values.log_source_id || values.instance_id || values.evidence_status,
  );
  const activeChips = (
    Object.entries(values) as Array<[
      keyof AnomalyFilterValues,
      string | boolean | undefined,
    ]>
  ).filter(
    (entry): entry is [keyof AnomalyFilterValues, string | boolean] =>
      Boolean(entry[1]),
  );

  return (
    <>
      {sourcesFailed && (
        <p className="notice" role="status">
          The source list could not be loaded, so the source filter is unavailable; the anomaly
          list below is complete.
        </p>
      )}
      <form className="filters" method="get" action="/anomalies">
        {/* Carried through a re-filter so the entity link does not silently widen. */}
        {values.ips_entity_id && (
          <input type="hidden" name="ips_entity_id" value={values.ips_entity_id} />
        )}
        {values.property_watch_id && (
          <input type="hidden" name="property_watch_id" value={values.property_watch_id} />
        )}
        <label className="sr-only" htmlFor="anomaly-subject">
          Subject kind
        </label>
        <select
          id="anomaly-subject"
          name="subject_kind"
          defaultValue={values.subject_kind ?? ""}
        >
          <option value="">All subjects</option>
          {SUBJECT_KINDS.map((k) => (
            <option key={k.value} value={k.value}>
              {k.label}
            </option>
          ))}
        </select>

        <label className="sr-only" htmlFor="anomaly-state">
          Lifecycle state
        </label>
        <select id="anomaly-state" name="state" defaultValue={values.state}>
          <option value="">All states</option>
          {STATES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>

        <label className="filter-field filter-check" htmlFor="anomaly-active-only">
          <input
            id="anomaly-active-only"
            type="checkbox"
            name="active_only"
            value="true"
            defaultChecked={values.active_only}
          />
          Active only
        </label>

        <label className="sr-only" htmlFor="anomaly-detector">
          Detector type
        </label>
        <select
          id="anomaly-detector"
          name="anomaly_type"
          defaultValue={values.anomaly_type}
        >
          <option value="">All detectors</option>
          <optgroup label="Log sources">
            {DETECTOR_TYPES.map((t) => (
              <option key={t} value={t}>
                {anomalyTypeLabel(t)}
              </option>
            ))}
          </optgroup>
          <optgroup label="WAF endpoints">
            {WAF_TYPES.map((t) => (
              <option key={t} value={t}>
                {anomalyTypeLabel(t)}
              </option>
            ))}
          </optgroup>
          <optgroup label="IPS entities and sources">
            {IPS_TYPES.map((t) => (
              <option key={t} value={t}>
                {ipsTypeLabel(t)}
              </option>
            ))}
          </optgroup>
          <optgroup label="Property watch values">
            {PW_TYPES.map((t) => (
              <option key={t} value={t}>
                {pwTypeLabel(t)}
              </option>
            ))}
          </optgroup>
        </select>

        <label className="sr-only" htmlFor="anomaly-severity">
          Severity
        </label>
        <select id="anomaly-severity" name="severity" defaultValue={values.severity}>
          <option value="">All severities</option>
          {SEVERITIES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>

        {/* The backend caps the range; these are datetime-local so an operator
            cannot accidentally request an unbounded window. The label is
            visible, because two bare date fields say neither which is which
            nor which timezone they mean. */}
        <label className="filter-field" htmlFor="anomaly-since">
          Start time (UTC)
          <input
            id="anomaly-since"
            type="datetime-local"
            name="since"
            defaultValue={toInputValue(values.since)}
          />
        </label>
        <label className="filter-field" htmlFor="anomaly-until">
          End time (UTC)
          <input
            id="anomaly-until"
            type="datetime-local"
            name="until"
            defaultValue={toInputValue(values.until)}
          />
        </label>

        <button type="submit">Filter</button>

        {/* Opened when something inside is active, so a filtered view never
            hides the control responsible for it. */}
        <details className="disclosure filter-more" open={advancedActive}>
          <summary className="disclosure-summary">
            <span className="disclosure-label">More filters</span>
          </summary>
          <div className="disclosure-body">
            <div className="filters filters-advanced">
              {sourcesFailed ? (
                values.log_source_id ? (
                  <input type="hidden" name="log_source_id" value={values.log_source_id} />
                ) : null
              ) : (
                <>
                  <label className="sr-only" htmlFor="anomaly-source">
                    Log source
                  </label>
                  <select
                    id="anomaly-source"
                    name="log_source_id"
                    defaultValue={values.log_source_id}
                  >
                    <option value="">All log sources</option>
                    {sources.map((s) => (
                      <option key={s.log_source_id} value={s.log_source_id}>
                        {s.name}
                      </option>
                    ))}
                  </select>
                </>
              )}

              <label className="sr-only" htmlFor="anomaly-instance">
                QRadar instance
              </label>
              <input
                id="anomaly-instance"
                type="search"
                name="instance_id"
                defaultValue={values.instance_id}
                placeholder="Instance…"
              />

              <label className="sr-only" htmlFor="anomaly-evidence">
                Evidence status
              </label>
              <select
                id="anomaly-evidence"
                name="evidence_status"
                defaultValue={values.evidence_status}
              >
                <option value="">All evidence states</option>
                {EVIDENCE.map((e) => (
                  <option key={e} value={e}>
                    {e}
                  </option>
                ))}
              </select>
            </div>
          </div>
        </details>
      </form>

      {/* Chips render only when something is actually filtered. */}
      {activeChips.length > 0 && (
        <div className="row filter-chips">
          <span className="muted">Filtered by</span>
          {activeChips.map(([key, value]) => (
            <Link
              key={key}
              href={withoutFilter(values, key)}
              className="filter-chip"
              aria-label={`Remove filter ${chipLabel(key, value, sources)}`}
            >
              {key === "since" || key === "until" ? (
                <>
                  {key === "since" ? "From: " : "To: "}
                  <UtcTime value={String(value)} />
                </>
              ) : (
                chipLabel(key, value, sources)
              )}
              <span aria-hidden="true">✕</span>
            </Link>
          ))}
          <Link href="/anomalies" className="filter-clear">
            Clear all
          </Link>
        </div>
      )}
    </>
  );
}
