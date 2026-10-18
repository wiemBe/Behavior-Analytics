// The Definition tab: what the watch is, read only. The scope as the synced inventory names it,
// the properties, the pinned values, the detectors, and the cardinality caps in force. All of it
// is text; none of it is an input. The generated query is not served by any read route, so it is
// not drawn here.

import Link from "next/link";

import type { PropertyWatch, PropertyWatchCatalog, WatchScope } from "@/lib/api";
import { formatDateTime } from "@/lib/health";

import { formatAge } from "@/lib/health";

import { pwTypeLabel, scopeKindLabel, scopeText, statusLabel, statusReason } from "./pwFormat";

export function WatchDefinition({
  watch,
  catalog,
  scope,
}: {
  watch: PropertyWatch;
  catalog: PropertyWatchCatalog | null;
  /** The resolved membership, or null when it could not be read. */
  scope: WatchScope | null;
}) {
  const caps = catalog
    ? [
        ["Values kept per bucket", catalog.max_values_per_bucket],
        ["New values admitted per bucket", catalog.max_new_values_per_bucket],
        ["Tracked values per watch", catalog.max_active_values_per_watch],
        ["Pinned values per watch", catalog.max_pinned_values],
        ["Longest value, in characters", catalog.value_max_length],
        ["Sources per search", catalog.max_sources_per_search],
        ["Searches per watch and run", catalog.max_chunks_per_watch],
      ]
    : [];
  return (
    <div className="pw-definition stack-6">
      <section aria-labelledby="pw-def-what">
        <h2 id="pw-def-what" className="section-title">
          What it watches
        </h2>
        <dl className="dimension-meta pw-dl">
          <div>
            <dt>Scope</dt>
            <dd>{scopeText(watch)}</dd>
          </div>
          {watch.scope.detail && (
            <div>
              <dt>{watch.scope.kind === "LOG_SOURCE" ? "Source type" : "Path"}</dt>
              <dd>{watch.scope.detail}</dd>
            </div>
          )}
          <div>
            <dt>Scope kind</dt>
            <dd>{scopeKindLabel(watch.scope_kind)}</dd>
          </div>
          <div>
            <dt>{watch.properties.length === 1 ? "Property" : "Properties"}</dt>
            <dd>
              {watch.properties.map((p) => (
                <span key={`${p.kind}:${p.name}`} className="pw-def-prop">
                  {p.name}
                  <span className="muted">
                    {p.kind === "NORMALIZED" ? " normalized field" : " custom property"}
                  </span>
                </span>
              ))}
            </dd>
          </div>
          <div>
            <dt>Always counted</dt>
            <dd>
              {watch.pinned_values.length === 0 ? (
                <span className="muted">no pinned values</span>
              ) : (
                <ul className="pw-def-pins" aria-label="Pinned values">
                  {watch.pinned_values.map((tuple) => (
                    <li key={tuple.join("\u0000")}>{tuple.join(" × ")}</li>
                  ))}
                </ul>
              )}
            </dd>
          </div>
          <div>
            <dt>Detectors</dt>
            <dd>{watch.detectors.map(pwTypeLabel).join(", ") || "none"}</dd>
          </div>
          <div>
            <dt>Incident severity</dt>
            <dd>{watch.severity}</dd>
          </div>
        </dl>
      </section>

      <section aria-labelledby="pw-def-members">
        <h2 id="pw-def-members" className="section-title">
          Scope membership
        </h2>
        <Membership scope={scope} />
      </section>

      <section aria-labelledby="pw-def-run">
        <h2 id="pw-def-run" className="section-title">
          How it is collected
        </h2>
        <dl className="dimension-meta pw-dl">
          <div>
            <dt>Enabled</dt>
            <dd>{watch.enabled ? "Yes" : "No"}</dd>
          </div>
          <div>
            <dt>Version</dt>
            <dd className="num">
              {watch.watch_version}
              <span className="muted"> a new scope or property set starts a new version; values of another version are never compared</span>
            </dd>
          </div>
          <div>
            <dt>Cadence</dt>
            <dd>
              Set for the whole PROPERTY_WATCH search family in{" "}
              <Link href="/admin/search-cadence">Search cadence</Link>; buckets are five minutes wide.
            </dd>
          </div>
          <div>
            <dt>Alerting</dt>
            <dd>
              {watch.alerting_mode === "shadow"
                ? "Shadow: incidents are recorded and nothing pages."
                : "Alerting: incidents page."}
            </dd>
          </div>
          <div>
            <dt>Created</dt>
            <dd className="num">
              <time dateTime={watch.created_at}>{formatDateTime(watch.created_at)}</time>
              {watch.created_by && <span className="muted"> by {watch.created_by}</span>}
            </dd>
          </div>
          <div>
            <dt>Last changed</dt>
            <dd className="num">
              <time dateTime={watch.updated_at}>{formatDateTime(watch.updated_at)}</time>
              {watch.updated_by && <span className="muted"> by {watch.updated_by}</span>}
            </dd>
          </div>
        </dl>
      </section>

      {caps.length > 0 && catalog && (
        <section aria-labelledby="pw-def-caps">
          <h2 id="pw-def-caps" className="section-title">
            Cardinality caps in force
          </h2>
          <p className="subtitle">
            A value beyond a cap is counted in its bucket and never plotted or judged. The coverage
            floor is {Math.round(catalog.min_coverage * 100)} %: below it, verdicts that read an
            absence as evidence are insufficient data.
          </p>
          <dl className="dimension-meta pw-dl">
            {caps.map(([label, value]) => (
              <div key={label}>
                <dt>{label}</dt>
                <dd className="num">{value.toLocaleString("en-US")}</dd>
              </div>
            ))}
          </dl>
        </section>
      )}
      {!catalog && (
        <div className="notice" role="alert">
          The cardinality caps could not be read, so they are not shown. Reload the page to try again.
        </div>
      )}
    </div>
  );
}

function Membership({ scope }: { scope: WatchScope | null }) {
  if (scope === null) {
    return (
      <div className="notice" role="alert">
        The scope&apos;s membership could not be loaded, so it is not shown. Reload the page to try again.
      </div>
    );
  }
  return (
    <div className="stack-6">
      {!scope.resolved && scope.state && (
        <p className="banner warn">
          <span>
            <strong>{statusLabel(scope.state).toUpperCase()}.</strong>{" "}
            {statusReason(scope.state, scope.reason)} What the inventory holds is shown; no count is
            drawn from nothing.
          </span>
        </p>
      )}
      <dl className="dimension-meta pw-dl">
        <div>
          <dt>Members</dt>
          <dd className="num">
            {scope.member_count === null ? (
              <span className="muted">not stated</span>
            ) : (
              <>
                {scope.member_count.toLocaleString("en-US")}
                <span className="muted"> sources</span>
              </>
            )}
          </dd>
        </div>
        <div>
          <dt>Inventory synced</dt>
          <dd className="num">
            {scope.inventory_synced_at ? (
              <>
                <time dateTime={scope.inventory_synced_at}>{formatDateTime(scope.inventory_synced_at)}</time>
                {scope.inventory_age_seconds !== null && (
                  <span className="muted"> ({formatAge(scope.inventory_age_seconds)} ago)</span>
                )}
              </>
            ) : (
              <span className="muted">not recorded</span>
            )}
          </dd>
        </div>
        {scope.groups_walked.length > 0 && (
          <div>
            <dt>Groups walked</dt>
            <dd>
              <ul className="pw-def-pins" aria-label="Groups walked">
                {scope.groups_walked.map((g) => (
                  <li key={g.qradar_id}>{g.name}</li>
                ))}
              </ul>
            </dd>
          </div>
        )}
      </dl>

      <div>
        <h3 className="pw-subtitle">Membership changes</h3>
        {scope.changes.length === 0 ? (
          <p className="subtitle">No membership change is stored for this watch.</p>
        ) : (
          <ul className="pw-def-changes" aria-label="Membership changes">
            {scope.changes.map((c) => (
              <li key={c.bucket_start}>
                <time dateTime={c.bucket_start}>{formatDateTime(c.bucket_start)}</time>
                {": "}
                {c.member_count_before === null ? "unknown" : c.member_count_before} to{" "}
                {c.member_count_after === null ? "unknown" : c.member_count_after} sources
                {c.in_grace_window && <span className="muted"> (inside the grace window)</span>}
              </li>
            ))}
          </ul>
        )}
      </div>

      {scope.members.length > 0 && (
        <div>
          <h3 className="pw-subtitle">Member names</h3>
          <p className="subtitle">
            {scope.members_total !== null && scope.members_total > scope.members.length
              ? `${scope.members.length} of ${scope.members_total.toLocaleString("en-US")} are listed, sorted.`
              : `${scope.members.length} listed, sorted.`}
          </p>
          <ul className="pw-def-members" aria-label="Member names" tabIndex={0}>
            {scope.members.map((m) => (
              <li key={m}>{m}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
