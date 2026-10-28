"use client";

// One IPS source's profile: its field references, what validation found, the
// action value map, the collection switch and the history.
//
// The same editor as the WAF one (docs/FIELD-MAPPING.md §FM.8), with the IPS
// differences of §FM.9: a field is read from a QRadar normalized field or from
// a custom property, and a preset only prefills. The editor holds a draft and
// never writes until the operator confirms. A mapping change pauses collection
// until the new version validates, and the confirmation says so first.
// Validation is asynchronous, so a pending one is polled every five seconds.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslations } from "next-intl";

import {
  draftFromDefaults,
  draftFromIpsProfile,
  draftFromPreset,
  draftToIpsInput,
  formSchema,
  genericNameNotes,
  historyForTable,
  ipsDraftChanges,
  ipsFieldStatusInfo,
  ipsHistoryActionLabel,
  ipsOutcomeNote,
  ipsReasonText,
  lastValidationWasBusy,
  referenceKindOf,
  reportForTable,
  type IpsDraft,
} from "@/app/admin/ips-sources/ips";
import { FieldMappingForm, referenceKindLabel } from "@/components/fieldmapping/FieldMappingForm";
import { MappingHistory } from "@/components/fieldmapping/MappingHistory";
import { ValidationReport } from "@/components/fieldmapping/ValidationReport";
import { ValueMapEditor, outcomeLabel } from "@/components/fieldmapping/ValueMapEditor";
import { PageHeader } from "@/components/ui/PageHeader";
import { guideFor } from "@/lib/guideRoutes";
import {
  ApiError,
  actionErrorMessage,
  ipsApi,
  type IpsMappingSchemaOut,
  type IpsPresetKey,
  type IpsProfileHistoryEntryOut,
  type IpsProfileOut,
  type IpsReferenceKind,
  type PropertyCatalogOut,
} from "@/lib/api";
import {
  collectionState,
  conflictInfo,
  conflictMessage,
  draftErrors,
  enableBlockReason,
  fieldForMessage,
  serverMessage,
  validationState,
  type ConflictInfo,
} from "@/lib/fieldMapping";
import { ARROW } from "@/lib/behaviorI18n";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";
import { UtcTime } from "@/components/ui/UtcTime";

import "@/components/ui/controls.css";

export interface IpsEditorSource {
  id: string;
  name: string;
  qradarId: number;
  typeName: string | null;
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

const NO_PRESET = "NONE";

/** The marks a value change line carries before its added and removed values. */
const PLUS = "+";
const MINUS = "−";

export function IpsProfileEditor({
  schema,
  source,
  profile: initialProfile,
  catalog: initialCatalog,
  history: initialHistory,
  pollMs = 5000,
}: {
  schema: IpsMappingSchemaOut;
  source: IpsEditorSource;
  profile: IpsProfileOut | null;
  /** Null: the catalog could not be loaded, which reads as unavailable. */
  catalog: PropertyCatalogOut | null;
  /** Null: the history could not be loaded, which is not the same as empty. */
  history: IpsProfileHistoryEntryOut[] | null;
  pollMs?: number;
}) {
  const t = useTranslations("ips.editor");
  const i18n = useBehaviorI18n();
  const formSch = useMemo(() => formSchema(schema), [schema]);
  const [profile, setProfile] = useState<IpsProfileOut | null>(initialProfile);
  const [catalog, setCatalog] = useState<PropertyCatalogOut | null>(initialCatalog);
  const [history, setHistory] = useState(initialHistory);
  const creating = profile === null;

  // The version the draft is edited against. It moves only when this editor
  // saves or the operator loads a newer version, never silently.
  const [baseVersion, setBaseVersion] = useState<number | null>(
    initialProfile?.profile_version ?? null,
  );
  const emptyBase = useMemo<IpsDraft>(() => {
    const blank = draftFromDefaults(schema);
    return {
      ...blank,
      mapping: Object.fromEntries(schema.fields.map((f) => [f.key, ""])),
      actionMap: Object.fromEntries(Object.keys(blank.actionMap).map((o) => [o, [] as string[]])),
    };
  }, [schema]);
  const baseDraft = useMemo(
    () => (profile ? draftFromIpsProfile(schema, profile) : emptyBase),
    [schema, profile, emptyBase],
  );
  const [draft, setDraft] = useState<IpsDraft>(() =>
    initialProfile ? draftFromIpsProfile(schema, initialProfile) : draftFromDefaults(schema),
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

  function failure(err: unknown, verb: "change" | "read" | "validate"): Msg {
    if (err instanceof ApiError && err.status === 403) {
      return { tone: "crit", text: t(`forbidden.${verb}`) };
    }
    return { tone: "crit", text: t("notChanged", { message: actionErrorMessage(err) }) };
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

  const changes = useMemo(() => ipsDraftChanges(schema, baseDraft, draft), [schema, baseDraft, draft]);
  const errors = useMemo(() => draftErrors(formSch, draft, i18n), [formSch, draft, i18n]);
  const dirty = changes.total > 0;
  const typeName = profile?.log_source_type_name ?? source.typeName;

  const refreshHistory = useCallback(async () => {
    try {
      setHistory(await ipsApi.profileHistory(source.id));
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
          const next = await ipsApi.profile(source.id);
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
      setConflict(
        (c) => c ?? { version: profile.profile_version, by: profile.updated_by, message: null },
      );
    }
  }, [profile, baseVersion]);

  useEffect(() => {
    if (confirmAt) confirmRef.current?.focus();
  }, [confirmAt]);

  function setKind(key: string, kind: string) {
    const field = schema.fields.find((f) => f.key === key);
    // The name belongs to the old kind. The schema default is offered again
    // only when it is of the kind being switched to.
    const restored = field?.default?.kind === kind ? field.default.name : "";
    setDraft({
      ...draft,
      kinds: { ...draft.kinds, [key]: kind as IpsReferenceKind },
      mapping: { ...draft.mapping, [key]: restored },
    });
    setFieldErrors({});
    setFormError(null);
  }

  function choosePreset(key: string) {
    setConfirmAt(null);
    setFieldErrors({});
    setFormError(null);
    setDraft((d) =>
      key === NO_PRESET
        ? { ...draftFromDefaults(schema), productLabel: d.productLabel }
        : draftFromPreset(schema, key as IpsPresetKey, d.productLabel),
    );
  }

  function review(where: "mapping" | "values", from: HTMLButtonElement) {
    setSaveMsg(null);
    setFormError(null);
    setFieldErrors({});
    setAttempted(true);
    if (Object.keys(errors).length > 0) {
      setSaveMsg({ tone: "crit", text: t("notSavedFix") });
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
      // The draft carries the stored preset, which a save replaces.
      const out = await ipsApi.saveProfile(source.id, draftToIpsInput(schema, draft, baseVersion));
      setProfile(out);
      setBaseVersion(out.profile_version);
      setDraft(draftFromIpsProfile(schema, out));
      setConfirmAt(null);
      setAttempted(false);
      setConflict(null);
      setFieldErrors({});
      setFormError(null);
      if (previousVersion === null) {
        setSaveMsg({ tone: "ok", text: t("created", { version: out.profile_version }) });
      } else if (out.profile_version === previousVersion) {
        setSaveMsg({ tone: "ok", text: t("savedLabelOnly") });
      } else {
        setSaveMsg({ tone: "ok", text: t("savedVersion", { version: out.profile_version }) });
      }
      void refreshHistory();
    } catch (err) {
      setConfirmAt(null);
      if (err instanceof ApiError && err.status === 409) {
        setConflict(conflictInfo(err));
      } else if (err instanceof ApiError && err.status === 422) {
        const text = serverMessage(err) || t("serverRejected");
        const key = fieldForMessage(formSch, draft, text);
        if (key) setFieldErrors({ [key]: text });
        else setFormError(text);
        setSaveMsg({ tone: "crit", text: t("notSavedRejected") });
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
      const next = await ipsApi.profile(source.id);
      setProfile(next);
      setBaseVersion(next.profile_version);
      setConflict(null);
      setSaveMsg({ tone: "info", text: t("loadedTheirs", { version: next.profile_version }) });
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
      const out = await ipsApi.validateProfile(source.id);
      setProfile(out);
      setValidateMsg({ tone: "ok", text: t("validateRequested", { version: out.profile_version }) });
      void refreshHistory();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setValidateMsg({
          tone: "warn",
          text: t("validateNotStarted", {
            message: serverMessage(err) || t("alreadyRunning"),
          }),
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
        ? await ipsApi.enableProfile(source.id)
        : await ipsApi.disableProfile(source.id);
      setProfile(out);
      setCollectMsg({
        tone: "ok",
        text: enable
          ? t("collectEnabled", { version: out.profile_version })
          : t("collectDisabled"),
      });
      void refreshHistory();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setCollectMsg({
          tone: "warn",
          text: t("notEnabled", { message: serverMessage(err) || t("notValid") }),
        });
      } else {
        setCollectMsg(failure(err, "change"));
      }
    } finally {
      setBusy(null);
    }
  }

  async function refreshCatalog() {
    try {
      setCatalog(await ipsApi.propertyCatalog(source.id, true));
    } catch (err) {
      setCatalog((c) => c ?? unavailable(t("catalogUnavailable")));
      setSaveMsg({
        tone: "warn",
        text: t("catalogRefreshFailed", {
          reason:
            err instanceof ApiError && err.status === 403 ? t("catalogForbidden") : t("catalogPrevious"),
        }),
      });
    }
  }

  // --- header ---------------------------------------------------------------
  const stateInput = profile ?? null;
  const vState = stateInput ? validationState(stateInput, null, i18n) : null;
  const cState = stateInput ? collectionState(stateInput, i18n) : null;

  const meta = (
    <>
      <span className="muted num">{t("qradarId", { id: source.qradarId })}</span>
      <span className="muted">{typeName ?? t("typeUnknown")}</span>
      {profile?.product_label && <span className="muted">{profile.product_label}</span>}
      {profile ? (
        <>
          <span className="pill">{t("versionPill", { version: profile.profile_version })}</span>
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
        <span className="pill">{t("noProfile")}</span>
      )}
    </>
  );

  // --- sections ---------------------------------------------------------------
  const valueMap = schema.value_maps[0] ?? null;
  const report = profile?.validation ?? null;
  const actionReport = valueMap ? report?.fields.find((f) => f.field === valueMap.field) : undefined;
  const actionLabel = schema.fields.find((f) => f.key === valueMap?.field)?.label ?? "Action";
  const actionMapped = valueMap ? (draft.mapping[valueMap.field] ?? "").trim() !== "" : false;
  const actionTruncated = Boolean(actionReport?.values_truncated);
  const currentReport = report !== null && report.profile_version === profile?.profile_version;
  const busyBefore = profile ? lastValidationWasBusy(history, profile.profile_version) : false;
  const tableReport = useMemo(() => reportForTable(report), [report]);
  const notes = useMemo(() => (report ? genericNameNotes(report, i18n) : []), [report, i18n]);

  const lower = (text: string) => text.toLocaleLowerCase(i18n.locale === "tr" ? "tr-TR" : "en-US");
  const truncatedNote =
    actionTruncated && report?.status === "INCONCLUSIVE" && currentReport
      ? t("truncatedNote", { action: lower(actionLabel) })
      : null;

  const actionUnmappedNote = (
    <p className="fm-banner is-info" role="status">
      <span>
        <strong>{t("actionUnmappedStrong", { action: actionLabel })}</strong>{" "}
        {t("actionUnmappedBody")}
      </span>
    </p>
  );

  const confirmation = (where: "mapping" | "values") => {
    if (confirmAt !== where) return null;
    const nextVersion = (profile?.profile_version ?? 0) + 1;
    const mapped = creating || changes.mappingChanged;
    const labelOnly = !creating && !changes.mappingChanged && changes.labelChanged;
    const changeRows = creating
      ? schema.fields
          .filter((f) => (draft.mapping[f.key] ?? "").trim() !== "")
          .map((f) => ({
            key: f.key,
            label: f.label,
            from: null,
            to: `${draft.mapping[f.key].trim()}${
              draft.kinds[f.key] === "CUSTOM_PROPERTY" ? " (custom property)" : ""
            }`,
          }))
      : changes.fields;
    return (
      <div
        className="fm-confirm"
        role="region"
        aria-label={t("confirmRegion")}
        tabIndex={-1}
        ref={confirmRef}
        onKeyDown={(event) => {
          if (event.key === "Escape" && busy === null) {
            event.stopPropagation();
            closeConfirmation();
          }
        }}
      >
        <h3>{creating ? t("createTitle") : t("saveTitle")}</h3>
        {changeRows.length > 0 && (
          <ul className="fm-changes" aria-label={t("changedFields")}>
            {changeRows.map((c) => (
              <li key={c.key}>
                {c.label}:{" "}
                {c.from === null ? (
                  <span className="muted">{t("notMapped")}</span>
                ) : (
                  <code>{c.from}</code>
                )}
                <span className="muted"> {ARROW} </span>
                {c.to === null ? <span className="muted">{t("notMapped")}</span> : <code>{c.to}</code>}
              </li>
            ))}
          </ul>
        )}
        {changes.values.length > 0 && (
          <ul className="fm-changes" aria-label={t("changedValues")}>
            {changes.values.map((v) => (
              <li key={v.outcome}>
                {t("valuesLine", { outcome: outcomeLabel(v.outcome, i18n) })}{" "}
                {v.added.map((a) => (
                  <span key={`a-${a}`}>
                    {PLUS} <code>{a}</code>{" "}
                  </span>
                ))}
                {v.removed.map((r) => (
                  <span key={`r-${r}`}>
                    {MINUS} <code>{r}</code>{" "}
                  </span>
                ))}
              </li>
            ))}
          </ul>
        )}
        {changes.labelChanged && !creating && (
          <p className="fm-note">
            {t("labelChange", { label: draft.productLabel.trim() || t("noneLabel") })}
          </p>
        )}
        {creating ? (
          <p>
            {t("createBody")} {draft.preset ? t("presetTail") : ""}
          </p>
        ) : mapped ? (
          <p>{t("mappedBody", { version: nextVersion })}</p>
        ) : (
          <p>{t("labelOnlyBody")}</p>
        )}
        <div className="actions">
          <button type="button" className="action" disabled={busy !== null} onClick={() => void save()}>
            {busy === "save"
              ? t("saving")
              : creating
                ? t("createButton")
                : labelOnly
                  ? t("saveLabelButton")
                  : t("saveVersionButton", { version: nextVersion })}
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
            ? t("unsaved", { count: changes.total })
            : t("noUnsaved")}
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
  const presetValue = draft.preset ?? NO_PRESET;

  return (
    <>
      <PageHeader guide={guideFor("/admin/ips-sources/[logSourceId]")} title={source.name} meta={meta} />

      {creating && (
        <p className="fm-banner is-info" role="status">
          <span>
            {t.rich("creatingBanner", { strong: (chunks) => <strong>{chunks}</strong> })}
          </span>
        </p>
      )}

      {conflict && (
        <div className="fm-banner is-warn" role="alert">
          <span>{conflictMessage(conflict, i18n)}</span>
          <button
            type="button"
            className="action secondary"
            disabled={busy !== null}
            onClick={() => void reloadTheirs()}
          >
            {busy === "reload" ? t("loading") : t("reloadTheirs")}
          </button>
        </div>
      )}

      <section aria-labelledby="fm-mapping-h" className="fm-section">
        <h2 id="fm-mapping-h">{t("mappingTitle")}</h2>
        <p className="subtitle">{t("mappingSubtitle")}</p>

        {creating && (
          <fieldset className="fm-presets" disabled={busy !== null}>
            <legend>{t("presetLegend")}</legend>
            <p className="fm-note">{t("presetNote")}</p>
            <div className="fm-preset-options">
              {schema.presets.map((p) => (
                <label key={p.key} className="fm-radio">
                  <input
                    type="radio"
                    name="ips-preset"
                    checked={presetValue === p.key}
                    onChange={() => choosePreset(p.key)}
                  />
                  {p.label}
                </label>
              ))}
              <label className="fm-radio">
                <input
                  type="radio"
                  name="ips-preset"
                  checked={presetValue === NO_PRESET}
                  onChange={() => choosePreset(NO_PRESET)}
                />
                {t("noPreset")}
              </label>
            </div>
          </fieldset>
        )}

        <FieldMappingForm
          schema={formSch}
          draft={draft}
          onChange={(next) => {
            setDraft({ ...draft, ...next });
            setFieldErrors({});
            setFormError(null);
          }}
          catalog={catalog}
          typeName={typeName}
          showRequired={attempted}
          fieldErrors={fieldErrors}
          disabled={busy !== null}
          onRefreshCatalog={refreshCatalog}
          referenceKinds={draft.kinds}
          onKindChange={setKind}
        />
        {creating && !actionMapped && actionUnmappedNote}
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
            {t("productLabelHelp")}
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
              {busy === "validate" ? t("requesting") : pending ? t("running") : t("validateAgain")}
            </button>
            {dirty && (
              <span className="muted">{t("validatesSaved", { version: profile.profile_version })}</span>
            )}
          </div>
          <Notice msg={validateMsg} />
          {busyBefore && !pending && (
            <p className="fm-banner is-warn" role="status">
              {t("busyBefore", { version: profile.profile_version })}
            </p>
          )}
          <ValidationReport
            schema={formSch}
            report={tableReport}
            profileVersion={profile.profile_version}
            pending={pending ? { since: profile.validation_requested_at } : null}
            requiredCoverage={schema.min_coverage_ratio}
            notes={notes}
            hideColumns={["distinct", "plausible"]}
            statusInfo={(status, ctx) => ipsFieldStatusInfo(status, ctx, i18n)}
            formatReason={(reason, ratio) => ipsReasonText(reason, schema, ratio, i18n)}
            describeReference={(f) => {
              const kind = referenceKindOf(report, f.field);
              return kind ? referenceKindLabel(kind, i18n) : null;
            }}
          />
        </section>
      )}

      {profile && valueMap && (
        <section aria-labelledby="fm-values-h" className="fm-section">
          <h2 id="fm-values-h">{t("valuesTitle", { action: actionLabel })}</h2>
          <p className="subtitle">
            {t("valuesSubtitle", {
              action: lower(actionLabel),
              // The Turkish sentence puts the outcomes after a colon, so their labels keep
              // their capital; the English one runs them into the sentence in lower case.
              outcomes: valueMap.outcomes
                .map((o) => (i18n.locale === "tr" ? outcomeLabel(o, i18n) : lower(outcomeLabel(o))))
                .join(` ${t("and")} `),
            })}
          </p>
          {!actionMapped ? (
            actionUnmappedNote
          ) : (
            <>
              <ValueMapEditor
                fieldLabel={actionLabel}
                outcomes={valueMap.outcomes}
                discovered={actionReport ? actionReport.discovered_values : null}
                truncated={actionTruncated}
                truncatedNote={truncatedNote}
                map={draft.actionMap}
                disabled={busy !== null}
                discoveredNote={
                  actionReport?.reference &&
                  actionReport.reference.name !== (draft.mapping[valueMap.field] ?? "").trim()
                    ? t("discoveredNote", {
                        reference: actionReport.reference.name,
                        mapping: draft.mapping[valueMap.field].trim(),
                      })
                    : null
                }
                onChange={(next) => setDraft({ ...draft, actionMap: next })}
              />
            </>
          )}
          {errors["@values"] && (
            <p className="fm-note is-crit" role="alert">
              {errors["@values"]}
            </p>
          )}
          {changes.values.length > 0 && changesBar("values")}
          {confirmation("values")}
        </section>
      )}

      {profile && (
        <section aria-labelledby="fm-collection-h" className="fm-section">
          <h2 id="fm-collection-h">{t("collectionTitle")}</h2>
          <p className="fm-state-line">
            <strong>{cState?.label}</strong>
            {profile.validated_at && vState?.kind === "valid" && (
              <span className="muted">
                {" · "}
                {t.rich("validatedLine", {
                  version: profile.profile_version,
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
            <MappingHistory
              schema={formSch}
              entries={historyForTable(history)}
              actionLabel={(action) => ipsHistoryActionLabel(action, i18n)}
              outcomeNote={(entry) => ipsOutcomeNote(entry, i18n)}
            />
          )}
        </section>
      )}
    </>
  );
}
