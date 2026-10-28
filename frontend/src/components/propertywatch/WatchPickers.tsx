"use client";

import { useEffect, useId, useMemo, useState } from "react";
import { useTranslations } from "next-intl";

import {
  api,
  type PwPropertyOptions,
  type PwPropertyRef,
  type PwScopeKind,
  type PwScopeOption,
} from "@/lib/api";
import { formatCount } from "@/lib/format";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";

import {
  authoringScopeKindLabel,
  choosePropertyKind,
  propertyApplicability,
  scopeOptionTooLarge,
} from "./authoring";
import { scopeReason } from "./pwReason";

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
  const t = useTranslations("propertyWatch.pickers");
  const i18n = useBehaviorI18n();
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
        <label htmlFor={kindId}>{t("scopeKind")}</label>
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
          {(["LOG_SOURCE", "LOG_SOURCE_TYPE", "LOG_SOURCE_GROUP"] as PwScopeKind[]).map((value) => (
            <option key={value} value={value}>
              {authoringScopeKindLabel(value, i18n)}
            </option>
          ))}
        </select>
      </div>
      <div className="pw-field">
        <label htmlFor={queryId}>{t("findScope", {
          kind: authoringScopeKindLabel(kind, i18n).toLocaleLowerCase(
            i18n.locale === "tr" ? "tr-TR" : "en-US",
          ),
        })}</label>
        <input
          id={queryId}
          type="search"
          className="field"
          aria-controls={optionId}
          value={query}
          placeholder={t("searchPlaceholder")}
          onChange={(event) => setQuery(event.target.value)}
        />
      </div>
      <div className="pw-field pw-picker-result">
        <label htmlFor={optionId}>{authoringScopeKindLabel(kind, i18n)}</label>
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
              ? t("loading")
              : options.length === 0
                ? t("noMatch")
                : t("choose")}
          </option>
          {options.map((option) => {
            const count =
              option.synced_source_count === null
                ? t("memberUnavailable")
                : i18n.t("propertyWatch.common.sources", {
                    count: option.synced_source_count,
                  });
            const disabled = scopeOptionTooLarge(option);
            return (
              <option key={option.id} value={option.id} disabled={disabled}>
                {option.name} — {count}
                {option.detail ? ` — ${option.detail}` : ""}
                {disabled ? ` — ${scopeReason(option, i18n) ?? t("scopeTooLarge")}` : ""}
              </option>
            );
          })}
        </select>
        {state === "ready" && (
          <p className="field-hint" role="status">
            {options.length === 0
              ? t("noMatches", {
                  kind: authoringScopeKindLabel(kind, i18n).toLocaleLowerCase(
                    i18n.locale === "tr" ? "tr-TR" : "en-US",
                  ),
                })
              : t("listed", {
                  count: options.length,
                  kind: authoringScopeKindLabel(kind, i18n).toLocaleLowerCase(
                    i18n.locale === "tr" ? "tr-TR" : "en-US",
                  ),
                })}
          </p>
        )}
        {state === "error" && (
          <p className="field-error" role="alert">
            {t("loadFailed")}
          </p>
        )}
        {options.some(scopeOptionTooLarge) && (
          <div className="field-hint pw-oversize">
            <p>{t("oversizeTitle")}</p>
            <ul>
              {options.filter(scopeOptionTooLarge).map((option) => (
                <li key={option.id}>
                  <strong>{option.name}</strong>
                  {option.synced_source_count === null
                    ? ""
                    : `, ${i18n.t("propertyWatch.common.sources", {
                        count: option.synced_source_count,
                      })}`}
                  . {scopeReason(option, i18n) ?? t("splitScope")}
                </li>
              ))}
            </ul>
          </div>
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
  const t = useTranslations("propertyWatch.pickers");
  const i18n = useBehaviorI18n();
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
      <label htmlFor={inputId}>{t("propertyNumber", {
        index: formatCount(index + 1, i18n.locale),
      })}</label>
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
        placeholder={t("propertyPlaceholder")}
        onFocus={() => setOpen(true)}
        onChange={(event) => {
          const name = event.target.value;
          onChange({ kind: choosePropertyKind(name, normalized), name });
          setOpen(true);
        }}
        onBlur={() => window.setTimeout(() => setOpen(false), 0)}
      />
      <p className="field-hint">{propertyApplicability(value, options, i18n)}</p>
      {open && (
        <div
          id={listId}
          role="listbox"
          aria-label={t("propertyList", { index: formatCount(index + 1, i18n.locale) })}
          className="pw-option-list"
        >
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
              <span className="pw-option-hint">{i18n.t("propertyWatch.authoring.normalized")}</span>
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
                  ? i18n.t("propertyWatch.authoring.definedTypes", {
                      defined: formatCount(property.defined_on_type_ids.length, i18n.locale),
                      total: formatCount(options?.scope_type_ids.length ?? 0, i18n.locale),
                    })
                  : t("notSelectable")}
              </span>
            </button>
          ))}
          {rows.length === 0 && !normalized.includes(value.name) && (
            <p className="pw-option-empty">
              {t("noCatalogMatch")}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
