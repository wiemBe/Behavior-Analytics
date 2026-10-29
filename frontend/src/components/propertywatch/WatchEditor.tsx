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
import { enumLabel } from "@/i18n/enumLabels";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";

import {
  DETECTORS,
  classifyWatchError,
  createWatchBody,
  draftFromWatch,
  draftReasons,
  detectorCopy,
  emptyWatchDraft,
  pinReason,
  propertyReason,
  updateWatchBody,
  watchSummary,
  type WatchDraft,
} from "./authoring";
import { WatchPropertyPicker, WatchScopePicker } from "./WatchPickers";
import { latencyCadence } from "@/components/cadence/CadenceLatency";

import { formatCount, formatShare } from "@/lib/format";

import { pinAvailability } from "./pwFormat";
import { pwSentence } from "./pwReason";
import { historyHeaderText } from "./history";
import { ENGLISH_PW, type PwI18n } from "./pwText";

import "./propertywatch.css";

const SEVERITIES = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"];
const COLLECTION_SETTING = "PW_COLLECTION_ENABLED";
/** When a "n/unit" limit has room again: within one unit of it, never sooner than a second. */
function retryIn(limit: string, i18n: PwI18n): string {
  const unit = limit.split("/")[1]?.trim().replace(/s$/, "");
  if (unit === "second") return i18n.t("propertyWatch.editor.retrySecond");
  if (unit === "minute") return i18n.t("propertyWatch.editor.retryMinute");
  if (unit === "hour") return i18n.t("propertyWatch.editor.retryHour");
  return i18n.t("propertyWatch.editor.retryLater");
}

const FALLBACK_DETECTORS = Object.keys(DETECTORS) as PwAnomalyTypeName[];

function PreviewResult({
  preview,
  propertyOptions,
  i18n,
}: {
  preview: PwPreview;
  propertyOptions: PwPropertyOptions | null;
  i18n: PwI18n;
}) {
  const t = i18n.t;
  const empty =
    preview.total_events === 0
      ? t("propertyWatch.preview.emptySilence")
      : preview.property_parses === false
        ? t("propertyWatch.preview.emptyNotParsed")
        : preview.outcome === "NOT_MEASURED"
          ? t("propertyWatch.preview.emptyNotMeasured")
          : t("propertyWatch.preview.emptyNoRows");
  const types = propertyOptions?.scope_type_ids.length ?? 0;

  return (
    <div className="pw-preview-content">
      <div className={`banner ${preview.outcome === "MEASURED" ? "" : "warn"}`} role="status">
        <span>
          <strong>{enumLabel(i18n.t, "propertyWatchPreviewOutcome", preview.outcome)}.</strong>{" "}
          {t("propertyWatch.preview.summary", {
            measured: formatCount(preview.buckets_measured, i18n.locale),
            lower: formatCount(preview.buckets_lower_bound, i18n.locale),
            unmeasured: formatCount(preview.buckets_unmeasured, i18n.locale),
            reason: preview.reason
              ? ` ${pwSentence(
                  preview.reason,
                  { code: preview.reason_code, params: preview.reason_params },
                  i18n,
                )}`
              : "",
          })}
        </span>
      </div>
      <p className="subtitle">
        {t("propertyWatch.preview.windowLead")} <UtcTime value={preview.window_start} />{" "}
        {t("propertyWatch.preview.windowTo")} <UtcTime value={preview.window_end} />.
      </p>
      <dl className="pw-preview-facts">
        <div>
          <dt>{t("propertyWatch.preview.totalEvents")}</dt>
          <dd className="num">{formatCount(preview.total_events, i18n.locale)}</dd>
        </div>
        <div>
          <dt>{t("propertyWatch.preview.parsedEvents")}</dt>
          <dd className="num">{formatCount(preview.parsed_events, i18n.locale)}</dd>
        </div>
        <div>
          <dt>{t("propertyWatch.preview.coverage")}</dt>
          <dd className="num">{formatShare(preview.coverage, i18n.locale)}</dd>
        </div>
        <div>
          <dt>{t("propertyWatch.preview.unparsed")}</dt>
          <dd className="num">
            {preview.unparsed_events === null
              ? "—"
              : `${preview.unparsed_bound === "UPPER_BOUND" ? t("propertyWatch.format.atMost", { value: "" }) : ""}${t("propertyWatch.common.events", { count: preview.unparsed_events })}`}
          </dd>
        </div>
      </dl>
      {preview.rows.length === 0 ? (
        <div className="notice" role="status">
          <p><strong>{t("propertyWatch.preview.emptyTitle")}</strong></p>
          <p>{empty}</p>
        </div>
      ) : (
        <div className="table-scroll" role="region" aria-label={t("propertyWatch.preview.tableLabel")} tabIndex={0}>
          <table aria-label={t("propertyWatch.preview.tableLabel")}>
            <thead>
              <tr><th scope="col">{t("propertyWatch.common.value")}</th><th scope="col" className="num">{t("propertyWatch.preview.eventsLastHour")}</th><th scope="col" className="num">{t("propertyWatch.preview.share")}</th></tr>
            </thead>
            <tbody>
              {preview.rows.map((row) => (
                <tr key={row.value_key}>
                  <td className="pw-value-text">{row.components.join(" × ") || row.value_key}</td>
                  <td className="num">{preview.rows_measurement === "LOWER_BOUND" ? t("propertyWatch.format.atLeast", { value: formatCount(row.events, i18n.locale) }) : formatCount(row.events, i18n.locale)}</td>
                  <td className="num">{formatShare(row.share, i18n.locale)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="notice">
        <p>
          <strong>{t("propertyWatch.preview.applicabilityTitle")}</strong>{" "}
          {preview.property_parses === true
            ? t("propertyWatch.preview.parses", { coverage: preview.coverage === null ? t("propertyWatch.preview.unknownShare") : `${formatShare(preview.coverage, i18n.locale)} ${i18n.locale === "en" ? "of" : ""}` })
            : preview.property_parses === false
              ? t("propertyWatch.preview.notParses")
              : t("propertyWatch.preview.cannotDetermine")}
        </p>
        <p>
          {t("propertyWatch.preview.catalogTypes", { count: types })}
        </p>
      </div>
      <div className="pw-query-list">
        {preview.queries.map((query) => (
          <CodePanel
            key={query.search}
            label={`${t(query.search === "GROUPED" ? "propertyWatch.preview.grouped" : "propertyWatch.preview.totals")} · ${t("propertyWatch.preview.generatedAql")}`}
            code={query.text}
            meta={t("propertyWatch.common.readOnly")}
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
  i18n: providedI18n,
}: {
  catalog: PropertyWatchCatalog;
  mode: "create" | "edit";
  watch?: PropertyWatch;
  initialScope?: WatchScope | null;
  i18n?: PwI18n;
}) {
  const runtimeI18n = useBehaviorI18n();
  const i18n = providedI18n ?? runtimeI18n ?? ENGLISH_PW;
  const t = i18n.t;
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
    const fromCatalog = catalog.detectors.filter((d): d is PwAnomalyTypeName => Object.hasOwn(DETECTORS, d));
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
    const reasons = draftReasons(draft, catalog, i18n);
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
      setMessage({ tone: "", text: t("propertyWatch.editor.saved") });
      if (mode === "edit") router.refresh();
    } catch (error) {
      const refusal = classifyWatchError(error, i18n);
      if (refusal.kind === "reasons") setServerReasons(refusal.reasons);
      else if (refusal.kind === "version") {
        setConflict({ currentVersion: refusal.currentVersion, updatedBy: refusal.updatedBy });
      } else if (refusal.kind === "rate") {
        setMessage({
          tone: "warn",
          text: t("propertyWatch.editor.rate", {
            limit: catalog.preview_rate_limit,
            retry: retryIn(catalog.preview_rate_limit, i18n),
          }),
        });
      } else if (refusal.kind === "dependencies") {
        setMessage({
          tone: "crit",
          text: (
            <>
              {t("propertyWatch.actions.dependencies", { message: refusal.message })}
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
      ? t("propertyWatch.editor.historyLocked", { history: historyHeaderText(currentWatch.history, i18n) })
      : !catalog.history.enabled
        ? t("propertyWatch.editor.historyOff")
        : historySearchesAvailable < historyNeed
          ? catalog.history.searches_per_tick <= catalog.history.searches_free
            ? t("propertyWatch.editor.historyServerLimit", {
                need: formatCount(historyNeed, i18n.locale),
                available: formatCount(catalog.history.searches_per_tick, i18n.locale),
              })
            : t("propertyWatch.editor.historyFree", {
                need: formatCount(historyNeed, i18n.locale),
                free: formatCount(catalog.history.searches_free, i18n.locale),
                verb: t(catalog.history.searches_free === 1 ? "propertyWatch.editor.is" : "propertyWatch.editor.are"),
              })
          : null;
  // A disabled section never asks for history: the form shows, and sends, what will happen.
  const shown: WatchDraft =
    historyDisabledReason !== null && !historyLocked
      ? { ...draft, historyDays: 0, historyTouched: catalog.history.enabled || draft.historyTouched }
      : draft;
  const summary = watchSummary(shown, i18n);
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
              {t("propertyWatch.editor.enabledWarning")}
            </span>
          </div>
        )}
        <div className="pw-field">
          <label htmlFor={nameId}>{t("propertyWatch.editor.name")}</label>
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
          <label htmlFor={descriptionId}>{t("propertyWatch.editor.description")}</label>
          <textarea
            id={descriptionId}
            rows={3}
            maxLength={2000}
            value={draft.description}
            onChange={(event) => patch({ description: event.target.value })}
          />
        </div>

        <fieldset className="pw-editor-section">
          <legend>{t("propertyWatch.editor.scopeLegend")}</legend>
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
                  ? t("propertyWatch.editor.memberUnknown")
                  : t("propertyWatch.editor.resolves", { count: formatCount(draft.scopeCount, i18n.locale) })}
              </p>
              {(scopeOption?.detail || watch?.scope.detail) && (
                <p>{t("propertyWatch.editor.inventoryPath", { path: scopeOption?.detail ?? watch?.scope.detail ?? "" })}</p>
              )}
              {scope?.groups_walked.length ? (
                <p>
                  {t("propertyWatch.editor.groupsWalked", { groups: scope.groups_walked.map((group) => group.name).join(" → ") })}
                </p>
              ) : draft.scopeKind === "LOG_SOURCE_GROUP" && currentWatch === null ? (
                <p>{t("propertyWatch.editor.walkAfterSave")}</p>
              ) : null}
            </div>
          )}
        </fieldset>

        <fieldset className="pw-editor-section">
          <legend>{t("propertyWatch.editor.propertiesLegend")}</legend>
          <p className="field-hint">
            {t("propertyWatch.editor.propertiesHelp")}
          </p>
          {propertyState === "loading" && <p className="loading-state" role="status">{t("propertyWatch.editor.propertyLoading")}</p>}
          {propertyState === "error" && (
            <p className="field-error" role="alert">
              {t("propertyWatch.editor.propertyFailed")}
            </p>
          )}
          {propertyOptions?.status === "UNAVAILABLE" && (
            <div className="notice" role="status">
              <p><strong>{t("propertyWatch.editor.catalogUnavailableTitle")}</strong></p>
              <p>{propertyOptions.detail ?? t("propertyWatch.editor.catalogUnavailableFallback")}</p>
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
              {propertyReason(property, i18n) && <p className="field-error" role="alert">{propertyReason(property, i18n)}</p>}
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
                  {t("propertyWatch.editor.removeProperty", { index: formatCount(index + 1, i18n.locale) })}
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
              {t("propertyWatch.editor.addProperty")}
            </button>
          )}
        </fieldset>

        <fieldset className="pw-editor-section">
          <legend>{t("propertyWatch.editor.valuesLegend")}</legend>
          <p className="pw-section-lead">{t("propertyWatch.editor.alwaysCounted")}</p>
          {draft.pins.length === 0 ? (
            <p className="field-hint">{t("propertyWatch.editor.noPins")}</p>
          ) : (
            <div className="pw-pins">
              {draft.pins.map((pin, row) => (
                <div className="pw-pin-row" key={row}>
                  <div className="pw-pin-fields">
                    {draft.properties.map((property, column) => {
                      const id = `${nameId}-pin-${row}-${column}`;
                      const reason = pinReason(pin[column] ?? "", catalog.value_max_length, i18n);
                      return (
                        <div className="pw-field" key={column}>
                          <label htmlFor={id}>{property.name || `${t("propertyWatch.common.property")} ${formatCount(column + 1, i18n.locale)}`}</label>
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
                    {t("propertyWatch.editor.removePin", { index: formatCount(row + 1, i18n.locale) })}
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
            {t("propertyWatch.editor.addPin")}
          </button>
          <p className="field-hint">
            {t("propertyWatch.editor.pinsHelp", {
              maxPins: formatCount(catalog.max_pinned_values, i18n.locale),
              newValues: formatCount(catalog.max_new_values_per_bucket, i18n.locale),
              activeValues: formatCount(catalog.max_active_values_per_watch, i18n.locale),
            })}
          </p>
        </fieldset>

        <fieldset className="pw-editor-section">
          <legend>{t("propertyWatch.editor.detectionLegend")}</legend>
          <div className="pw-detectors">
            {detectors.map((detector) => {
              const combinationBlocked = detector === "PW_NEW_COMBINATION" && draft.properties.length !== 2;
              const copy = detectorCopy(detector, i18n);
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
                    <strong>{copy.label}</strong>
                    <span>{copy.claim} {copy.learning}</span>
                  </span>
                </label>
              );
            })}
          </div>
          <div className="pw-field pw-severity-field">
            <label htmlFor={severityId}>{t("propertyWatch.editor.incidentSeverity")}</label>
            <select
              id={severityId}
              className="select-control"
              value={draft.severity}
              onChange={(event) => patch({ severity: event.target.value })}
            >
              {SEVERITIES.map((severity) => <option key={severity}>{i18n.locale === "en" ? severity : enumLabel(i18n.t, "severity", severity)}</option>)}
            </select>
          </div>
          <p className="field-hint">
            {t("propertyWatch.editor.thresholdHelp", {
              link: t("propertyWatch.editor.customRules"),
              availability: pinAvailability(latencyCadence(watch?.cadence ?? catalog.cadence), i18n),
            }).split(t("propertyWatch.editor.customRules"))[0]}
            <Link href="/custom-rules/new">{t("propertyWatch.editor.customRules")}</Link>
            {t("propertyWatch.editor.thresholdHelp", {
              link: t("propertyWatch.editor.customRules"),
              availability: pinAvailability(latencyCadence(watch?.cadence ?? catalog.cadence), i18n),
            }).split(t("propertyWatch.editor.customRules"))[1]}
          </p>
        </fieldset>

        <fieldset className="pw-editor-section">
          <legend>{t("propertyWatch.editor.historyLegend")}</legend>
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
              <strong>{t("propertyWatch.editor.readHistoryTitle")}</strong>
              <span>{t("propertyWatch.editor.readHistoryBody")}</span>
            </span>
          </label>
          {historyDisabledReason && (
            <p className="field-hint" role="status">{historyDisabledReason}</p>
          )}
          <div className="pw-field">
            <label htmlFor={historyDaysId}>{t("propertyWatch.editor.historyWindow")}</label>
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
                <option key={days} value={days}>{t("propertyWatch.common.days", { count: days })}</option>
              ))}
            </select>
          </div>
          {currentWatch?.history.estimate && shown.historyDays > 0 && (
            <p className="field-hint">
              {t("propertyWatch.editor.estimate", {
                searchMin: formatCount(currentWatch.history.estimate.searches_min, i18n.locale),
                searchMax: formatCount(currentWatch.history.estimate.searches_max, i18n.locale),
                tickMin: formatCount(currentWatch.history.estimate.ticks_min, i18n.locale),
                tickMax: formatCount(currentWatch.history.estimate.ticks_max, i18n.locale),
              })}
            </p>
          )}
          <ul className="pw-history-limits">
            <li>{t("propertyWatch.editor.historyLimitParsed")}</li>
            <li>{t("propertyWatch.editor.historyLimitMembership")}</li>
            <li>{t("propertyWatch.editor.historyLimitJudged")}</li>
          </ul>
        </fieldset>

        <p className="pw-editor-summary">{summary}</p>
        {localReasons.length > 0 && (
          <ul className="pw-reasons" aria-label={t("propertyWatch.editor.localReasons")} role="alert">
            {localReasons.map((reason) => <li key={reason}>{reason}</li>)}
          </ul>
        )}
        {serverReasons.length > 0 && (
          <ul className="pw-reasons" aria-label={t("propertyWatch.editor.serverReasons")} role="alert">
            {serverReasons.map((reason, index) => <li key={`${index}-${reason}`}>{reason}</li>)}
          </ul>
        )}
        {conflict && (
          <div className="banner warn" role="alert">
            <span>
              {t("propertyWatch.editor.conflict", {
                operator: conflict.updatedBy ?? t("propertyWatch.editor.anotherOperator"),
                version: conflict.currentVersion,
              })}
              <button type="button" className="action secondary pw-inline-action" onClick={() => router.refresh()}>
                {t("propertyWatch.editor.reload")}
              </button>
            </span>
          </div>
        )}
        {message && <div className={`banner ${message.tone}`} role={message.tone ? "alert" : "status"}><span>{message.text}</span></div>}
        <div className="actions">
          <button type="submit" className="action" disabled={saving}>
            {t(saving ? "propertyWatch.editor.saving" : "propertyWatch.editor.save")}
          </button>
        </div>
      </form>

      <aside className="pw-editor-preview panel" aria-labelledby={`${nameId}-preview-title`}>
        <div className="pw-preview-head">
          <h2 id={`${nameId}-preview-title`}>{t("propertyWatch.editor.previewHeading")}</h2>
          <p>
            {t("propertyWatch.editor.previewIntro", { searches: formatCount(catalog.preview_searches, i18n.locale) })}
          </p>
        </div>
        {!catalog.collection_enabled && (
          <div className="banner warn" role="status">
            <span>
              <strong>{t("propertyWatch.editor.collectionOffTitle")}</strong>{" "}
              <code>{COLLECTION_SETTING}</code>{t("propertyWatch.editor.collectionOffAfter")}
            </span>
          </div>
        )}
        {preview ? (
          <PreviewResult preview={preview} propertyOptions={propertyOptions} i18n={i18n} />
        ) : (
          <div className="notice" role="status">
            <p><strong>{t("propertyWatch.editor.previewEmptyTitle")}</strong></p>
            <p>{t("propertyWatch.editor.previewEmptyBody")}</p>
          </div>
        )}
      </aside>
    </div>
  );
}
