// The Definition tab: what the watch is, read only. All backend reasons remain backend text.

import Link from "next/link";

import { UtcTime } from "@/components/ui/UtcTime";
import { enumLabel } from "@/i18n/enumLabels";
import { formatCount } from "@/lib/format";
import { formatDuration } from "@/lib/behavior";
import type { PropertyWatch, PropertyWatchCatalog, WatchScope } from "@/lib/api";

import { WatchAqlSection, type AqlRead } from "./WatchAql";
import { historyHeaderText } from "./history";
import {
  formatCoveragePercent,
  pwTypeLabel,
  scopeKindLabel,
  scopeText,
  statusLabel,
  statusReason,
} from "./pwFormat";
import { HistoryGapLine } from "./WatchHonesty";
import { ENGLISH_PW, type PwI18n } from "./pwText";

const RANGE_ARROW = " → ";

export function WatchDefinition({
  watch,
  catalog,
  scope,
  aql = null,
  i18n = ENGLISH_PW,
}: {
  watch: PropertyWatch;
  catalog: PropertyWatchCatalog | null;
  scope: WatchScope | null;
  aql?: AqlRead | null;
  i18n?: PwI18n;
}) {
  const t = i18n.t;
  const caps = catalog
    ? [
        [t("propertyWatch.definition.cap.values"), catalog.max_values_per_bucket],
        [t("propertyWatch.definition.cap.newValues"), catalog.max_new_values_per_bucket],
        [t("propertyWatch.definition.cap.tracked"), catalog.max_active_values_per_watch],
        [t("propertyWatch.definition.cap.pinned"), catalog.max_pinned_values],
        [t("propertyWatch.definition.cap.length"), catalog.value_max_length],
        [t("propertyWatch.definition.cap.sources"), catalog.max_sources_per_search],
        [t("propertyWatch.definition.cap.searches"), catalog.max_chunks_per_watch],
      ] as const
    : [];
  return (
    <div className="pw-definition stack-6">
      <section aria-labelledby="pw-def-what">
        <h2 id="pw-def-what" className="section-title">{t("propertyWatch.definition.what")}</h2>
        <dl className="dimension-meta pw-dl">
          <div><dt>{t("propertyWatch.common.scope")}</dt><dd>{scopeText(watch, i18n)}</dd></div>
          {watch.scope.detail && (
            <div>
              <dt>{t(watch.scope.kind === "LOG_SOURCE" ? "propertyWatch.definition.sourceType" : "propertyWatch.definition.path")}</dt>
              <dd>{watch.scope.detail}</dd>
            </div>
          )}
          <div><dt>{t("propertyWatch.definition.scopeKind")}</dt><dd>{scopeKindLabel(watch.scope_kind, i18n)}</dd></div>
          <div>
            <dt>{t(watch.properties.length === 1 ? "propertyWatch.common.property" : "propertyWatch.common.properties")}</dt>
            <dd>{watch.properties.map((property) => (
              <span key={`${property.kind}:${property.name}`} className="pw-def-prop">
                {property.name}<span className="muted">{t(property.kind === "NORMALIZED" ? "propertyWatch.definition.normalizedField" : "propertyWatch.definition.customProperty")}</span>
              </span>
            ))}</dd>
          </div>
          <div>
            <dt>{t("propertyWatch.definition.alwaysCounted")}</dt>
            <dd>{watch.pinned_values.length === 0 ? <span className="muted">{t("propertyWatch.definition.noPins")}</span> : (
              <ul className="pw-def-pins" aria-label={t("propertyWatch.definition.pinnedValues")}>
                {watch.pinned_values.map((tuple) => <li key={tuple.join("\u0000")}>{tuple.join(" × ")}</li>)}
              </ul>
            )}</dd>
          </div>
          <div><dt>{t("propertyWatch.definition.detectors")}</dt><dd>{watch.detectors.map((type) => pwTypeLabel(type, i18n)).join(", ") || t("propertyWatch.common.none")}</dd></div>
          <div><dt>{t("propertyWatch.definition.incidentSeverity")}</dt><dd>{i18n.locale === "en" ? watch.severity : enumLabel(i18n.t, "severity", watch.severity)}</dd></div>
        </dl>
      </section>

      <section aria-labelledby="pw-def-members">
        <h2 id="pw-def-members" className="section-title">{t("propertyWatch.definition.membership")}</h2>
        <Membership scope={scope} i18n={i18n} />
      </section>

      <section aria-labelledby="pw-def-run">
        <h2 id="pw-def-run" className="section-title">{t("propertyWatch.definition.collection")}</h2>
        <dl className="dimension-meta pw-dl">
          <div><dt>{t("propertyWatch.definition.enabled")}</dt><dd>{t(watch.enabled ? "propertyWatch.common.yes" : "propertyWatch.common.no")}</dd></div>
          <div><dt>{t("propertyWatch.definition.version")}</dt><dd className="num">{formatCount(watch.watch_version, i18n.locale)}<span className="muted">{t("propertyWatch.definition.versionNote")}</span></dd></div>
          <div>
            <dt>{t("propertyWatch.definition.cadence")}</dt>
            <dd>{t("propertyWatch.definition.cadenceBeforeLink", { duration: formatDuration(watch.cadence_seconds, i18n) })}<Link href="/admin/search-cadence">{t("propertyWatch.definition.searchCadence")}</Link>{t("propertyWatch.definition.cadenceAfterLink")}</dd>
          </div>
          <div><dt>{t("propertyWatch.definition.alerting")}</dt><dd>{t(watch.alerting_mode === "shadow" ? "propertyWatch.definition.shadow" : "propertyWatch.definition.paging")}</dd></div>
          <div><dt>{t("propertyWatch.definition.created")}</dt><dd className="num"><UtcTime value={watch.created_at} />{watch.created_by && <span className="muted">{t("propertyWatch.definition.by", { operator: watch.created_by })}</span>}</dd></div>
          <div><dt>{t("propertyWatch.definition.changed")}</dt><dd className="num"><UtcTime value={watch.updated_at} />{watch.updated_by && <span className="muted">{t("propertyWatch.definition.by", { operator: watch.updated_by })}</span>}</dd></div>
        </dl>
      </section>

      <section aria-labelledby="pw-def-history">
        <h2 id="pw-def-history" className="section-title">{t("propertyWatch.definition.history")}</h2>
        <dl className="dimension-meta pw-dl">
          <div><dt>{t("propertyWatch.common.state")}</dt><dd>{historyHeaderText(watch.history, i18n)}</dd></div>
          <div><dt>{t("propertyWatch.definition.requested")}</dt><dd className="num">{watch.history.days_requested === null ? t("propertyWatch.definition.noHistory") : t("propertyWatch.common.days", { count: watch.history.days_requested })}</dd></div>
          <TimeSpan label={t("propertyWatch.definition.requestedWindow")} start={watch.history.target_start} end={watch.history.end} missing={t("propertyWatch.common.notRecorded")} />
          <TimeSpan label={t("propertyWatch.definition.readFrom")} start={watch.history.floor} end={watch.history.through} missing={t("propertyWatch.definition.readSpanMissing")} />
          <div><dt>{t("propertyWatch.definition.historyFloor")}</dt><dd className="num">{watch.history.floor ? <UtcTime value={watch.history.floor} /> : t("propertyWatch.common.notRecorded")}</dd></div>
          <div><dt>{t("propertyWatch.definition.membershipBasis")}</dt><dd>{t("propertyWatch.definition.membershipBasisBody")}</dd></div>
        </dl>
        {(watch.history.gaps ?? []).length > 0 && (
          <div><h3 className="pw-subtitle">{t("propertyWatch.definition.historyGaps")}</h3>
            <ul className="pw-def-changes" aria-label={t("propertyWatch.definition.historyGaps")}>
              {(watch.history.gaps ?? []).map((gap) => <li key={`${gap.start}-${gap.end}`}><HistoryGapLine gap={gap} i18n={i18n} /></li>)}
            </ul>
          </div>
        )}
      </section>

      <section aria-labelledby="pw-def-aql"><h2 id="pw-def-aql" className="section-title">{t("propertyWatch.definition.generatedQuery")}</h2><WatchAqlSection read={aql} /></section>
      {caps.length > 0 && catalog && (
        <section aria-labelledby="pw-def-caps">
          <h2 id="pw-def-caps" className="section-title">{t("propertyWatch.definition.caps")}</h2>
          <p className="subtitle">{t("propertyWatch.definition.capsBody", { floor: formatCoveragePercent(catalog.min_coverage, i18n) })}</p>
          <dl className="dimension-meta pw-dl">{caps.map(([label, value]) => <div key={label}><dt>{label}</dt><dd className="num">{formatCount(value, i18n.locale)}</dd></div>)}</dl>
        </section>
      )}
      {!catalog && <div className="notice" role="alert">{t("propertyWatch.definition.capsFailed")}</div>}
    </div>
  );
}

function TimeSpan({ label, start, end, missing }: { label: string; start: string | null; end: string | null; missing: string }) {
  return <div><dt>{label}</dt><dd className="num">{start && end ? <><UtcTime value={start} />{RANGE_ARROW}<UtcTime value={end} /></> : missing}</dd></div>;
}

function Membership({ scope, i18n }: { scope: WatchScope | null; i18n: PwI18n }) {
  const t = i18n.t;
  if (scope === null) return <div className="notice" role="alert">{t("propertyWatch.definition.membershipFailed")}</div>;
  return (
    <div className="stack-6">
      {!scope.resolved && scope.state && <p className="banner warn"><span><strong>{statusLabel(scope.state, i18n).toLocaleUpperCase(i18n.locale)}.</strong>{" "}{statusReason(scope.state, scope.reason, i18n)} {t("propertyWatch.definition.membershipWarning")}</span></p>}
      <dl className="dimension-meta pw-dl">
        <div><dt>{t("propertyWatch.definition.members")}</dt><dd className="num">{scope.member_count === null ? <span className="muted">{t("propertyWatch.definition.notStated")}</span> : <>{formatCount(scope.member_count, i18n.locale)}<span className="muted"> {t(scope.member_count === 1 ? "propertyWatch.definition.sourceOne" : "propertyWatch.definition.sourceMany")}</span></>}</dd></div>
        <div><dt>{t("propertyWatch.definition.inventorySynced")}</dt><dd className="num">{scope.inventory_synced_at ? <><UtcTime value={scope.inventory_synced_at} />{scope.inventory_age_seconds !== null && <span className="muted">{t("propertyWatch.definition.ageAgo", { age: formatDuration(scope.inventory_age_seconds, i18n) })}</span>}</> : <span className="muted">{t("propertyWatch.common.notRecorded")}</span>}</dd></div>
        {scope.groups_walked.length > 0 && <div><dt>{t("propertyWatch.definition.groupsWalked")}</dt><dd><ul className="pw-def-pins" aria-label={t("propertyWatch.definition.groupsWalked")}>{scope.groups_walked.map((group) => <li key={group.qradar_id}>{group.name}</li>)}</ul></dd></div>}
      </dl>
      <div><h3 className="pw-subtitle">{t("propertyWatch.definition.membershipChanges")}</h3>
        {scope.changes.length === 0 ? <p className="subtitle">{t("propertyWatch.definition.noChanges")}</p> : <ul className="pw-def-changes" aria-label={t("propertyWatch.definition.membershipChanges")}>{scope.changes.map((change) => <li key={change.bucket_start}><UtcTime value={change.bucket_start} />{": "}{t("propertyWatch.definition.change", { before: change.member_count_before === null ? t("propertyWatch.common.unknown") : formatCount(change.member_count_before, i18n.locale), after: change.member_count_after === null ? t("propertyWatch.common.unknown") : formatCount(change.member_count_after, i18n.locale) })}{change.in_grace_window && <span className="muted">{t("propertyWatch.definition.insideGrace")}</span>}</li>)}</ul>}
      </div>
      {scope.members.length > 0 && <div><h3 className="pw-subtitle">{t("propertyWatch.definition.memberNames")}</h3><p className="subtitle">{scope.members_total !== null && scope.members_total > scope.members.length ? t("propertyWatch.definition.membersLimited", { shown: formatCount(scope.members.length, i18n.locale), total: formatCount(scope.members_total, i18n.locale) }) : t("propertyWatch.definition.membersAll", { shown: formatCount(scope.members.length, i18n.locale) })}</p><ul className="pw-def-members" aria-label={t("propertyWatch.definition.memberNames")} tabIndex={0}>{scope.members.map((member) => <li key={member}>{member}</li>)}</ul></div>}
    </div>
  );
}
