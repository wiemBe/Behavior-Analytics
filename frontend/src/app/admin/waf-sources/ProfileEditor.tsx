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
import { useTranslations } from "next-intl";

import { FieldMappingForm } from "@/components/fieldmapping/FieldMappingForm";
import { MappingHistory } from "@/components/fieldmapping/MappingHistory";
import { ValidationReport } from "@/components/fieldmapping/ValidationReport";
import { useBackendMessage } from "@/i18n/backendMessage";
import { ValueMapEditor, outcomeLabel } from "@/components/fieldmapping/ValueMapEditor";
import { PageHeader } from "@/components/ui/PageHeader";
import { guideFor } from "@/lib/guideRoutes";
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
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";
import type { BehaviorI18n } from "@/lib/behaviorI18n";
import { ARROW_SEP, ENGLISH_WAF } from "@/components/waf/wafI18n";
import { formatCount } from "@/lib/format";
import { versionLabel } from "@/lib/behaviorI18n";

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
  origin:
    | string
    | { kind: "seeded"; name: string; version: number }
    | { kind: "defaults" };
}

type Msg = { tone: "ok" | "crit" | "warn" | "info"; text: string } | null;

const ADD_MARK = "+";
const REMOVE_MARK = "−";

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

function failure(
  err: unknown,
  action: "change" | "read" | "validate",
  i18n: BehaviorI18n = ENGLISH_WAF,
): Msg {
  if (err instanceof ApiError && err.status === 403) {
    return {
      tone: "crit",
      text: i18n.t(`waf.editor.cannot${action[0].toUpperCase()}${action.slice(1)}`),
    };
  }
  return {
    tone: "crit",
    text: i18n.t("waf.editor.notChanged", { message: actionErrorMessage(err, i18n.t) }),
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
  const t = useTranslations("waf.editor");
  const pageT = useTranslations("waf.admin.page");
  const common = useTranslations("common");
  const i18n = useBehaviorI18n();
  const backendMessage = useBackendMessage();
  const { locale } = i18n;
  const count = (value: number) => formatCount(value, locale);
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
  const errors = useMemo(() => draftErrors(schema, draft, i18n), [schema, draft, i18n]);
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
        text: t("fixFields"),
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
          text: t("createdVersion", { version: count(out.profile_version) }),
        });
      } else if (out.profile_version === previousVersion) {
        setSaveMsg({
          tone: "ok",
          text: t("savedLabel"),
        });
      } else {
        setSaveMsg({
          tone: "ok",
          text: t("savedVersion", { version: count(out.profile_version) }),
        });
      }
      void refreshHistory();
    } catch (err) {
      setConfirmAt(null);
      if (err instanceof ApiError && err.status === 409) {
        setConflict(conflictInfo(err));
      } else if (err instanceof ApiError && err.status === 422) {
        const text = serverMessage(err, backendMessage) || t("serverRejected");
        // The field is found in the server's own English: the property name is in it verbatim.
        const key = fieldForMessage(schema, draft, serverMessage(err));
        if (key) setFieldErrors({ [key]: text });
        else setFormError(text);
        setSaveMsg({ tone: "crit", text: t("formRejected") });
      } else {
        setSaveMsg(failure(err, "change", i18n));
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
        text: t("loadedTheirs", { version: count(next.profile_version) }),
      });
      void refreshHistory();
    } catch (err) {
      setSaveMsg(failure(err, "read", i18n));
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
      setValidateMsg({
        tone: "ok",
        text: t("validationRequested", { version: count(out.profile_version) }),
      });
      void refreshHistory();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setValidateMsg({
          tone: "warn",
          text: t("validationNotStarted", {
            message: serverMessage(err, backendMessage) || t("alreadyRunning"),
          }),
        });
      } else {
        setValidateMsg(failure(err, "validate", i18n));
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
          ? t("collectionOn", { version: count(out.profile_version) })
          : t("collectionOff"),
      });
      void refreshHistory();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setCollectMsg({
          tone: "warn",
          text: t("notEnabled", {
            message: serverMessage(err, backendMessage) || t("currentNotValid"),
          }),
        });
      } else {
        setCollectMsg(failure(err, "change", i18n));
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
        text: t("catalogFailed", {
          reason:
            err instanceof ApiError && err.status === 403
              ? t("catalogForbidden")
              : t("catalogPrevious"),
        }),
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
          label: `${s.log_source_name} (${versionLabel(formatCount(s.profile_version, locale))})`,
        })),
    [siblings, source.id, instanceId, locale],
  );

  async function copyFrom(id: string): Promise<string> {
    let other: WafProfileOut;
    try {
      other = await api.wafProfile(id);
    } catch (err) {
      throw new Error(t("notCopied", { message: actionErrorMessage(err, i18n.t) }));
    }
    const copied = draftFromProfile(schema, other);
    setDraft((d) => ({ ...copied, productLabel: d.productLabel }));
    setConfirmAt(null);
    return t("copied", {
      name: other.log_source_name,
      version: count(other.profile_version),
    });
  }

  // --- header ---------------------------------------------------------------
  const stateInput = profile
    ? {
        ...profile,
        fields: profile.validation?.fields,
        renderBackendMessage: backendMessage,
      }
    : null;
  const vState = stateInput ? validationState(stateInput, schema, i18n) : null;
  const cState = stateInput ? collectionState(stateInput, i18n) : null;

  const meta = (
    <>
      <span className="muted num">{t("qradarId", { id: count(source.qradarId) })}</span>
      <span className="muted">{typeName ?? t("typeUnknown")}</span>
      {profile?.product_label && <span className="muted">{profile.product_label}</span>}
      {profile ? (
        <>
          <span className="pill">{t("version", { version: count(profile.profile_version) })}</span>
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
        <span className="pill">{t("noProfileYet")}</span>
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
        aria-label={t("confirmLabel")}
        tabIndex={-1}
        ref={confirmRef}
        onKeyDown={(event) => {
          if (event.key === "Escape" && busy === null) {
            event.stopPropagation();
            closeConfirmation();
          }
        }}
      >
        <h3>{creating ? t("createQuestion") : t("saveQuestion")}</h3>
        {changeRows.length > 0 && (
          <ul className="fm-changes" aria-label={t("changedFields")}>
            {changeRows.map((c) => (
              <li key={c.key}>
                {c.label}:{" "}
                {c.from === null ? <span className="muted">{t("notMapped")}</span> : <code>{c.from}</code>}
                {ARROW_SEP}
                {c.to === null ? <span className="muted">{t("notMapped")}</span> : <code>{c.to}</code>}
              </li>
            ))}
          </ul>
        )}
        {changes.values.length > 0 && (
          <ul className="fm-changes" aria-label={t("changedValues")}>
            {changes.values.map((v) => (
              <li key={v.outcome}>
                {t("valuesLine", { outcome: outcomeLabel(v.outcome, i18n) })}
                {v.added.map((a) => (
                  <span key={`a-${a}`}>
                    {ADD_MARK} <code>{a}</code>{" "}
                  </span>
                ))}
                {v.removed.map((r) => (
                  <span key={`r-${r}`}>
                    {REMOVE_MARK} <code>{r}</code>{" "}
                  </span>
                ))}
              </li>
            ))}
          </ul>
        )}
        {changes.labelChanged && !creating && (
          <p className="fm-note">
            {t("labelChangesTo", { label: draft.productLabel.trim() || common("none") })}
          </p>
        )}
        {creating ? (
          <p>{t("createExplains")}</p>
        ) : mapped ? (
          <p>{t("mappingExplains", { version: count(nextVersion) })}</p>
        ) : (
          <p>{t("labelExplains")}</p>
        )}
        <div className="actions">
          <button type="button" className="action" disabled={busy !== null} onClick={() => void save()}>
            {busy === "save"
              ? common("saving")
              : creating
                ? t("createProfile")
                : labelOnly
                  ? t("saveLabel")
                  : t("saveAsVersion", { version: count(nextVersion) })}
          </button>
          <button
            type="button"
            className="action secondary"
            disabled={busy !== null}
            onClick={closeConfirmation}
          >
            {t("cancel")}
          </button>
        </div>
      </div>
    );
  };

  const changesBar = (where: "mapping" | "values") => (
    <div className="fm-bar">
      <p className="fm-bar-state">
        {creating
          ? t("newProfile")
          : dirty
            ? t(changes.total === 1 ? "unsavedOne" : "unsavedMany", {
                count: count(changes.total),
              })
            : t("noChanges")}
      </p>
      <div className="actions">
        <button
          type="button"
          className="action"
          disabled={busy !== null || (!creating && !dirty) || confirmAt === where}
          onClick={(event) => review(where, event.currentTarget)}
        >
          {creating ? t("reviewCreate") : t("reviewSave")}
        </button>
        {!creating && (
          <button
            type="button"
            className="action secondary"
            disabled={busy !== null || !dirty}
            onClick={discard}
          >
            {t("discard")}
          </button>
        )}
      </div>
    </div>
  );

  const block = profile ? enableBlockReason(profile, i18n) : null;
  const prefillOrigin =
    typeof prefill?.origin === "string"
      ? prefill.origin
      : prefill?.origin.kind === "seeded"
        ? pageT("prefillSeeded", {
            name: prefill.origin.name,
            version: count(prefill.origin.version),
          })
        : prefill?.origin.kind === "defaults"
          ? pageT("prefillDefaults")
          : t("defaultsOrigin");

  return (
    <>
      <PageHeader guide={guideFor("/admin/waf-sources/[logSourceId]")} title={source.name} meta={meta} />

      {creating && (
        <p className="fm-banner is-info" role="status">
          <span>
            <strong>{t("noProfile")}</strong> {prefillOrigin} {t("createTail")}
          </span>
        </p>
      )}

      {conflict && (
        <div className="fm-banner is-warn" role="alert">
          <span>{conflictMessage(conflict, i18n, backendMessage)}</span>
          <button
            type="button"
            className="action secondary"
            disabled={busy !== null}
            onClick={() => void reloadTheirs()}
          >
            {busy === "reload" ? t("conflictLoading") : t("conflictReload")}
          </button>
        </div>
      )}

      <section aria-labelledby="fm-mapping-h" className="fm-section">
        <h2 id="fm-mapping-h">{t("mappingTitle")}</h2>
        <p className="subtitle">{t("mappingNote")}</p>
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
          <label htmlFor="fm-product-label">{t("productLabel")}</label>
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
            {t("productLabelNote")}
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
          <h2 id="fm-validation-h">{t("validationTitle")}</h2>
          <div className="fm-actions-line">
            <button
              type="button"
              className="action secondary"
              disabled={busy !== null || pending}
              onClick={() => void validateNow()}
            >
              {busy === "validate"
                ? t("requesting")
                : pending
                  ? t("validationRunning")
                  : t("validateAgain")}
            </button>
            {dirty && (
              <span className="muted">
                {t("validatesSaved", { version: count(profile.profile_version) })}
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
            <h2 id={`fm-values-${vm.field}`}>{t("valuesTitle", { label })}</h2>
            <p className="subtitle">
              {t("valuesNote", {
                label: label.toLocaleLowerCase(i18n.locale === "tr" ? "tr-TR" : "en-US"),
                outcomes: vm.outcomes
                  .map((o) =>
                    outcomeLabel(o, i18n).toLocaleLowerCase(i18n.locale === "tr" ? "tr-TR" : "en-US"),
                  )
                  .join(i18n.locale === "tr" ? " ve " : " and "),
              })}
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
                  ? t("blockedReason", { label })
                  : null
              }
              discoveredNote={
                actionReport && seenUnder && now && seenUnder !== now
                  ? t("discoveredNote", { seen: seenUnder, now })
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
          <h2 id="fm-collection-h">{t("collectionTitle")}</h2>
          <p className="fm-state-line">
            <strong>{cState?.label}</strong>
            {profile.validated_at && vState?.kind === "valid" && (
              <span className="muted">
                {t.rich("validatedAt", {
                  version: count(profile.profile_version),
                  time: () => <UtcTime value={profile.validated_at as string} />,
                })}
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
                {t("disableCollection")}
              </button>
            ) : (
              <button
                type="button"
                className="action"
                disabled={busy !== null || block !== null}
                aria-describedby={block ? "fm-enable-why" : undefined}
                onClick={() => void toggleCollection(true)}
              >
                {t("enableCollection")}
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
          <h2 id="fm-history-h">{t("historyTitle")}</h2>
          {history === null ? (
            <p className="fm-note is-crit" role="alert">
              {t("historyFailed")}
            </p>
          ) : (
            <MappingHistory schema={schema} entries={history} />
          )}
        </section>
      )}
    </>
  );
}
