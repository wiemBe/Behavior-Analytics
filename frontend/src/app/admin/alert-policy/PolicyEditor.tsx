"use client";

// The editor for one policy row, shared by "Add policy" and a row's "Edit".
//
// Adding names the whole identity: family, scope (one source, a class, or a property watch),
// detector and console. Editing keeps the identity fixed and changes only the mode, because the
// API upserts on that identity. What saving does is shown before anything is sent. A failed save
// keeps the input and says why; a 422 shows the server's sentence next to the field it names.
// Submitting twice is not possible while a save is in flight.

import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { useTranslations } from "next-intl";

import { ChangeStatus, type ChangeOutcome } from "@/components/ui/ChangeStatus";
import {
  actionErrorMessage,
  alertPolicyApi,
  api,
  type AlertPolicyFamily,
  type AlertPolicyMode,
  type AlertPolicyRow,
  type AlertPolicyScope,
  type FleetSource,
  type PropertyWatch,
  type SourceClass,
} from "@/lib/api";
import { enumLabel, type EnumTranslator } from "@/i18n/enumLabels";
import {
  FAMILIES,
  MODES,
  allowsClass,
  detectorGroup,
  detectorsOf,
  identityKey,
  knownConsoles,
  saveFailure,
  type PolicyField,
  shortId,
} from "@/lib/alertPolicy";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";

import { SourcePicker } from "./SourcePicker";

import "@/components/ui/controls.css";

/** Every SourceClass, in the order the fleet page lists them. */
const CLASSES: readonly SourceClass[] = [
  "WAF",
  "IPS",
  "FIREWALL",
  "PROXY",
  "VPN",
  "LINUX",
  "WINDOWS",
  "NETWORK",
  "DNS",
  "DATABASE",
  "EMAIL",
  "ENDPOINT",
  "CLOUD",
  "IDENTITY",
  "APPLICATION",
  "QRADAR_INTERNAL",
  "OTHER",
  "UNCLASSIFIED",
];

type Target = { value: string; name: string; instanceId: string | null };
type Watches =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "error"; text: string }
  | { kind: "ready"; items: PropertyWatch[] };

export function PolicyEditor({
  rows,
  editing,
  onSaved,
  onCancel,
}: {
  /** The stored rows: the consoles they name, and whether an identity already has a row. */
  rows: AlertPolicyRow[];
  /** The row being edited; null adds a new one. */
  editing: AlertPolicyRow | null;
  onSaved: (text: string) => Promise<void>;
  onCancel: () => void;
}) {
  const t = useTranslations("alertPolicy");
  const enums = useTranslations("enums") as unknown as EnumTranslator;
  const i18n = useBehaviorI18n();
  const uid = useId();
  const className = (value: string) => enumLabel(enums, "sourceClass", value);

  const [family, setFamily] = useState<AlertPolicyFamily>(editing?.family ?? "log_source");
  const [scopeKind, setScopeKind] = useState<AlertPolicyScope>(editing?.scope_kind ?? "SOURCE");
  const [target, setTarget] = useState<Target | null>(null);
  const [sourceClass, setSourceClass] = useState<string>("");
  const [picking, setPicking] = useState(false);
  const [detector, setDetector] = useState<string>(editing?.detector_type ?? "");
  const [instanceId, setInstanceId] = useState<string>(editing?.instance_id ?? "");
  const [mode, setMode] = useState<AlertPolicyMode>(editing?.mode ?? "RECORD_ONLY");
  const [watches, setWatches] = useState<Watches>({ kind: "idle" });
  const [fieldError, setFieldError] = useState<{ field: PolicyField; text: string } | null>(null);
  const [outcome, setOutcome] = useState<ChangeOutcome>({ kind: "idle" });
  const [pending, setPending] = useState(false);
  const inFlight = useRef(false);

  const isWatch = family === "property_watch";
  const effectiveScope: AlertPolicyScope = allowsClass(family) ? scopeKind : "SOURCE";

  // Property watches are read when the family needs them, once.
  useEffect(() => {
    if (editing || !isWatch || watches.kind !== "idle") return;
    let cancelled = false;
    setWatches({ kind: "loading" });
    api
      .propertyWatches()
      .then((items) => {
        if (!cancelled) setWatches({ kind: "ready", items });
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setWatches({
            kind: "error",
            text: t("editor.watchesFailed", { reason: actionErrorMessage(err, i18n.t) }),
          });
        }
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isWatch, editing]);

  const detectorName = (type: string | null) =>
    type === null ? t("table.allDetectors") : enumLabel(enums, detectorGroup(family), type);

  // --- the identity this form would write ---------------------------------------
  let scopeValue: string | null;
  let scopeName: string | null;
  if (editing) {
    scopeValue = editing.scope_value;
    scopeName =
      editing.scope_kind === "CLASS"
        ? className(editing.scope_value)
        : (editing.source_name ?? shortId(editing.scope_value));
  } else if (effectiveScope === "CLASS") {
    scopeValue = sourceClass || null;
    scopeName = sourceClass ? className(sourceClass) : null;
  } else {
    scopeValue = target?.value ?? null;
    scopeName = target?.name ?? null;
  }
  const detectorType = detector === "" ? null : detector;
  const instance = editing ? editing.instance_id : instanceId === "" ? null : instanceId;

  const existing =
    scopeValue === null
      ? undefined
      : rows.find(
          (r) =>
            identityKey(r) ===
            identityKey({
              instance_id: instance,
              scope_kind: effectiveScope,
              scope_value: scopeValue,
              family,
              detector_type: detectorType,
            }),
        );
  const unchanged = scopeValue !== null && existing !== undefined && existing.mode === mode;

  const consoles = knownConsoles(rows);
  const consoleChoices = [...consoles];
  if (target?.instanceId && effectiveScope === "SOURCE" && !consoleChoices.some((c) => c.id === target.instanceId)) {
    consoleChoices.push({ id: target.instanceId, name: null });
  }
  const consoleName = (c: { id: string; name: string | null }) =>
    c.name ? t("editor.consoleOnly", { name: c.name }) : `${t("editor.consoleOnlyUnnamed")} (${shortId(c.id)})`;

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (inFlight.current || scopeValue === null) return;
    inFlight.current = true;
    setPending(true);
    setOutcome({ kind: "pending" });
    setFieldError(null);
    try {
      await alertPolicyApi.put({
        instance_id: instance,
        scope_kind: effectiveScope,
        scope_value: scopeValue,
        family,
        detector_type: detectorType,
        mode,
      });
      await onSaved(
        t("flash.saved", {
          name: scopeName ?? shortId(scopeValue),
          detector: detectorName(detectorType),
          mode: t(`mode.${mode}`),
        }),
      );
    } catch (err) {
      const failure = saveFailure(err, i18n.t);
      if (failure.field) {
        setFieldError({ field: failure.field, text: failure.text });
        setOutcome({ kind: "idle" });
      } else {
        setOutcome({ kind: failure.kind, text: failure.text });
      }
    } finally {
      inFlight.current = false;
      setPending(false);
    }
  }

  const ids = {
    family: `${uid}-family`,
    scope: `${uid}-scope`,
    cls: `${uid}-class`,
    watch: `${uid}-watch`,
    detector: `${uid}-detector`,
    console: `${uid}-console`,
    mode: `${uid}-mode`,
  };
  const errorAt = (field: PolicyField) =>
    fieldError?.field === field ? (
      <p className="field-error" role="alert">
        {fieldError.text}
      </p>
    ) : null;
  const title = editing
    ? t("editor.editTitle", { name: scopeName ?? "" })
    : t("editor.addTitle");

  return (
    <form className="rule-form outage-form" onSubmit={(e) => void submit(e)} aria-label={title}>
      <p className="outage-form-title">{title}</p>
      {editing && <p className="field-hint">{t("editor.fixed")}</p>}
      <div className="rule-form-grid">
        {editing ? (
          <>
            <span className="outage-form-label">{t("editor.scope")}</span>
            <p className="policy-fixed">
              {scopeName} · {t(`family.${editing.family}`)} · {detectorName(editing.detector_type)} ·{" "}
              {editing.instance_id === null
                ? t("table.allConsoles")
                : t("table.console", { name: editing.instance_name ?? shortId(editing.instance_id) })}
            </p>
          </>
        ) : (
          <>
            <label htmlFor={ids.family}>{t("editor.family")}</label>
            <select
              id={ids.family}
              className="field policy-select"
              value={family}
              disabled={pending}
              onChange={(e) => {
                const next = e.target.value as AlertPolicyFamily;
                setFamily(next);
                setDetector("");
                setTarget(null);
                setPicking(false);
                setInstanceId("");
                if (!allowsClass(next)) setScopeKind("SOURCE");
              }}
            >
              {FAMILIES.map((f) => (
                <option key={f} value={f}>
                  {t(`family.${f}`)}
                </option>
              ))}
            </select>

            <span id={ids.scope} className="outage-form-label">
              {t("editor.scope")}
            </span>
            <div>
              <div className="fm-radios" role="radiogroup" aria-labelledby={ids.scope}>
                <label className="fm-radio">
                  <input
                    type="radio"
                    name={ids.scope}
                    checked={effectiveScope === "SOURCE"}
                    disabled={pending}
                    onChange={() => {
                      setScopeKind("SOURCE");
                      setInstanceId("");
                    }}
                  />
                  {isWatch ? t("editor.scopeWatch") : t("editor.scopeSource")}
                </label>
                {allowsClass(family) && (
                  <label className="fm-radio">
                    <input
                      type="radio"
                      name={ids.scope}
                      checked={effectiveScope === "CLASS"}
                      disabled={pending}
                      onChange={() => {
                        setScopeKind("CLASS");
                        setInstanceId("");
                      }}
                    />
                    {t("editor.scopeClass")}
                  </label>
                )}
              </div>
              {isWatch && <p className="field-hint">{t("editor.classNoWatch")}</p>}
              {errorAt("scope")}
            </div>

            {effectiveScope === "CLASS" && (
              <>
                <label htmlFor={ids.cls}>{t("editor.class")}</label>
                <select
                  id={ids.cls}
                  className="field policy-select"
                  value={sourceClass}
                  disabled={pending}
                  onChange={(e) => setSourceClass(e.target.value)}
                >
                  <option value="">{t("editor.classChoose")}</option>
                  {CLASSES.map((c) => (
                    <option key={c} value={c}>
                      {className(c)}
                    </option>
                  ))}
                </select>
              </>
            )}

            {effectiveScope === "SOURCE" && !isWatch && (
              <>
                <span className="outage-form-label">{t("editor.source")}</span>
                <div>
                  <p className="policy-target">
                    {target ? target.name : <span className="muted">{t("editor.sourceNone")}</span>}{" "}
                    {!picking && (
                      <button
                        type="button"
                        className="action secondary"
                        disabled={pending}
                        onClick={() => setPicking(true)}
                      >
                        {target ? t("editor.sourceChange") : t("picker.choose")}
                      </button>
                    )}
                  </p>
                  {picking && (
                    <SourcePicker
                      onClose={() => setPicking(false)}
                      onChoose={(s: FleetSource) => {
                        setTarget({ value: s.id, name: s.name, instanceId: s.instance_id });
                        setInstanceId("");
                        setPicking(false);
                      }}
                    />
                  )}
                </div>
              </>
            )}

            {effectiveScope === "SOURCE" && isWatch && (
              <>
                <label htmlFor={ids.watch}>{t("editor.watch")}</label>
                <div>
                  {watches.kind === "loading" && (
                    <p className="field-hint" role="status">
                      {t("editor.watchesLoading")}
                    </p>
                  )}
                  {watches.kind === "error" && (
                    <p className="field-error" role="alert">
                      {watches.text}
                    </p>
                  )}
                  {watches.kind === "ready" && watches.items.length === 0 && (
                    <p className="field-hint" role="status">
                      {t("editor.watchesNone")}
                    </p>
                  )}
                  {watches.kind === "ready" && watches.items.length > 0 && (
                    <select
                      id={ids.watch}
                      className="field policy-select"
                      value={target?.value ?? ""}
                      disabled={pending}
                      onChange={(e) => {
                        const w = watches.items.find((x) => x.id === e.target.value);
                        setTarget(w ? { value: w.id, name: w.name, instanceId: w.instance_id } : null);
                        setInstanceId("");
                      }}
                    >
                      <option value="">{t("editor.watchChoose")}</option>
                      {watches.items.map((w) => (
                        <option key={w.id} value={w.id}>
                          {w.name}
                        </option>
                      ))}
                    </select>
                  )}
                </div>
              </>
            )}

            <label htmlFor={ids.detector}>{t("editor.detector")}</label>
            <div>
              <select
                id={ids.detector}
                className="field policy-select"
                value={detector}
                disabled={pending}
                aria-invalid={fieldError?.field === "detector" ? true : undefined}
                onChange={(e) => {
                  setDetector(e.target.value);
                  setFieldError(null);
                }}
              >
                <option value="">{t("editor.detectorAll")}</option>
                {detectorsOf(family).map((d) => (
                  <option key={d} value={d}>
                    {enumLabel(enums, detectorGroup(family), d)}
                  </option>
                ))}
              </select>
              {errorAt("detector")}
            </div>

            <label htmlFor={ids.console}>{t("editor.console")}</label>
            <div>
              <select
                id={ids.console}
                className="field policy-select"
                value={instanceId}
                disabled={pending}
                aria-invalid={fieldError?.field === "console" ? true : undefined}
                onChange={(e) => {
                  setInstanceId(e.target.value);
                  setFieldError(null);
                }}
              >
                <option value="">{t("editor.consoleAll")}</option>
                {consoleChoices.map((c) => (
                  <option key={c.id} value={c.id}>
                    {consoleName(c)}
                  </option>
                ))}
              </select>
              {errorAt("console")}
            </div>
          </>
        )}

        <span id={ids.mode} className="outage-form-label">
          {t("editor.mode")}
        </span>
        <div className="fm-radios" role="radiogroup" aria-labelledby={ids.mode}>
          {MODES.map((m) => (
            <label key={m} className="fm-radio">
              <input
                type="radio"
                name={ids.mode}
                value={m}
                checked={mode === m}
                disabled={pending}
                onChange={() => setMode(m)}
              />
              {t(`mode.${m}`)}
            </label>
          ))}
        </div>
      </div>

      <div className={scopeValue === null ? "impact is-unknown" : "impact"} role="group" aria-label={t("editor.before")}>
        <p className="impact-title">{t("editor.before")}</p>
        <ul className="impact-lines" aria-live="polite">
          <li>
            {scopeValue === null
              ? t("editor.needTarget")
              : unchanged
                ? t("editor.willStay", {
                    name: scopeName ?? "",
                    detector: detectorName(detectorType),
                    mode: t(`mode.${mode}`),
                  })
                : existing
                ? t("editor.willChange", {
                    name: scopeName ?? "",
                    detector: detectorName(detectorType),
                    from: t(`mode.${existing.mode}`),
                    mode: t(`mode.${mode}`),
                  })
                : t("editor.willSet", {
                    name: scopeName ?? "",
                    detector: detectorName(detectorType),
                    mode: t(`mode.${mode}`),
                  })}
          </li>
        </ul>
      </div>

      <div className="actions">
        <button type="submit" className="action" disabled={pending || scopeValue === null || unchanged}>
          {pending ? t("editor.saving") : t("editor.save")}
        </button>
        <button type="button" className="action secondary" disabled={pending} onClick={onCancel}>
          {t("editor.cancel")}
        </button>
        {unchanged && !pending && <span className="field-hint">{t("editor.unchanged")}</span>}
      </div>
      <ChangeStatus outcome={outcome} />
    </form>
  );
}
