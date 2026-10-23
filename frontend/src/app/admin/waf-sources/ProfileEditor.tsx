"use client";

// One WAF source's profile: its mapping, what validation found, the action
// value map, the collection switch and the history.
//
// The editor holds a draft and never writes until the operator confirms. A
// mapping change pauses collection until the new version validates, and the
// confirmation says so before anything is sent. Authorization is the
// backend's: a 403 is shown, not pre-empted. Validation is asynchronous, so a
// pending validation is polled every five seconds until it ends.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { FieldMappingForm } from "@/components/fieldmapping/FieldMappingForm";
import { MappingHistory } from "@/components/fieldmapping/MappingHistory";
import { ValidationReport } from "@/components/fieldmapping/ValidationReport";
import { ValueMapEditor, outcomeLabel } from "@/components/fieldmapping/ValueMapEditor";
import { PageHeader } from "@/components/ui/PageHeader";
import {
  ApiError,
  actionErrorMessage,
  api,
  type MappingSchemaOut,
  type PropertyCatalogOut,
  type WafProfileHistoryEntryOut,
  type WafProfileOut,
  type WafSourceStatus,
} from "@/lib/api";
import {
  collectionState,
  conflictInfo,
  conflictMessage,
  draftChanges,
  draftErrors,
  draftFromDefaults,
  draftFromProfile,
  draftToInput,
  emptyDraft,
  enableBlockReason,
  fieldForMessage,
  serverMessage,
  validationState,
  type ConflictInfo,
  type MappingDraft,
} from "@/lib/fieldMapping";
import { UtcTime } from "@/components/ui/UtcTime";

import "@/components/ui/controls.css";

export interface EditorSource {
  id: string;
  name: string;
  qradarId: number;
  typeName: string | null;
  instanceId: string | null;
}

export interface Prefill {
  draft: MappingDraft;
  /** Where the form's starting values came from, in words. */
  origin: string;
}

type Msg = { tone: "ok" | "crit" | "warn" | "info"; text: string } | null;

function Notice({ msg }: { msg: Msg }) {
  if (!msg) return null;
  return (
    <p
      className={`fm-banner is-${msg.tone}`}
      role={msg.tone === "crit" || msg.tone === "warn" ? "alert" : "status"}
    >
      {msg.text}
    </p>
  );
}

function failure(err: unknown, verb: string): Msg {
  if (err instanceof ApiError && err.status === 403) {
    return {
      tone: "crit",
      text: `Your role cannot ${verb} WAF sources (permission admin:waf). Nothing was changed.`,
    };
  }
  return { tone: "crit", text: `Not changed. ${actionErrorMessage(err)}` };
}

function unavailable(detail: string): PropertyCatalogOut {
  return {
    status: "UNAVAILABLE",
    fetched_at: null,
    filtered_by_source_type: false,
    properties: [],
    detail,
  };
}

export function ProfileEditor({
  schema,
  source,
  profile: initialProfile,
  catalog: initialCatalog,
  history: initialHistory,
  siblings,
  prefill,
  pollMs = 5000,
}: {
  schema: MappingSchemaOut;
  source: EditorSource;
  profile: WafProfileOut | null;
  /** Null: the catalog could not be loaded, which reads as unavailable. */
  catalog: PropertyCatalogOut | null;
  /** Null: the history could not be loaded, which is not the same as empty. */
  history: WafProfileHistoryEntryOut[] | null;
  /** Other sources' states, for "Copy mapping from…". */
  siblings: WafSourceStatus[];
  /** Create mode only. */
  prefill: Prefill | null;
  pollMs?: number;
}) {
  const [profile, setProfile] = useState<WafProfileOut | null>(initialProfile);
  const [catalog, setCatalog] = useState<PropertyCatalogOut | null>(initialCatalog);
  const [history, setHistory] = useState(initialHistory);
  const creating = profile === null;

  // The version the draft is edited against. It moves only when this editor
  // saves or the operator loads a newer version, never silently.
  const [baseVersion, setBaseVersion] = useState<number | null>(
    initialProfile?.profile_version ?? null,
  );
  const baseDraft = useMemo(
    () => (profile ? draftFromProfile(schema, profile) : emptyDraft(schema)),
    [schema, profile],
  );
  const [draft, setDraft] = useState<MappingDraft>(() =>
    initialProfile
      ? draftFromProfile(schema, initialProfile)
      : (prefill?.draft ?? draftFromDefaults(schema)),
  );

  const [attempted, setAttempted] = useState(false);
  const [confirmAt, setConfirmAt] = useState<"mapping" | "values" | null>(null);
  const [busy, setBusy] = useState<null | "save" | "validate" | "collection" | "reload">(null);
  const [saveMsg, setSaveMsg] = useState<Msg>(null);
  const [validateMsg, setValidateMsg] = useState<Msg>(null);
  const [collectMsg, setCollectMsg] = useState<Msg>(null);
  const [conflict, setConflict] = useState<ConflictInfo | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const confirmRef = useRef<HTMLDivElement>(null);
  const confirmTrigger = useRef<HTMLButtonElement | null>(null);

  const changes = useMemo(() => draftChanges(schema, baseDraft, draft), [schema, baseDraft, draft]);
  const errors = useMemo(() => draftErrors(schema, draft), [schema, draft]);
  const dirty = changes.total > 0;

  const typeName = profile?.log_source_type_name ?? source.typeName;
  const instanceId = profile?.instance_id ?? source.instanceId;

  const refreshHistory = useCallback(async () => {
    try {
      setHistory(await api.wafProfileHistory(source.id));
    } catch {
      /* the previous history stays; it is only context */
    }
  }, [source.id]);

  // Poll while a validation runs. A different version appearing means someone
  // else changed the mapping: surface it instead of letting a later save lose it.
  const pending = profile?.validation_pending ?? false;
  useEffect(() => {
    if (!pending) return;
    let cancelled = false;
    const timer = setInterval(() => {
      void (async () => {
        try {
          const next = await api.wafProfile(source.id);
          if (cancelled) return;
          setProfile(next);
          if (!next.validation_pending) void refreshHistory();
        } catch {
          /* keep polling; the last known state stays on screen */
        }
      })();
    }, pollMs);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [pending, pollMs, source.id, refreshHistory]);

  useEffect(() => {
    if (profile && baseVersion !== null && profile.profile_version !== baseVersion) {
      setConflict((c) => c ?? { version: profile.profile_version, by: profile.updated_by, message: null });
    }
  }, [profile, baseVersion]);

  useEffect(() => {
    if (confirmAt) confirmRef.current?.focus();
  }, [confirmAt]);

  function review(where: "mapping" | "values", from: HTMLButtonElement) {
    setSaveMsg(null);
    setFormError(null);
    setFieldErrors({});
    setAttempted(true);
    if (Object.keys(errors).length > 0) {
      setSaveMsg({
        tone: "crit",
        text: "Not saved. Fix the fields marked below; nothing was sent.",
      });
      return;
    }
    confirmTrigger.current = from;
    setConfirmAt(where);
  }

  function closeConfirmation() {
    setConfirmAt(null);
    const from = confirmTrigger.current;
    confirmTrigger.current = null;
    queueMicrotask(() => from?.focus());
  }

  function discard() {
    setDraft(baseDraft);
    setConfirmAt(null);
    setAttempted(false);
    setFieldErrors({});
    setFormError(null);
    setSaveMsg(null);
  }

  async function save() {
    if (busy) return;
    setBusy("save");
    setSaveMsg(null);
    try {
      const previousVersion = profile?.profile_version ?? null;
      const out = await api.saveWafProfile(source.id, draftToInput(schema, draft, baseVersion));
      setProfile(out);
      setBaseVersion(out.profile_version);
      setDraft(draftFromProfile(schema, out));
      setConfirmAt(null);
      setAttempted(false);
      setConflict(null);
      setFieldErrors({});
      setFormError(null);
      if (previousVersion === null) {
        setSaveMsg({
          tone: "ok",
          text: `Created version ${out.profile_version}. Collection stays off. Validation of version ${out.profile_version} has been requested.`,
        });
      } else if (out.profile_version === previousVersion) {
        setSaveMsg({
          tone: "ok",
          text: "Saved. This was a label change only, so the version and its validation are unchanged.",
        });
      } else {
        setSaveMsg({
          tone: "ok",
          text: `Saved as version ${out.profile_version}. Collection is paused until it validates. Validation has been requested.`,
        });
      }
      void refreshHistory();
    } catch (err) {
      setConfirmAt(null);
      if (err instanceof ApiError && err.status === 409) {
        setConflict(conflictInfo(err));
      } else if (err instanceof ApiError && err.status === 422) {
        const text = serverMessage(err) || "The server rejected this mapping.";
        const key = fieldForMessage(schema, draft, text);
        if (key) setFieldErrors({ [key]: text });
        else setFormError(text);
        setSaveMsg({ tone: "crit", text: "Not saved. The server rejected the mapping; see the message below." });
      } else {
        setSaveMsg(failure(err, "change"));
      }
    } finally {
      setBusy(null);
    }
  }

  async function reloadTheirs() {
    if (busy) return;
    setBusy("reload");
    try {
      const next = await api.wafProfile(source.id);
      setProfile(next);
      setBaseVersion(next.profile_version);
      setConflict(null);
      setSaveMsg({
        tone: "info",
        text: `Loaded version ${next.profile_version}. Your edits are still in the form; review them against it.`,
      });
      void refreshHistory();
    } catch (err) {
      setSaveMsg(failure(err, "read"));
    } finally {
      setBusy(null);
    }
  }

  async function validateNow() {
    if (busy || !profile) return;
    setBusy("validate");
    setValidateMsg(null);
    try {
      const out = await api.validateWafProfile(source.id);
      setProfile(out);
      setValidateMsg({ tone: "ok", text: `Validation of version ${out.profile_version} has been requested.` });
      void refreshHistory();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setValidateMsg({
          tone: "warn",
          text: `Validation was not started. ${serverMessage(err) || "A validation is already running."}`,
        });
      } else {
        setValidateMsg(failure(err, "validate"));
      }
    } finally {
      setBusy(null);
    }
  }

  async function toggleCollection(enable: boolean) {
    if (busy || !profile) return;
    setBusy("collection");
    setCollectMsg(null);
    try {
      const out = enable
        ? await api.enableWafProfile(source.id)
        : await api.disableWafProfile(source.id);
      setProfile(out);
      setCollectMsg({
        tone: "ok",
        text: enable
          ? `Collection is enabled for version ${out.profile_version}.`
          : "Collection is disabled. No searches run for this source.",
      });
      void refreshHistory();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setCollectMsg({ tone: "warn", text: `Not enabled. ${serverMessage(err) || "The current version is not valid."}` });
      } else {
        setCollectMsg(failure(err, "change"));
      }
    } finally {
      setBusy(null);
    }
  }

  async function refreshCatalog() {
    try {
      setCatalog(await api.wafPropertyCatalog(source.id, true));
    } catch (err) {
      setSaveMsg({
        tone: "warn",
        text: `The property list could not be refreshed. ${
          err instanceof ApiError && err.status === 403
            ? "Your role cannot read it (permission admin:waf)."
            : "The previous list is still shown."
        }`,
      });
    }
  }

  const copyOptions = useMemo(
    () =>
      siblings
        .filter((s) => s.log_source_id !== source.id)
        .filter((s) => (instanceId ? s.instance_id === instanceId : true))
        .map((s) => ({
          id: s.log_source_id,
          label: `${s.log_source_name} (v${s.profile_version})`,
        })),
    [siblings, source.id, instanceId],
  );

  async function copyFrom(id: string): Promise<string> {
    let other: WafProfileOut;
    try {
      other = await api.wafProfile(id);
    } catch (err) {
      throw new Error(`Not copied. ${actionErrorMessage(err)}`);
    }
    const copied = draftFromProfile(schema, other);
    setDraft((d) => ({ ...copied, productLabel: d.productLabel }));
    setConfirmAt(null);
    return `Copied from ${other.log_source_name} (version ${other.profile_version}). Nothing is saved until you save.`;
  }

  // --- header ---------------------------------------------------------------
  const stateInput = profile ? { ...profile, fields: profile.validation?.fields } : null;
  const vState = stateInput ? validationState(stateInput, schema) : null;
  const cState = stateInput ? collectionState(stateInput) : null;

  const meta = (
    <>
      <span className="muted num">QRadar id {source.qradarId}</span>
      <span className="muted">{typeName ?? "log-source type unknown"}</span>
      {profile?.product_label && <span className="muted">{profile.product_label}</span>}
      {profile ? (
        <>
          <span className="pill">Version {profile.profile_version}</span>
          {vState && (
            <span className={`pill ${vState.tone === "neutral" ? "" : vState.tone}`}>
              {vState.label}
            </span>
          )}
          {cState && (
            <span className={`pill ${cState.collecting ? "ok" : "warn"}`}>{cState.label}</span>
          )}
        </>
      ) : (
        <span className="pill">No profile yet</span>
      )}
    </>
  );

  // --- sections ---------------------------------------------------------------
  const valueMaps = schema.value_maps;
  const actionReport = profile?.validation?.fields.find((f) => f.field === valueMaps[0]?.field);

  const confirmation = (where: "mapping" | "values") => {
    if (confirmAt !== where) return null;
    const nextVersion = (profile?.profile_version ?? 0) + 1;
    const mapped = creating || changes.mappingChanged;
    const labelOnly = !creating && !changes.mappingChanged && changes.labelChanged;
    const changeRows = creating
      ? schema.fields
          .filter((f) => (draft.mapping[f.key] ?? "").trim() !== "")
          .map((f) => ({ key: f.key, label: f.label, from: null, to: draft.mapping[f.key].trim() }))
      : changes.fields;
    return (
      <div
        className="fm-confirm"
        role="region"
        aria-label="Confirm the change"
        tabIndex={-1}
        ref={confirmRef}
        onKeyDown={(event) => {
          if (event.key === "Escape" && busy === null) {
            event.stopPropagation();
            closeConfirmation();
          }
        }}
      >
        <h3>{creating ? "Create this profile?" : "Save this change?"}</h3>
        {changeRows.length > 0 && (
          <ul className="fm-changes" aria-label="Changed fields">
            {changeRows.map((c) => (
              <li key={c.key}>
                {c.label}:{" "}
                {c.from === null ? <span className="muted">not mapped</span> : <code>{c.from}</code>}
                {" → "}
                {c.to === null ? <span className="muted">not mapped</span> : <code>{c.to}</code>}
              </li>
            ))}
          </ul>
        )}
        {changes.values.length > 0 && (
          <ul className="fm-changes" aria-label="Changed values">
            {changes.values.map((v) => (
              <li key={v.outcome}>
                {outcomeLabel(v.outcome)} values:{" "}
                {v.added.map((a) => (
                  <span key={`a-${a}`}>
                    + <code>{a}</code>{" "}
                  </span>
                ))}
                {v.removed.map((r) => (
                  <span key={`r-${r}`}>
                    − <code>{r}</code>{" "}
                  </span>
                ))}
              </li>
            ))}
          </ul>
        )}
        {changes.labelChanged && !creating && (
          <p className="fm-note">The product label changes to “{draft.productLabel.trim() || "none"}”.</p>
        )}
        {creating ? (
          <p>
            The profile is created disabled. Collection from this source stays off until version 1
            validates and you enable it. Validation starts now and runs a few bounded searches
            against QRadar.
          </p>
        ) : mapped ? (
          <p>
            Collection from this source pauses until version {nextVersion} validates. Validation
            starts now and runs a few bounded searches against QRadar.
          </p>
        ) : (
          <p>
            Only the product label changes. The version, its validation and collection are
            unaffected.
          </p>
        )}
        <div className="actions">
          <button type="button" className="action" disabled={busy !== null} onClick={() => void save()}>
            {busy === "save"
              ? "Saving…"
              : creating
                ? "Create profile"
                : labelOnly
                  ? "Save label"
                  : `Save as version ${nextVersion}`}
          </button>
          <button
            type="button"
            className="action secondary"
            disabled={busy !== null}
            onClick={closeConfirmation}
          >
            Cancel
          </button>
        </div>
      </div>
    );
  };

  const changesBar = (where: "mapping" | "values") => (
    <div className="fm-bar">
      <p className="fm-bar-state">
        {creating
          ? "A new profile. Nothing is saved yet."
          : dirty
            ? `${changes.total} unsaved ${changes.total === 1 ? "change" : "changes"}.`
            : "No unsaved changes."}
      </p>
      <div className="actions">
        <button
          type="button"
          className="action"
          disabled={busy !== null || (!creating && !dirty) || confirmAt === where}
          onClick={(event) => review(where, event.currentTarget)}
        >
          {creating ? "Review and create" : "Review and save"}
        </button>
        {!creating && (
          <button
            type="button"
            className="action secondary"
            disabled={busy !== null || !dirty}
            onClick={discard}
          >
            Discard changes
          </button>
        )}
      </div>
    </div>
  );

  const block = profile ? enableBlockReason(profile) : null;

  return (
    <>
      <PageHeader title={source.name} meta={meta} />

      {creating && (
        <p className="fm-banner is-info" role="status">
          <span>
            <strong>This log source has no WAF profile.</strong>{" "}
            {prefill?.origin ?? "Prefilled from the schema defaults."} Review the mapping, then
            create the profile. Nothing is saved until you confirm.
          </span>
        </p>
      )}

      {conflict && (
        <div className="fm-banner is-warn" role="alert">
          <span>{conflictMessage(conflict)}</span>
          <button
            type="button"
            className="action secondary"
            disabled={busy !== null}
            onClick={() => void reloadTheirs()}
          >
            {busy === "reload" ? "Loading…" : "Reload their version"}
          </button>
        </div>
      )}

      <section aria-labelledby="fm-mapping-h" className="fm-section">
        <h2 id="fm-mapping-h">Field mapping</h2>
        <p className="subtitle">
          The QRadar property each logical field is read from for this source. Changing a
          property pauses collection until the new version validates.
        </p>
        <FieldMappingForm
          schema={schema}
          draft={draft}
          onChange={(next) => {
            setDraft(next);
            setFieldErrors({});
            setFormError(null);
          }}
          catalog={catalog}
          typeName={typeName}
          showRequired={attempted}
          fieldErrors={fieldErrors}
          disabled={busy !== null}
          onRefreshCatalog={refreshCatalog}
          copyFrom={{ options: copyOptions, onCopy: copyFrom }}
        />
        <div className="fm-label-row">
          <label htmlFor="fm-product-label">Product label</label>
          <input
            id="fm-product-label"
            className="field fm-input"
            type="text"
            maxLength={128}
            value={draft.productLabel}
            disabled={busy !== null}
            aria-describedby="fm-product-label-d"
            onChange={(e) => setDraft({ ...draft, productLabel: e.target.value })}
          />
          <span id="fm-product-label-d" className="muted">
            An operator note, for example the WAF product. Never parsed.
          </span>
        </div>
        {formError && (
          <p className="fm-banner is-crit" role="alert">
            {formError}
          </p>
        )}
        <Notice msg={saveMsg} />
        {changesBar("mapping")}
        {confirmation("mapping")}
      </section>

      {profile && (
        <section aria-labelledby="fm-validation-h" className="fm-section">
          <h2 id="fm-validation-h">Validation</h2>
          <div className="fm-actions-line">
            <button
              type="button"
              className="action secondary"
              disabled={busy !== null || pending}
              onClick={() => void validateNow()}
            >
              {busy === "validate" ? "Requesting…" : pending ? "Validation running" : "Validate again"}
            </button>
            {dirty && (
              <span className="muted">
                Validates the saved version {profile.profile_version}, not your unsaved edits.
              </span>
            )}
          </div>
          <Notice msg={validateMsg} />
          <ValidationReport
            schema={schema}
            report={profile.validation}
            profileVersion={profile.profile_version}
            pending={pending ? { since: profile.validation_requested_at } : null}
            requiredCoverage={schema.min_coverage_ratio ?? null}
          />
        </section>
      )}

      {profile && valueMaps.map((vm) => {
        const def = schema.fields.find((f) => f.key === vm.field);
        const label = def?.label ?? vm.field;
        const unmapped = (draft.mapping[vm.field] ?? "").trim() === "";
        const seenUnder = actionReport?.property_name ?? null;
        const now = (draft.mapping[vm.field] ?? "").trim();
        return (
          <section key={vm.field} aria-labelledby={`fm-values-${vm.field}`} className="fm-section">
            <h2 id={`fm-values-${vm.field}`}>{label} values</h2>
            <p className="subtitle">
              Which raw {label.toLowerCase()} values count as {vm.outcomes.map((o) => outcomeLabel(o).toLowerCase()).join(" and ")}.
              Saving this map is a mapping change: it bumps the version and validates again.
            </p>
            <ValueMapEditor
              fieldLabel={label}
              outcomes={vm.outcomes}
              discovered={actionReport ? actionReport.discovered_values : null}
              truncated={actionReport?.values_truncated ?? false}
              map={draft.actionMap}
              disabled={busy !== null}
              blockedReason={
                unmapped
                  ? `No property is mapped for ${label}, so values cannot be assigned yet.`
                  : null
              }
              discoveredNote={
                actionReport && seenUnder && now && seenUnder !== now
                  ? `These values were counted under ${seenUnder}; the mapping now reads ${now}. Validate again to refresh them.`
                  : null
              }
              onChange={(next) => setDraft({ ...draft, actionMap: next })}
            />
            {errors["@values"] && (
              <p className="fm-note is-crit" role="alert">
                {errors["@values"]}
              </p>
            )}
            {changes.values.length > 0 && changesBar("values")}
            {confirmation("values")}
          </section>
        );
      })}

      {profile && (
        <section aria-labelledby="fm-collection-h" className="fm-section">
          <h2 id="fm-collection-h">Collection</h2>
          <p className="fm-state-line">
            <strong>{cState?.label}</strong>
            {profile.validated_at && vState?.kind === "valid" && (
              <span className="muted">
                {" "}
                · version {profile.profile_version} validated{" "}
                <UtcTime value={profile.validated_at} />
              </span>
            )}
          </p>
          <div className="fm-actions-line">
            {profile.enabled ? (
              <button
                type="button"
                className="action secondary"
                disabled={busy !== null}
                onClick={() => void toggleCollection(false)}
              >
                Disable collection
              </button>
            ) : (
              <button
                type="button"
                className="action"
                disabled={busy !== null || block !== null}
                aria-describedby={block ? "fm-enable-why" : undefined}
                onClick={() => void toggleCollection(true)}
              >
                Enable collection
              </button>
            )}
            {!profile.enabled && block && (
              <span id="fm-enable-why" className="muted">
                {block}
              </span>
            )}
          </div>
          <Notice msg={collectMsg} />
        </section>
      )}

      {profile && (
        <section aria-labelledby="fm-history-h" className="fm-section">
          <h2 id="fm-history-h">History</h2>
          {history === null ? (
            <p className="fm-note is-crit" role="alert">
              The history could not be loaded. Reload the page to try again; the profile itself is
              unaffected.
            </p>
          ) : (
            <MappingHistory schema={schema} entries={history} />
          )}
        </section>
      )}
    </>
  );
}
