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
import { useTranslations } from "next-intl";

import { useEnumLabels } from "@/i18n/enumLabels";
import type { SourceBehavior } from "@/lib/api";
import { ENGLISH_ANOMALIES } from "@/lib/anomaliesI18n";
import { subjectKindLabel, SUBJECT_KINDS } from "@/lib/anomalyQuery";
import { detectorLabel } from "@/lib/behavior";
import { CLOSE_MARK, evidenceCode, wordOrValue, type BehaviorI18n } from "@/lib/behaviorI18n";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";
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

/** The detector's name in a chip: the IPS, property-watch and WAF names stay as their helpers write them. */
function detectorName(text: string, i18n: BehaviorI18n): string {
  if (isIpsType(text)) return ipsTypeLabel(text, i18n);
  if (isPwType(text)) return pwTypeLabel(text, i18n);
  if (DETECTOR_TYPES.includes(text)) return detectorLabel(text, i18n);
  return anomalyTypeLabel(text, i18n);
}

/** Human labels for the active-filter chips. */
export function chipLabel(
  key: keyof AnomalyFilterValues,
  value: string | boolean,
  sources: SourceBehavior[],
  i18n: BehaviorI18n = ENGLISH_ANOMALIES,
): string {
  const text = String(value);
  const say = (name: string, values?: Record<string, string>) =>
    i18n.t(`anomalies.filters.chip.${name}`, values);
  switch (key) {
    case "active_only":
      return say("activeOnly");
    case "subject_kind":
      return say("subject", { value: subjectKindLabel(text, i18n) });
    case "ips_entity_id":
      return say("ipsEntity", { id: text.slice(0, 8) });
    case "property_watch_id":
      return say("propertyWatch", { id: text.slice(0, 8) });
    case "log_source_id": {
      const match = sources.find((s) => s.log_source_id === text);
      return say("source", { value: match?.name ?? text });
    }
    case "instance_id":
      return say("instance", { value: text });
    case "anomaly_type":
      return say("detector", { value: detectorName(text, i18n) });
    case "state":
      return say("state", { value: wordOrValue(i18n, "enums.anomalyState", text) });
    case "severity":
      return say("severity", { value: wordOrValue(i18n, "enums.severityPill", text) });
    case "evidence_status":
      return say("evidence", { value: evidenceCode(i18n, text) });
    case "since":
      return say("since", { value: utcTimeText(text) });
    case "until":
      return say("until", { value: utcTimeText(text) });
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
  const t = useTranslations("anomalies.filters");
  const i18n = useBehaviorI18n();
  const { label, severityPill } = useEnumLabels();
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
          {t("sourcesFailed")}
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
          {t("subjectLabel")}
        </label>
        <select
          id="anomaly-subject"
          name="subject_kind"
          defaultValue={values.subject_kind ?? ""}
        >
          <option value="">{t("allSubjects")}</option>
          {SUBJECT_KINDS.map((k) => (
            <option key={k.value} value={k.value}>
              {subjectKindLabel(k.value, i18n)}
            </option>
          ))}
        </select>

        <label className="sr-only" htmlFor="anomaly-state">
          {t("stateLabel")}
        </label>
        <select id="anomaly-state" name="state" defaultValue={values.state}>
          <option value="">{t("allStates")}</option>
          {STATES.map((s) => (
            <option key={s} value={s}>
              {label("anomalyState", s)}
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
          {t("activeOnly")}
        </label>

        <label className="sr-only" htmlFor="anomaly-detector">
          {t("detectorLabel")}
        </label>
        <select
          id="anomaly-detector"
          name="anomaly_type"
          defaultValue={values.anomaly_type}
        >
          <option value="">{t("allDetectors")}</option>
          <optgroup label={t("groupLogSources")}>
            {DETECTOR_TYPES.map((type) => (
              <option key={type} value={type}>
                {detectorLabel(type, i18n)}
              </option>
            ))}
          </optgroup>
          <optgroup label={t("groupWaf")}>
            {WAF_TYPES.map((type) => (
              <option key={type} value={type}>
                {anomalyTypeLabel(type, i18n)}
              </option>
            ))}
          </optgroup>
          <optgroup label={t("groupIps")}>
            {IPS_TYPES.map((type) => (
              <option key={type} value={type}>
                {ipsTypeLabel(type, i18n)}
              </option>
            ))}
          </optgroup>
          <optgroup label={t("groupPw")}>
            {PW_TYPES.map((type) => (
              <option key={type} value={type}>
                {pwTypeLabel(type, i18n)}
              </option>
            ))}
          </optgroup>
        </select>

        <label className="sr-only" htmlFor="anomaly-severity">
          {t("severityLabel")}
        </label>
        <select id="anomaly-severity" name="severity" defaultValue={values.severity}>
          <option value="">{t("allSeverities")}</option>
          {SEVERITIES.map((s) => (
            <option key={s} value={s}>
              {severityPill(s)}
            </option>
          ))}
        </select>

        {/* The backend caps the range; these are datetime-local so an operator
            cannot accidentally request an unbounded window. The label is
            visible, because two bare date fields say neither which is which
            nor which timezone they mean. */}
        <label className="filter-field" htmlFor="anomaly-since">
          {t("since")}
          <input
            id="anomaly-since"
            type="datetime-local"
            name="since"
            defaultValue={toInputValue(values.since)}
          />
        </label>
        <label className="filter-field" htmlFor="anomaly-until">
          {t("until")}
          <input
            id="anomaly-until"
            type="datetime-local"
            name="until"
            defaultValue={toInputValue(values.until)}
          />
        </label>

        <button type="submit">{t("submit")}</button>

        {/* Opened when something inside is active, so a filtered view never
            hides the control responsible for it. */}
        <details className="disclosure filter-more" open={advancedActive}>
          <summary className="disclosure-summary">
            <span className="disclosure-label">{t("more")}</span>
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
                    {t("sourceLabel")}
                  </label>
                  <select
                    id="anomaly-source"
                    name="log_source_id"
                    defaultValue={values.log_source_id}
                  >
                    <option value="">{t("allSources")}</option>
                    {sources.map((s) => (
                      <option key={s.log_source_id} value={s.log_source_id}>
                        {s.name}
                      </option>
                    ))}
                  </select>
                </>
              )}

              <label className="sr-only" htmlFor="anomaly-instance">
                {t("instanceLabel")}
              </label>
              <input
                id="anomaly-instance"
                type="search"
                name="instance_id"
                defaultValue={values.instance_id}
                // placeholder="Instance…" (anomalies.filters.instancePlaceholder): the short form
                // `placeholders.test.tsx` pins, kept in the source as text.
                placeholder={t("instancePlaceholder")}
              />

              <label className="sr-only" htmlFor="anomaly-evidence">
                {t("evidenceLabel")}
              </label>
              <select
                id="anomaly-evidence"
                name="evidence_status"
                defaultValue={values.evidence_status}
              >
                <option value="">{t("allEvidence")}</option>
                {EVIDENCE.map((e) => (
                  <option key={e} value={e}>
                    {evidenceCode(i18n, e)}
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
          <span className="muted">{t("filteredBy")}</span>
          {activeChips.map(([key, value]) => (
            <Link
              key={key}
              href={withoutFilter(values, key)}
              className="filter-chip"
              aria-label={t("removeFilter", { chip: chipLabel(key, value, sources, i18n) })}
            >
              {key === "since" || key === "until"
                ? t.rich(key === "since" ? "chip.sinceRich" : "chip.untilRich", {
                    value: () => <UtcTime value={String(value)} />,
                  })
                : chipLabel(key, value, sources, i18n)}
              <span aria-hidden="true">{CLOSE_MARK}</span>
            </Link>
          ))}
          <Link href="/anomalies" className="filter-clear">
            {t("clearAll")}
          </Link>
        </div>
      )}
    </>
  );
}
