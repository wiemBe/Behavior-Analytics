"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useId, useMemo, useState } from "react";

import { CodePanel } from "@/components/ui/CodePanel";
import {
  api,
  type PropertyWatch,
  type PropertyWatchCatalog,
  type PwAnomalyTypeName,
  type PwPreview,
  type PwPropertyOptions,
  type PwScopeOption,
  type WatchScope,
} from "@/lib/api";
import { UtcTime } from "@/components/ui/UtcTime";

import {
  DETECTORS,
  classifyWatchError,
  createWatchBody,
  draftFromWatch,
  draftReasons,
  emptyWatchDraft,
  pinReason,
  propertyReason,
  updateWatchBody,
  watchSummary,
  type WatchDraft,
} from "./authoring";
import { WatchPropertyPicker, WatchScopePicker } from "./WatchPickers";
import { latencyCadence } from "@/components/cadence/CadenceLatency";

import { pinAvailability } from "./pwFormat";
import { historyHeaderText } from "./history";

import "./propertywatch.css";

const SEVERITIES = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"];
/** When a "n/unit" limit has room again: within one unit of it, never sooner than a second. */
function retryIn(limit: string): string {
  const unit = limit.split("/")[1]?.trim().replace(/s$/, "");
  if (unit === "second") return "about a second";
  if (unit === "minute") return "about a minute";
  if (unit === "hour") return "up to an hour";
  return "a little while";
}

const FALLBACK_DETECTORS = Object.keys(DETECTORS) as PwAnomalyTypeName[];

function PreviewResult({
  preview,
  propertyOptions,
}: {
  preview: PwPreview;
  propertyOptions: PwPropertyOptions | null;
}) {
  const empty =
    preview.total_events === 0
      ? "The scope sent no events in the last closed hour. That is measured silence, so there are no values to list."
      : preview.property_parses === false
        ? "Events arrived, but the selected property parsed on none of them. No value was measured."
        : preview.outcome === "NOT_MEASURED"
          ? "The preview measured no bucket. This is not a zero or a healthy result."
          : "No value was returned in the measured preview window.";
  const types = propertyOptions?.scope_type_ids.length ?? 0;

  return (
    <div className="pw-preview-content">
      <div className={`banner ${preview.outcome === "MEASURED" ? "" : "warn"}`} role="status">
        <span>
          <strong>{preview.outcome.replaceAll("_", " ")}.</strong>{" "}
          {preview.buckets_measured} measured, {preview.buckets_lower_bound} lower-bound and{" "}
          {preview.buckets_unmeasured} unmeasured 5-minute buckets.
          {preview.reason ? ` ${preview.reason}` : ""}
        </span>
      </div>
      <p className="subtitle">
        Last closed hour: <UtcTime value={preview.window_start} />{" "}
        to <UtcTime value={preview.window_end} />.
      </p>
      <dl className="pw-preview-facts">
        <div>
          <dt>Total events</dt>
          <dd className="num">{preview.total_events?.toLocaleString("en-US") ?? "—"}</dd>
        </div>
        <div>
          <dt>Parsed events</dt>
          <dd className="num">{preview.parsed_events?.toLocaleString("en-US") ?? "—"}</dd>
        </div>
        <div>
          <dt>Coverage</dt>
          <dd className="num">{preview.coverage === null ? "—" : `${(preview.coverage * 100).toFixed(1)} %`}</dd>
        </div>
        <div>
          <dt>Unparsed remainder</dt>
          <dd className="num">
            {preview.unparsed_events === null
              ? "—"
              : `${preview.unparsed_bound === "UPPER_BOUND" ? "at most " : ""}${preview.unparsed_events.toLocaleString("en-US")} events`}
          </dd>
        </div>
      </dl>
      {preview.rows.length === 0 ? (
        <div className="notice" role="status">
          <p><strong>No preview value is available.</strong></p>
          <p>{empty}</p>
        </div>
      ) : (
        <div className="table-scroll" role="region" aria-label="Preview values" tabIndex={0}>
          <table aria-label="Preview values">
            <thead>
              <tr><th scope="col">Value</th><th scope="col" className="num">Events in last hour</th><th scope="col" className="num">Share of parsed</th></tr>
            </thead>
            <tbody>
              {preview.rows.map((row) => (
                <tr key={row.value_key}>
                  <td className="pw-value-text">{row.components.join(" × ") || row.value_key}</td>
                  <td className="num">{preview.rows_measurement === "LOWER_BOUND" ? "at least " : ""}{row.events.toLocaleString("en-US")}</td>
                  <td className="num">{row.share === null ? "—" : `${(row.share * 100).toFixed(1)} %`}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="notice">
        <p>
          <strong>Applicability reading.</strong>{" "}
          {preview.property_parses === true
            ? `The selected property set parsed on ${preview.coverage === null ? "an unknown share of" : `${(preview.coverage * 100).toFixed(1)} % of`} events.`
            : preview.property_parses === false
              ? "The selected property set parsed on no event although the scope sent events."
              : "The preview cannot determine whether the property parses on this scope."}
        </p>
        <p>
          The catalog consulted {types} {types === 1 ? "type" : "types"} in this scope. It is a
          suggestion; this real preview is the applicability check.
        </p>
      </div>
      <div className="pw-query-list">
        {preview.queries.map((query) => (
          <CodePanel
            key={query.search}
            label={`${query.search === "GROUPED" ? "Value counts" : "Scope total"} · generated AQL`}
            code={query.text}
            meta="Read-only"
          />
        ))}
      </div>
    </div>
  );
}

export function WatchEditor({
  catalog,
  mode,
  watch,
  initialScope = null,
}: {
  catalog: PropertyWatchCatalog;
  mode: "create" | "edit";
  watch?: PropertyWatch;
  initialScope?: WatchScope | null;
}) {
  const router = useRouter();
  const nameId = useId();
  const descriptionId = useId();
  const severityId = useId();
  const historyId = useId();
  const historyDaysId = useId();
  const [draft, setDraft] = useState<WatchDraft>(() =>
    watch ? draftFromWatch(watch) : emptyWatchDraft(catalog),
  );
  const [currentWatch, setCurrentWatch] = useState<PropertyWatch | null>(watch ?? null);
  const [scope, setScope] = useState<WatchScope | null>(initialScope);
  const [scopeOption, setScopeOption] = useState<PwScopeOption | null>(null);
  const [propertyOptions, setPropertyOptions] = useState<PwPropertyOptions | null>(null);
  const [propertyState, setPropertyState] = useState<"idle" | "loading" | "ready" | "error">(
    draft.scopeId ? "loading" : "idle",
  );
  const [preview, setPreview] = useState<PwPreview | null>(null);
  const [saving, setSaving] = useState(false);
  const [localReasons, setLocalReasons] = useState<string[]>([]);
  const [serverReasons, setServerReasons] = useState<string[]>([]);
  const [message, setMessage] = useState<{
    tone: "crit" | "warn" | "";
    text: React.ReactNode;
  } | null>(null);
  const [conflict, setConflict] = useState<{ currentVersion: string; updatedBy: string | null } | null>(null);

  useEffect(() => {
    if (!draft.scopeId) {
      setPropertyOptions(null);
      setPropertyState("idle");
      return;
    }
    const controller = new AbortController();
    setPropertyState("loading");
    api
      .propertyWatchPropertyOptions(draft.scopeKind, draft.scopeId, "", controller.signal)
      .then((options) => {
        setPropertyOptions(options);
        setPropertyState("ready");
      })
      .catch(() => {
        if (!controller.signal.aborted) setPropertyState("error");
      });
    return () => controller.abort();
  }, [draft.scopeId, draft.scopeKind]);

  const detectors = useMemo(() => {
    const fromCatalog = catalog.detectors.filter((d): d is PwAnomalyTypeName => d in DETECTORS);
    return fromCatalog.length > 0 ? fromCatalog : FALLBACK_DETECTORS;
  }, [catalog.detectors]);

  function patch(next: Partial<WatchDraft>) {
    setDraft((current) => ({ ...current, ...next }));
    setPreview(null);
    setLocalReasons([]);
    setServerReasons([]);
    setMessage(null);
    setConflict(null);
  }

  function changeProperty(index: number, property: WatchDraft["properties"][number]) {
    const properties = draft.properties.map((current, i) => (i === index ? property : current));
    const pins = draft.pins.map((pin) =>
      Array.from({ length: properties.length }, (_, i) => pin[i] ?? ""),
    );
    patch({ properties, pins });
  }

  function setPin(row: number, column: number, value: string) {
    const pins = draft.pins.map((pin, pinIndex) =>
      pinIndex === row ? pin.map((part, partIndex) => (partIndex === column ? value : part)) : pin,
    );
    patch({ pins });
  }

  async function saveAndPreview() {
    if (saving) return;
    const reasons = draftReasons(draft, catalog);
    setLocalReasons(reasons);
    setServerReasons([]);
    setMessage(null);
    setConflict(null);
    if (reasons.length > 0) return;
    setSaving(true);
    try {
      const saved =
        currentWatch === null
          ? await api.createPropertyWatch(createWatchBody(shown))
          : await api.updatePropertyWatch(currentWatch.id, updateWatchBody(shown, currentWatch));
      setCurrentWatch(saved);
      const [result, resolvedScope] = await Promise.all([
        api.previewPropertyWatch(saved.id),
        api.propertyWatchScope(saved.id).catch(() => null),
      ]);
      setPreview(result);
      setScope(resolvedScope);
      setMessage({ tone: "", text: "The watch was saved disabled and the stored definition was previewed." });
      if (mode === "edit") router.refresh();
    } catch (error) {
      const refusal = classifyWatchError(error);
      if (refusal.kind === "reasons") setServerReasons(refusal.reasons);
      else if (refusal.kind === "version") {
        setConflict({ currentVersion: refusal.currentVersion, updatedBy: refusal.updatedBy });
      } else if (refusal.kind === "rate") {
        setMessage({
          tone: "warn",
          text: `The preview is rate limited to ${catalog.preview_rate_limit}. Try again in ${retryIn(catalog.preview_rate_limit)}. The watch was saved disabled and the preview did not run.`,
        });
      } else if (refusal.kind === "dependencies") {
        setMessage({
          tone: "crit",
          text: (
            <>
              {refusal.message}. Rules to detach first:{" "}
              {refusal.rules.map((rule, index) => (
                <span key={rule.id}>
                  {index > 0 ? ", " : ""}
                  <Link href={`/custom-rules/${rule.id}`}>{rule.name}</Link>
                </span>
              ))}
              .
            </>
          ),
        });
      } else setMessage({ tone: refusal.kind === "conflict" ? "warn" : "crit", text: refusal.detail });
    } finally {
      setSaving(false);
    }
  }

  const historyNeed = draft.pins.length > 0 ? 3 : 2;
  const historyLocked =
    currentWatch !== null &&
    (currentWatch.history.state !== "NOT_REQUESTED" ||
      currentWatch.coverage !== null ||
      currentWatch.collected_through !== null ||
      currentWatch.evaluated_through !== null);
  const historySearchesAvailable = Math.min(
    catalog.history.searches_per_tick,
    catalog.history.searches_free,
  );
  const historyDisabledReason =
    historyLocked && currentWatch
      ? `${historyHeaderText(currentWatch.history)}. The request cannot change after history or collected buckets exist.`
      : !catalog.history.enabled
        ? "History reading is switched off on this server."
        : historySearchesAvailable < historyNeed
          ? catalog.history.searches_per_tick <= catalog.history.searches_free
            ? `History needs ${historyNeed} searches per tick for this watch; this server gives a history reading at most ${catalog.history.searches_per_tick} (PW_BOOTSTRAP_SEARCHES_PER_TICK).`
            : `History needs ${historyNeed} free searches per tick for this watch; ${catalog.history.searches_free} ${catalog.history.searches_free === 1 ? "is" : "are"} free.`
          : null;
  // A disabled section never asks for history: the form shows, and sends, what will happen.
  const shown: WatchDraft =
    historyDisabledReason !== null && !historyLocked
      ? { ...draft, historyDays: 0, historyTouched: catalog.history.enabled || draft.historyTouched }
      : draft;
  const summary = watchSummary(shown);
  const historyOptions = [...new Set([7, 14, 28, catalog.history.days_default])]
    .filter((days) => days >= catalog.history.days_min && days <= catalog.history.days_max)
    .sort((a, b) => a - b);

  return (
    <div className="pw-editor-layout">
      <form
        className="pw-editor panel"
        onSubmit={(event) => {
          event.preventDefault();
          void saveAndPreview();
        }}
      >
        {currentWatch?.enabled && (
          <div className="banner warn" role="note">
            <span>
              This watch is enabled. Saving here disables it until you enable it again from the
              actions above.
            </span>
          </div>
        )}
        <div className="pw-field">
          <label htmlFor={nameId}>Watch name</label>
          <input
            id={nameId}
            className="field"
            maxLength={80}
            autoComplete="off"
            value={draft.name}
            onChange={(event) => patch({ name: event.target.value })}
          />
        </div>
        <div className="pw-field">
          <label htmlFor={descriptionId}>Description</label>
          <textarea
            id={descriptionId}
            rows={3}
            maxLength={2000}
            value={draft.description}
            onChange={(event) => patch({ description: event.target.value })}
          />
        </div>

        <fieldset className="pw-editor-section">
          <legend>1. Scope</legend>
          <WatchScopePicker
            kind={draft.scopeKind}
            selectedId={draft.scopeId}
            onKind={(scopeKind) => patch({ scopeKind, scopeId: "", scopeLabel: "", scopeCount: null })}
            onSelect={(option) => {
              setScopeOption(option);
              patch({
                scopeId: option?.id ?? "",
                scopeLabel: option?.name ?? "",
                scopeCount: option?.synced_source_count ?? null,
              });
            }}
          />
          {(draft.scopeId || scopeOption) && (
            <div className="notice pw-scope-reading">
              <p>
                <strong>{draft.scopeLabel || draft.scopeId}</strong>{" "}
                {draft.scopeCount === null
                  ? "has no reliable resolved-member count."
                  : `resolves to ${draft.scopeCount.toLocaleString("en-US")} sources.`}
              </p>
              {(scopeOption?.detail || watch?.scope.detail) && (
                <p>Inventory path: {scopeOption?.detail ?? watch?.scope.detail}</p>
              )}
              {scope?.groups_walked.length ? (
                <p>
                  Groups walked: {scope.groups_walked.map((group) => group.name).join(" → ")}.
                </p>
              ) : draft.scopeKind === "LOG_SOURCE_GROUP" && currentWatch === null ? (
                <p>The exact descendant walk is available after the required disabled save.</p>
              ) : null}
            </div>
          )}
        </fieldset>

        <fieldset className="pw-editor-section">
          <legend>2. Properties</legend>
          <p className="field-hint">
            Choose one or two properties. Two is the limit because each additional property
            multiplies the combination space and can prevent a reliable baseline from learning.
          </p>
          {propertyState === "loading" && <p className="loading-state" role="status">Loading property options…</p>}
          {propertyState === "error" && (
            <p className="field-error" role="alert">
              Property options could not be loaded. You may type a valid property name, but the
              catalog applicability reading is unavailable until this read succeeds.
            </p>
          )}
          {propertyOptions?.status === "UNAVAILABLE" && (
            <div className="notice" role="status">
              <p><strong>The custom-property catalog is unavailable.</strong></p>
              <p>{propertyOptions.detail ?? "Typed names remain suggestions until the real preview runs."}</p>
            </div>
          )}
          {draft.properties.map((property, index) => (
            <div key={index} className="pw-property-row">
              <WatchPropertyPicker
                index={index}
                value={property}
                options={propertyOptions}
                disabled={!draft.scopeId}
                onChange={(next) => changeProperty(index, next)}
              />
              {propertyReason(property) && <p className="field-error" role="alert">{propertyReason(property)}</p>}
              {draft.properties.length > 1 && (
                <button
                  type="button"
                  className="action secondary"
                  onClick={() => {
                    const properties = draft.properties.filter((_, i) => i !== index);
                    patch({
                      properties,
                      pins: draft.pins.map((pin) => pin.filter((_, i) => i !== index)),
                      detectors: draft.detectors.filter((d) => d !== "PW_NEW_COMBINATION"),
                    });
                  }}
                >
                  Remove property {index + 1}
                </button>
              )}
            </div>
          ))}
          {draft.properties.length < catalog.max_properties && (
            <button
              type="button"
              className="action secondary"
              onClick={() =>
                patch({
                  properties: [...draft.properties, { kind: "NORMALIZED", name: "username" }],
                  pins: draft.pins.map((pin) => [...pin, ""]),
                })
              }
            >
              Add second property
            </button>
          )}
        </fieldset>

        <fieldset className="pw-editor-section">
          <legend>3. Values</legend>
          <p className="pw-section-lead">Always counted</p>
          {draft.pins.length === 0 ? (
            <p className="field-hint">No value is pinned. Automatic values can be pinned later.</p>
          ) : (
            <div className="pw-pins">
              {draft.pins.map((pin, row) => (
                <div className="pw-pin-row" key={row}>
                  <div className="pw-pin-fields">
                    {draft.properties.map((property, column) => {
                      const id = `${nameId}-pin-${row}-${column}`;
                      const reason = pinReason(pin[column] ?? "", catalog.value_max_length);
                      return (
                        <div className="pw-field" key={column}>
                          <label htmlFor={id}>{property.name || `Property ${column + 1}`}</label>
                          <input
                            id={id}
                            className="field pw-mono-input"
                            maxLength={catalog.value_max_length}
                            value={pin[column] ?? ""}
                            aria-invalid={reason ? true : undefined}
                            onChange={(event) => setPin(row, column, event.target.value)}
                          />
                          {reason && <p className="field-error" role="alert">{reason}</p>}
                        </div>
                      );
                    })}
                  </div>
                  <button
                    type="button"
                    className="action secondary"
                    onClick={() => patch({ pins: draft.pins.filter((_, i) => i !== row) })}
                  >
                    Remove pinned value {row + 1}
                  </button>
                </div>
              ))}
            </div>
          )}
          <button
            type="button"
            className="action secondary"
            disabled={draft.pins.length >= catalog.max_pinned_values}
            onClick={() =>
              patch({ pins: [...draft.pins, Array.from({ length: draft.properties.length }, () => "")] })
            }
          >
            Add pinned value
          </button>
          <p className="field-hint">
            At most {catalog.max_pinned_values} values or full pairs may be pinned. Quotes and
            backslashes cannot be pinned. The most frequent other values are tracked automatically:
            up to {catalog.max_new_values_per_bucket} new values per 5 minutes and{" "}
            {catalog.max_active_values_per_watch} in total.
          </p>
        </fieldset>

        <fieldset className="pw-editor-section">
          <legend>4. Detection</legend>
          <div className="pw-detectors">
            {detectors.map((detector) => {
              const combinationBlocked = detector === "PW_NEW_COMBINATION" && draft.properties.length !== 2;
              return (
                <label key={detector} className="pw-detector-option">
                  <input
                    type="checkbox"
                    checked={draft.detectors.includes(detector)}
                    disabled={combinationBlocked}
                    onChange={(event) =>
                      patch({
                        detectors: event.target.checked
                          ? [...draft.detectors, detector]
                          : draft.detectors.filter((item) => item !== detector),
                      })
                    }
                  />
                  <span>
                    <strong>{DETECTORS[detector].label}</strong>
                    <span>{DETECTORS[detector].claim} {DETECTORS[detector].learning}</span>
                  </span>
                </label>
              );
            })}
          </div>
          <div className="pw-field pw-severity-field">
            <label htmlFor={severityId}>Incident severity</label>
            <select
              id={severityId}
              className="select-control"
              value={draft.severity}
              onChange={(event) => patch({ severity: event.target.value })}
            >
              {SEVERITIES.map((severity) => <option key={severity}>{severity}</option>)}
            </select>
          </div>
          <p className="field-hint">
            Threshold alerts are <Link href="/custom-rules/new">custom rules</Link> on a pinned
            value. A new pin can be used in a rule {pinAvailability(latencyCadence(watch?.cadence ?? catalog.cadence))}.
          </p>
        </fieldset>

        <fieldset className="pw-editor-section">
          <legend>5. History</legend>
          <label className="pw-detector-option" htmlFor={historyId}>
            <input
              id={historyId}
              type="checkbox"
              checked={shown.historyDays > 0}
              disabled={historyDisabledReason !== null}
              onChange={(event) =>
                patch({
                  historyDays: event.target.checked ? catalog.history.days_default : 0,
                  historyTouched: true,
                })
              }
            />
            <span>
              <strong>Read history from QRadar (used only for learning)</strong>
              <span>Reads QRadar&apos;s past once before collection begins.</span>
            </span>
          </label>
          {historyDisabledReason && (
            <p className="field-hint" role="status">{historyDisabledReason}</p>
          )}
          <div className="pw-field">
            <label htmlFor={historyDaysId}>History window</label>
            <select
              id={historyDaysId}
              className="select-control"
              value={shown.historyDays || draft.historyDays || catalog.history.days_default}
              disabled={shown.historyDays === 0 || historyDisabledReason !== null}
              onChange={(event) =>
                patch({ historyDays: Number(event.target.value), historyTouched: true })
              }
            >
              {historyOptions.map((days) => (
                <option key={days} value={days}>{days} days</option>
              ))}
            </select>
          </div>
          {currentWatch?.history.estimate && shown.historyDays > 0 && (
            <p className="field-hint">
              Estimate: about {currentWatch.history.estimate.searches_min} to{" "}
              {currentWatch.history.estimate.searches_max} searches,{" "}
              {currentWatch.history.estimate.ticks_min} to {currentWatch.history.estimate.ticks_max}{" "}
              collection runs. This is an estimate, not a completion promise.
            </p>
          )}
          <ul className="pw-history-limits">
            <li>Events whose property was not parsed then are not counted as history.</li>
            <li>The membership of a group is today&apos;s, not as it was.</li>
            <li>Nothing is judged until the reading finishes.</li>
          </ul>
        </fieldset>

        <p className="pw-editor-summary">{summary}</p>
        {localReasons.length > 0 && (
          <ul className="pw-reasons" aria-label="Form validation reasons" role="alert">
            {localReasons.map((reason) => <li key={reason}>{reason}</li>)}
          </ul>
        )}
        {serverReasons.length > 0 && (
          <ul className="pw-reasons" aria-label="Server validation reasons" role="alert">
            {serverReasons.map((reason, index) => <li key={`${index}-${reason}`}>{reason}</li>)}
          </ul>
        )}
        {conflict && (
          <div className="banner warn" role="alert">
            <span>
              This watch was changed by {conflict.updatedBy ?? "another operator"} at{" "}
              {conflict.currentVersion}. Your draft was not written.
              <button type="button" className="action secondary pw-inline-action" onClick={() => router.refresh()}>
                Reload the current watch
              </button>
            </span>
          </div>
        )}
        {message && <div className={`banner ${message.tone}`} role={message.tone ? "alert" : "status"}><span>{message.text}</span></div>}
        <div className="actions">
          <button type="submit" className="action" disabled={saving}>
            {saving ? "Saving and previewing…" : "Save as disabled and preview"}
          </button>
        </div>
      </form>

      <aside className="pw-editor-preview panel" aria-labelledby={`${nameId}-preview-title`}>
        <div className="pw-preview-head">
          <h2 id={`${nameId}-preview-title`}>6. Preview</h2>
          <p>
            Saves this definition disabled, then spends {catalog.preview_searches} bounded searches
            over the last closed hour. Nothing is admitted or alerted.
          </p>
        </div>
        {!catalog.collection_enabled && (
          <div className="banner warn" role="status">
            <span>
              <strong>COLLECTION OFF.</strong> <code>PW_COLLECTION_ENABLED</code> is false. A preview
              may still run explicitly, but enabling will not collect buckets until the family switch is on.
            </span>
          </div>
        )}
        {preview ? (
          <PreviewResult preview={preview} propertyOptions={propertyOptions} />
        ) : (
          <div className="notice" role="status">
            <p><strong>No preview has run for this draft.</strong></p>
            <p>
              Complete the form, then save it disabled and preview the stored definition. The
              preview will distinguish no events, no parsing and an unmeasured search.
            </p>
          </div>
        )}
      </aside>
    </div>
  );
}
