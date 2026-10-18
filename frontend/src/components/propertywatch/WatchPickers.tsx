"use client";

import { useEffect, useId, useMemo, useState } from "react";

import {
  api,
  type PwPropertyOptions,
  type PwPropertyRef,
  type PwScopeKind,
  type PwScopeOption,
} from "@/lib/api";

import {
  SCOPE_KIND_LABELS,
  choosePropertyKind,
  propertyApplicability,
  scopeOptionTooLarge,
} from "./authoring";

type LoadState = "loading" | "ready" | "error";

export function WatchScopePicker({
  kind,
  selectedId,
  onKind,
  onSelect,
}: {
  kind: PwScopeKind;
  selectedId: string;
  onKind: (kind: PwScopeKind) => void;
  onSelect: (option: PwScopeOption | null) => void;
}) {
  const kindId = useId();
  const queryId = useId();
  const optionId = useId();
  const [query, setQuery] = useState("");
  const [options, setOptions] = useState<PwScopeOption[]>([]);
  const [state, setState] = useState<LoadState>("loading");

  useEffect(() => {
    const controller = new AbortController();
    setState("loading");
    const timer = window.setTimeout(() => {
      api
        .propertyWatchScopeOptions(kind, query, controller.signal)
        .then((rows) => {
          setOptions(rows);
          setState("ready");
        })
        .catch(() => {
          if (!controller.signal.aborted) setState("error");
        });
    }, 200);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [kind, query]);

  return (
    <div className="pw-picker-grid">
      <div className="pw-field">
        <label htmlFor={kindId}>Scope kind</label>
        <select
          id={kindId}
          className="select-control"
          value={kind}
          onChange={(event) => {
            setQuery("");
            onKind(event.target.value as PwScopeKind);
            onSelect(null);
          }}
        >
          {Object.entries(SCOPE_KIND_LABELS).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </div>
      <div className="pw-field">
        <label htmlFor={queryId}>Find {SCOPE_KIND_LABELS[kind].toLowerCase()}</label>
        <input
          id={queryId}
          type="search"
          className="field"
          role="combobox"
          aria-controls={optionId}
          aria-expanded="true"
          aria-autocomplete="list"
          value={query}
          placeholder="Search the synced inventory"
          onChange={(event) => setQuery(event.target.value)}
        />
      </div>
      <div className="pw-field pw-picker-result">
        <label htmlFor={optionId}>{SCOPE_KIND_LABELS[kind]}</label>
        <select
          id={optionId}
          className="select-control"
          value={selectedId}
          disabled={state === "loading"}
          onChange={(event) => {
            onSelect(options.find((option) => option.id === event.target.value) ?? null);
          }}
        >
          <option value="">
            {state === "loading"
              ? "Loading scope options…"
              : options.length === 0
                ? "No matching scope"
                : "Choose a scope"}
          </option>
          {options.map((option) => {
            const count =
              option.synced_source_count === null
                ? "member count unavailable"
                : `${option.synced_source_count.toLocaleString("en-US")} sources`;
            const disabled = scopeOptionTooLarge(option);
            return (
              <option key={option.id} value={option.id} disabled={disabled}>
                {option.name} — {count}
                {option.detail ? ` — ${option.detail}` : ""}
                {disabled ? ` — ${option.scope_reason ?? "scope is too large"}` : ""}
              </option>
            );
          })}
        </select>
        {state === "error" && (
          <p className="field-error" role="alert">
            Scope options could not be loaded. Check the backend, then search again.
          </p>
        )}
        {options.some(scopeOptionTooLarge) && (
          <p className="field-hint">
            Oversized groups are disabled. Split the group or use a type scope; the option names
            its resolved size and limit reason.
          </p>
        )}
      </div>
    </div>
  );
}

export function WatchPropertyPicker({
  index,
  value,
  options,
  disabled,
  onChange,
}: {
  index: number;
  value: PwPropertyRef;
  options: PwPropertyOptions | null;
  disabled?: boolean;
  onChange: (property: PwPropertyRef) => void;
}) {
  const inputId = useId();
  const listId = useId();
  const [open, setOpen] = useState(false);
  const normalized = options?.normalized_fields ?? ["username"];
  const query = value.name.trim().toLocaleLowerCase();
  const rows = useMemo(
    () =>
      (options?.properties ?? []).filter(
        (property) => query === "" || property.name.toLocaleLowerCase().includes(query),
      ),
    [options, query],
  );

  return (
    <div className="pw-field pw-property-picker">
      <label htmlFor={inputId}>Property {index + 1}</label>
      <input
        id={inputId}
        className="field pw-mono-input"
        type="text"
        role="combobox"
        aria-autocomplete="list"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        autoComplete="off"
        spellCheck={false}
        disabled={disabled}
        value={value.name}
        placeholder="Type or choose a property"
        onFocus={() => setOpen(true)}
        onChange={(event) => {
          const name = event.target.value;
          onChange({ kind: choosePropertyKind(name, normalized), name });
          setOpen(true);
        }}
        onBlur={() => window.setTimeout(() => setOpen(false), 0)}
      />
      <p className="field-hint">{propertyApplicability(value, options)}</p>
      {open && (
        <div id={listId} role="listbox" aria-label={`Properties for property ${index + 1}`} className="pw-option-list">
          {normalized.map((name) => (
            <button
              key={`normalized-${name}`}
              type="button"
              role="option"
              aria-selected={value.kind === "NORMALIZED" && value.name === name}
              className="pw-option"
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => {
                onChange({ kind: "NORMALIZED", name });
                setOpen(false);
              }}
            >
              <span className="pw-option-name">{name}</span>
              <span className="pw-option-hint">QRadar normalized field</span>
            </button>
          ))}
          {rows.map((property) => (
            <button
              key={property.name}
              type="button"
              role="option"
              aria-selected={value.kind === "CUSTOM_PROPERTY" && value.name === property.name}
              aria-disabled={!property.name_allowed || undefined}
              disabled={!property.name_allowed}
              className="pw-option"
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => {
                onChange({ kind: "CUSTOM_PROPERTY", name: property.name });
                setOpen(false);
              }}
            >
              <span className="pw-option-name">{property.name}</span>
              <span className="pw-option-hint">
                {property.name_allowed
                  ? `Defined on ${property.defined_on_type_ids.length} of ${options?.scope_type_ids.length ?? 0} types in this scope`
                  : "Not selectable: the name cannot be used safely in a stored property reference"}
              </span>
            </button>
          ))}
          {rows.length === 0 && !normalized.includes(value.name) && (
            <p className="pw-option-empty">
              No catalog entry matches. The typed name can still be saved after validation; the
              preview will show whether it parses.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
