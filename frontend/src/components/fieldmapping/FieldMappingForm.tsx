"use client";

// The mapping editor: one row per field the schema declares.
//
// Driven entirely by `MappingSchemaOut` and knows nothing about WAF; the IPS
// screens render the same component with their own schema. The component is
// controlled. It never saves: the owner decides what a change means.

import { useId, useMemo, useState } from "react";

import { PropertyInput } from "@/components/fieldmapping/PropertyInput";
import type { CatalogProperty, MappingFieldOut, MappingSchemaOut, PropertyCatalogOut } from "@/lib/api";
import { catalogNote, draftErrors, type MappingDraft } from "@/lib/fieldMapping";
import { formatDateTime } from "@/lib/health";

import "@/components/ui/controls.css";

export interface CopyOption {
  id: string;
  label: string;
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
        text: err instanceof Error && err.message ? err.message : "The mapping could not be copied.",
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fm-copy">
      <label htmlFor={selectId} className="fm-copy-label">
        Copy mapping from…
      </label>
      <select
        id={selectId}
        className="select-control"
        value={choice}
        disabled={disabled || busy || options.length === 0}
        onChange={(e) => setChoice(e.target.value)}
      >
        <option value="">{options.length === 0 ? "No other source has a mapping" : "Choose a source"}</option>
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
        {busy ? "Copying…" : "Copy"}
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
}) {
  const uid = useId();
  const [refreshing, setRefreshing] = useState(false);
  const errors = useMemo(() => draftErrors(schema, draft), [schema, draft]);

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
              Property list from this console, fetched {formatDateTime(catalog.fetched_at)}.{" "}
              {catalog.filtered_by_source_type
                ? `Properties that apply to ${typeName ?? "this source’s type"} come first.`
                : "It is not filtered by log-source type."}
            </>
          ) : (
            <>
              The property list is unavailable
              {catalog?.detail ? `: ${catalog.detail}` : "."} Type each name as QRadar shows it;
              validation checks it.
            </>
          )}
        </p>
        {onRefreshCatalog && (
          <button
            type="button"
            className="action secondary"
            disabled={disabled || refreshing}
            onClick={() => void refresh()}
          >
            {refreshing ? "Refreshing…" : "Refresh list"}
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
          const note = catalogNote(value, catalog, typeName);
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
                  {field.required ? "Required" : "Optional"}
                </span>
                <p id={descId} className="fm-desc">
                  {field.description}
                </p>
              </div>
              <div className="fm-control">
                <div className="fm-input-line">
                  <PropertyInput
                    id={inputId}
                    label={`${field.label} property`}
                    value={value}
                    placeholder={field.required ? "Property name" : "Not mapped"}
                    suggestions={propertiesFor(field, catalog)}
                    normalized={normalizedFor(field)}
                    describedBy={hasMsg ? `${descId} ${msgId}` : descId}
                    invalid={Boolean(error)}
                    disabled={disabled}
                    onChange={(next) => setProperty(field.key, next)}
                  />
                  {!field.required && value !== "" && (
                    <button
                      type="button"
                      className="action secondary"
                      disabled={disabled}
                      aria-label={`Do not map ${field.label}`}
                      onClick={() => setProperty(field.key, "")}
                    >
                      Unmap
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
