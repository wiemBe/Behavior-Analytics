"use client";

// The configure drawer (UR.5.3), built like the catalog drawer: a native modal dialog on the
// right, `Esc` and Cancel close it without a change, Apply hands the choices back. It holds the
// range choice of a range-declaring widget ("Follow the dashboard" or a preset) and one select
// per `config` key, rendered from the widget's `ConfigSchema`. Every control is a pick from the
// enumerated options; there is no text field, so nothing typed here can reach a query.

import { useEffect, useId, useRef, useState } from "react";

import { RANGE_TEXT } from "./runtime";
import type { AnyWidgetDef, LayoutWidget, WidgetRange } from "./types";

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

/** One select per config key, with exactly the schema's options. */
export function ConfigForm({
  schema,
  values,
  onChange,
}: {
  schema: AnyWidgetDef["config"];
  values: Record<string, unknown>;
  onChange: (key: string, value: unknown) => void;
}) {
  const base = useId();
  return (
    <>
      {Object.entries(schema).map(([key, field]) => {
        const options = (field as { label: string; options: ReadonlyArray<unknown> }).options;
        const label = (field as { label: string }).label;
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
          onChange={(key, value) => setConfig((current) => ({ ...current, [key]: value }))}
        />
        <p className="dg-catalog-note">
          Applies to this session only; saving is not built yet.
        </p>
      </div>
      <footer className="dg-configure-foot">
        <button type="button" className="action secondary" onClick={onClose}>
          Cancel
        </button>
        <button type="button" className="action" onClick={() => onApply({ range, config })}>
          Apply
        </button>
      </footer>
    </dialog>
  );
}
