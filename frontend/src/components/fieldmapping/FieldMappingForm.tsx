"use client";

// The mapping editor: one row per field the schema declares.
//
// Driven entirely by `MappingSchemaOut` and knows nothing about WAF; the IPS
// screens render the same component with their own schema. The component is
// controlled. It never saves: the owner decides what a change means.

import { useId, useMemo, useState } from "react";
import { useTranslations } from "next-intl";

import { PropertyInput } from "@/components/fieldmapping/PropertyInput";
import type { CatalogProperty, MappingFieldOut, MappingSchemaOut, PropertyCatalogOut } from "@/lib/api";
import { catalogNote, draftErrors, type MappingDraft } from "@/lib/fieldMapping";
import { UtcTime } from "@/components/ui/UtcTime";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";

import "@/components/ui/controls.css";

export interface CopyOption {
  id: string;
  label: string;
}

// A field whose schema offers more than one reference kind (IPS: a normalized
// QRadar field or a custom property) gets a switch. The kind is the owner's
// state, passed in as `referenceKinds`; a form without it, like the WAF one,
// renders exactly as before.
const KIND_LABEL: Record<string, string> = {
  NORMALIZED: "QRadar field",
  CUSTOM_PROPERTY: "Custom property",
};

export function referenceKindLabel(
  kind: string,
  i18n?: { t: (key: string) => string; locale: string },
): string {
  if (i18n && Object.prototype.hasOwnProperty.call(KIND_LABEL, kind)) {
    return i18n.t(`fieldMapping.form.kind${kind}`);
  }
  return KIND_LABEL[kind] ?? kind;
}

function normalizedFor(field: MappingFieldOut): string[] {
  return field.kinds.includes("NORMALIZED") ? field.normalized_options : [];
}

function propertiesFor(field: MappingFieldOut, catalog: PropertyCatalogOut | null): CatalogProperty[] {
  return field.kinds.includes("CUSTOM_PROPERTY") && catalog?.status === "AVAILABLE"
    ? catalog.properties
    : [];
}

function CopyFrom({
  options,
  onCopy,
  disabled,
}: {
  options: CopyOption[];
  onCopy: (id: string) => Promise<string>;
  disabled: boolean;
}) {
  const t = useTranslations("fieldMapping.form");
  const selectId = useId();
  const [choice, setChoice] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);

  async function run() {
    if (!choice || busy) return;
    setBusy(true);
    setNote(null);
    try {
      setNote({ ok: true, text: await onCopy(choice) });
    } catch (err) {
      setNote({
        ok: false,
        text: err instanceof Error && err.message ? err.message : t("copyFailed"),
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fm-copy">
      <label htmlFor={selectId} className="fm-copy-label">
        {t("copyLabel")}
      </label>
      <select
        id={selectId}
        className="select-control"
        value={choice}
        disabled={disabled || busy || options.length === 0}
        onChange={(e) => setChoice(e.target.value)}
      >
        <option value="">{options.length === 0 ? t("copyNone") : t("copyChoose")}</option>
        {options.map((o) => (
          <option key={o.id} value={o.id}>
            {o.label}
          </option>
        ))}
      </select>
      <button
        type="button"
        className="action secondary"
        disabled={disabled || busy || !choice}
        onClick={() => void run()}
      >
        {busy ? t("copying") : t("copy")}
      </button>
      {note && (
        <p className={`fm-note${note.ok ? "" : " is-crit"}`} role={note.ok ? "status" : "alert"}>
          {note.text}
        </p>
      )}
    </div>
  );
}

export function FieldMappingForm({
  schema,
  draft,
  onChange,
  catalog,
  typeName,
  showRequired,
  fieldErrors = {},
  disabled = false,
  onRefreshCatalog,
  copyFrom,
  referenceKinds,
  onKindChange,
}: {
  schema: MappingSchemaOut;
  draft: MappingDraft;
  onChange: (next: MappingDraft) => void;
  /** Null: the list could not be loaded at all, which reads as unavailable. */
  catalog: PropertyCatalogOut | null;
  /** The source's log-source type (DSM) name, for the type warning. */
  typeName: string | null;
  /** Show "Required" errors for empty fields (after a save attempt). */
  showRequired: boolean;
  /** Messages from the server, by field key. */
  fieldErrors?: Record<string, string>;
  disabled?: boolean;
  onRefreshCatalog?: () => Promise<void>;
  copyFrom?: { options: CopyOption[]; onCopy: (id: string) => Promise<string> };
  /** The chosen reference kind per field key. With `onKindChange` it turns on the kind switch. */
  referenceKinds?: Record<string, string>;
  onKindChange?: (key: string, kind: string) => void;
}) {
  const t = useTranslations("fieldMapping.form");
  const i18n = useBehaviorI18n();
  const uid = useId();
  const [refreshing, setRefreshing] = useState(false);
  const errors = useMemo(() => draftErrors(schema, draft, i18n), [schema, draft, i18n]);

  function setProperty(key: string, value: string) {
    onChange({ ...draft, mapping: { ...draft.mapping, [key]: value } });
  }

  async function refresh() {
    if (!onRefreshCatalog || refreshing) return;
    setRefreshing(true);
    try {
      await onRefreshCatalog();
    } finally {
      setRefreshing(false);
    }
  }

  const available = catalog?.status === "AVAILABLE";

  return (
    <div className="fm-form">
      <div className="fm-toolbar">
        <p className="fm-catalog" role="status">
          {available ? (
            <>
              {t.rich("catalogFetched", { time: () => <UtcTime value={catalog.fetched_at} /> })}{" "}
              {catalog.filtered_by_source_type
                ? t("catalogApplies", { type: typeName ?? t("catalogThisType") })
                : t("catalogUnfiltered")}
            </>
          ) : (
            t("catalogDownTail", {
              lead:
                t("catalogDown") + (catalog?.detail ? `: ${catalog.detail}` : "."),
            })
          )}
        </p>
        {onRefreshCatalog && (
          <button
            type="button"
            className="action secondary"
            disabled={disabled || refreshing}
            onClick={() => void refresh()}
          >
            {refreshing ? t("refreshing") : t("refresh")}
          </button>
        )}
      </div>

      {copyFrom && <CopyFrom options={copyFrom.options} onCopy={copyFrom.onCopy} disabled={disabled} />}

      <div className="fm-rows">
        {schema.fields.map((field) => {
          const value = draft.mapping[field.key] ?? "";
          const inputId = `${uid}-${field.key}`;
          const descId = `${inputId}-d`;
          const msgId = `${inputId}-m`;
          const switchable = Boolean(referenceKinds && onKindChange) && field.kinds.length > 1;
          const kind = switchable ? (referenceKinds?.[field.key] ?? field.kinds[0]) : null;
          const normalizedKind = kind === "NORMALIZED";
          // A QRadar normalized field is not in the console's property list.
          const note = normalizedKind ? null : catalogNote(value, catalog, typeName, i18n);
          const clientError = errors[field.key];
          const error =
            fieldErrors[field.key] ??
            (clientError && (value.trim() !== "" || showRequired) ? clientError : null);
          const hasMsg = Boolean(error || note);
          return (
            <div className="fm-row" key={field.key}>
              <div className="fm-meta">
                <label htmlFor={inputId} className="fm-label">
                  {field.label}
                </label>
                <span className={`fm-req${field.required ? " is-required" : ""}`}>
                  {field.required ? t("required") : t("optional")}
                </span>
                <p id={descId} className="fm-desc">
                  {field.description}
                </p>
              </div>
              <div className="fm-control">
                {switchable && kind && (
                  <fieldset className="fm-kind">
                    <legend className="sr-only">{t("kindLegend", { field: field.label })}</legend>
                    {field.kinds.map((k) => (
                      <label key={k} className="fm-radio">
                        <input
                          type="radio"
                          name={`${inputId}-kind`}
                          checked={k === kind}
                          disabled={disabled}
                          onChange={() => onKindChange?.(field.key, k)}
                        />
                        {referenceKindLabel(k, i18n)}
                      </label>
                    ))}
                  </fieldset>
                )}
                <div className="fm-input-line">
                  {normalizedKind ? (
                    <select
                      id={inputId}
                      className="select-control"
                      aria-label={t("fieldAria", { field: field.label })}
                      aria-describedby={hasMsg ? `${descId} ${msgId}` : descId}
                      aria-invalid={Boolean(error) || undefined}
                      value={value}
                      disabled={disabled}
                      onChange={(e) => setProperty(field.key, e.target.value)}
                    >
                      {(value === "" || !field.required) && (
                        <option value="">{field.required ? t("chooseField") : t("notMapped")}</option>
                      )}
                      {value !== "" && !field.normalized_options.includes(value) && (
                        <option value={value}>{t("notAllowed", { value })}</option>
                      )}
                      {field.normalized_options.map((o) => (
                        <option key={o} value={o}>
                          {o}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <PropertyInput
                      id={inputId}
                      label={t("propertyLabel", { field: field.label })}
                      value={value}
                      placeholder={field.required ? t("propertyPlaceholder") : t("notMapped")}
                      suggestions={propertiesFor(field, catalog)}
                      normalized={switchable ? [] : normalizedFor(field)}
                      describedBy={hasMsg ? `${descId} ${msgId}` : descId}
                      invalid={Boolean(error)}
                      disabled={disabled}
                      onChange={(next) => setProperty(field.key, next)}
                    />
                  )}
                  {!field.required && value !== "" && (
                    <button
                      type="button"
                      className="action secondary"
                      disabled={disabled}
                      aria-label={t("unmapAria", { field: field.label })}
                      onClick={() => setProperty(field.key, "")}
                    >
                      {t("unmap")}
                    </button>
                  )}
                </div>
                {hasMsg && (
                  <p id={msgId} className={`fm-note${error ? " is-crit" : " is-warn"}`}>
                    {error ?? note?.text}
                  </p>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
