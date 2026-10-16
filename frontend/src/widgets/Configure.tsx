"use client";

// The configure drawer (UR.5.3), built like the catalog drawer: a native modal dialog on the
// right, `Esc` and Cancel close it without a change, Apply hands the choices back. It holds the
// range choice of a range-declaring widget ("Follow the dashboard" or a preset) and one select
// per `config` key, rendered from the widget's `ConfigSchema`. A key's options are a fixed list
// or the ids of one existing GET read (UI-3.5b; a read may depend on another key, so changing
// that key clears this one). Every control is a pick; the only text field is the filter over a
// long read, and it narrows the options in the browser: its text is not a query, not a request
// body and not part of the stored config. Apply is off while a read is loading, has failed or
// is empty, and until each read key holds an id the read returned.

import { useCallback, useEffect, useId, useRef, useState } from "react";

import { useApiResource } from "@/hooks/useApiResource";

import { RANGE_TEXT } from "./runtime";
import {
  isReadField,
  type AnyWidgetDef,
  type ConfigOption,
  type LayoutWidget,
  type OptionsSource,
  type WidgetRange,
} from "./types";

import "./dashboard.css";

/** True when a widget has anything to configure: a range, or at least one config key. */
export function hasConfigure(def: AnyWidgetDef): boolean {
  return def.range !== null || Object.keys(def.config).length > 0;
}

export interface ConfigChoice {
  range: WidgetRange | null;
  config: Record<string, unknown>;
}

const FOLLOW = "";

/** A list longer than this gets a filter above its select, and its select shows a few rows. */
export const FILTER_AFTER = 8;
const LISTBOX_ROWS = 6;

const NO_READ = async (): Promise<unknown> => null;

/** The options of a read field narrowed by the filter; the chosen one always stays. */
function narrowed(options: ConfigOption[], filter: string, chosen: string | null): ConfigOption[] {
  const needle = filter.trim().toLowerCase();
  if (!needle) return options;
  return options.filter((o) => o.id === chosen || o.label.toLowerCase().includes(needle));
}

/** One select over the ids a read returned, with the read's own states designed. */
function ReadField({
  id,
  label,
  source,
  values,
  onChange,
  onReady,
  fieldKey,
}: {
  id: string;
  label: string;
  source: OptionsSource;
  values: Record<string, unknown>;
  onChange: (key: string, value: unknown) => void;
  onReady?: (key: string, ready: boolean) => void;
  fieldKey: string;
}) {
  const parent = source.dependsOn ? ((values[source.dependsOn] as string | null) ?? null) : null;
  const read = source.read(parent);
  const state = useApiResource(read?.key ?? null, read?.fetch ?? NO_READ);
  const [filter, setFilter] = useState("");
  const chosen = typeof values[fieldKey] === "string" ? (values[fieldKey] as string) : null;
  const list = state.status === "ok" && read ? read.options(state.data) : null;
  const options = list ? list.options : [];
  const ready = state.status === "ok" && options.some((o) => o.id === chosen);

  useEffect(() => {
    onReady?.(fieldKey, ready);
    return () => onReady?.(fieldKey, false);
  }, [fieldKey, ready, onReady]);

  const fieldLabel = <label htmlFor={id}>{label}</label>;
  if (read === null) {
    return (
      <div className="dg-field">
        {fieldLabel}
        <select id={id} value="" disabled>
          <option value="">{source.needs ?? "Choose the option above first"}</option>
        </select>
      </div>
    );
  }
  if (state.status === "idle" || state.status === "loading") {
    return (
      <div className="dg-field">
        {fieldLabel}
        <p className="dg-field-state" role="status">
          Loading the {source.what}…
        </p>
      </div>
    );
  }
  if (state.status === "forbidden") {
    return (
      <div className="dg-field">
        {fieldLabel}
        <p className="dg-field-state" role="alert">
          Your role cannot read the {source.what}, so nothing can be chosen here.
        </p>
      </div>
    );
  }
  if (state.status === "error") {
    return (
      <div className="dg-field">
        {fieldLabel}
        <p className="dg-field-state" role="alert">
          <strong>The {source.what} could not be read.</strong>{" "}
          {state.httpStatus === null
            ? "The backend did not answer. "
            : `The backend answered with HTTP ${state.httpStatus}. `}
          Close this drawer and open it again; if it still fails, check that the backend is
          reachable.
        </p>
      </div>
    );
  }
  if (options.length === 0) {
    return (
      <div className="dg-field">
        {fieldLabel}
        <p className="dg-field-state" role="status">
          <strong>No {source.what}.</strong> {source.emptyReason}
        </p>
      </div>
    );
  }
  const shown = narrowed(options, filter, chosen);
  const filterId = `${id}-filter`;
  // A long list is a listbox beside its filter, so the narrowed options are in view, not behind
  // a closed dropdown. It has no placeholder row: nothing is selected until one is picked.
  const long = options.length > FILTER_AFTER;
  return (
    <div className="dg-field">
      {fieldLabel}
      {long ? (
        <>
          <input
            id={filterId}
            type="search"
            aria-label={`Filter ${source.what}`}
            placeholder={`Filter ${source.what}`}
            autoComplete="off"
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
          />
          <p className="dg-field-note" aria-live="polite">
            {filter.trim()
              ? `${shown.length} of ${options.length} shown`
              : `${options.length} ${source.what}`}
          </p>
        </>
      ) : null}
      <select
        id={id}
        size={long ? LISTBOX_ROWS : undefined}
        value={chosen !== null && options.some((o) => o.id === chosen) ? chosen : ""}
        onChange={(event) => onChange(fieldKey, event.target.value)}
      >
        {ready ? null : long ? (
          // Selected but not shown: no row looks chosen before the analyst picks one.
          <option value="" disabled hidden />
        ) : (
          <option value="" disabled>
            Choose…
          </option>
        )}
        {shown.map((option) => (
          <option key={option.id} value={option.id}>
            {option.label}
          </option>
        ))}
      </select>
      {list && list.total > options.length ? (
        <p className="dg-field-note">
          The read returned the first {options.length} of {list.total} {source.what}; only
          these can be chosen here.
        </p>
      ) : null}
    </div>
  );
}

/** One select per config key: the schema's static options, or the ids of a read. */
export function ConfigForm({
  schema,
  values,
  onChange,
  onReady,
}: {
  schema: AnyWidgetDef["config"];
  values: Record<string, unknown>;
  onChange: (key: string, value: unknown) => void;
  /** Reported by each read field: true once it holds an id its read returned. */
  onReady?: (key: string, ready: boolean) => void;
}) {
  const base = useId();
  return (
    <>
      {Object.entries(schema).map(([key, field]) => {
        const label = (field as { label: string }).label;
        if (isReadField(field as never)) {
          return (
            <ReadField
              key={key}
              id={`${base}-${key}`}
              fieldKey={key}
              label={label}
              source={(field as { from: OptionsSource }).from}
              values={values}
              onChange={onChange}
              onReady={onReady}
            />
          );
        }
        const options = (field as { options: ReadonlyArray<unknown> }).options;
        const index = options.findIndex((option) => Object.is(option, values[key]));
        return (
          <div className="dg-field" key={key}>
            <label htmlFor={`${base}-${key}`}>{label}</label>
            <select
              id={`${base}-${key}`}
              value={index < 0 ? "" : String(index)}
              onChange={(event) => onChange(key, options[Number(event.target.value)])}
            >
              {index < 0 ? <option value="" disabled hidden /> : null}
              {options.map((option, i) => (
                <option key={i} value={String(i)}>
                  {String(option)}
                </option>
              ))}
            </select>
          </div>
        );
      })}
    </>
  );
}

/** Sets a key and clears every key whose read depends on it, and theirs in turn. */
export function setConfigKey(
  schema: AnyWidgetDef["config"],
  current: Record<string, unknown>,
  key: string,
  value: unknown,
): Record<string, unknown> {
  const next = { ...current, [key]: value };
  for (const [other, field] of Object.entries(schema)) {
    if (isReadField(field as never) && (field as { from: OptionsSource }).from.dependsOn === key) {
      Object.assign(next, setConfigKey(schema, next, other, null));
    }
  }
  return next;
}

export function ConfigureDrawer({
  def,
  widget,
  dashboardRange,
  onApply,
  onClose,
}: {
  def: AnyWidgetDef;
  widget: LayoutWidget;
  dashboardRange: WidgetRange;
  onApply: (choice: ConfigChoice) => void;
  onClose: () => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const firstRef = useRef<HTMLDivElement>(null);
  const rangeId = useId();
  const [range, setRange] = useState<WidgetRange | null>(widget.range);
  const [config, setConfig] = useState<Record<string, unknown>>({
    ...def.defaultConfig,
    ...widget.config,
  });
  const [ready, setReady] = useState<Record<string, boolean>>({});
  const onReady = useCallback(
    (key: string, value: boolean) =>
      setReady((current) => (current[key] === value ? current : { ...current, [key]: value })),
    [],
  );
  const readKeys = Object.entries(def.config)
    .filter(([, field]) => isReadField(field as never))
    .map(([key]) => key);
  const blocked = readKeys.some((key) => !ready[key]);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (!dialog.open) dialog.showModal();
    firstRef.current?.querySelector<HTMLElement>("select")?.focus();
    return () => {
      if (dialog.open) dialog.close();
    };
  }, []);

  return (
    <dialog
      ref={dialogRef}
      className="dg-catalog dg-configure"
      aria-labelledby="dg-configure-title"
      onCancel={(event) => {
        // Esc is a Cancel: the dialog closes by unmounting, so the page owns the focus return.
        event.preventDefault();
        onClose();
      }}
      onClose={onClose}
    >
      <header className="dg-catalog-head">
        <h2 id="dg-configure-title" className="dg-catalog-title">
          Configure {def.title}
        </h2>
      </header>
      <p className="dg-catalog-note">{def.description}</p>
      <div className="dg-catalog-body dg-configure-body" ref={firstRef}>
        {def.range !== null ? (
          <div className="dg-field">
            <label htmlFor={rangeId}>Time range</label>
            <select
              id={rangeId}
              value={range ?? FOLLOW}
              onChange={(event) =>
                setRange(event.target.value === FOLLOW ? null : (event.target.value as WidgetRange))
              }
            >
              <option value={FOLLOW}>Follow the dashboard ({RANGE_TEXT[dashboardRange]})</option>
              {def.range.map((option) => (
                <option key={option} value={option}>
                  {RANGE_TEXT[option]}
                </option>
              ))}
            </select>
          </div>
        ) : null}
        <ConfigForm
          schema={def.config}
          values={config}
          onChange={(key, value) =>
            setConfig((current) => setConfigKey(def.config, current, key, value))
          }
          onReady={onReady}
        />
        {blocked ? (
          <p className="dg-catalog-note" id="dg-configure-blocked">
            Apply is off until every choice above is made from what its read returned.
          </p>
        ) : null}
        <p className="dg-catalog-note">
          Applies to this session only; saving is not built yet.
        </p>
      </div>
      <footer className="dg-configure-foot">
        <button type="button" className="action secondary" onClick={onClose}>
          Cancel
        </button>
        <button
          type="button"
          className="action"
          disabled={blocked}
          aria-describedby={blocked ? "dg-configure-blocked" : undefined}
          onClick={() => onApply({ range, config })}
        >
          Apply
        </button>
      </footer>
    </dialog>
  );
}
