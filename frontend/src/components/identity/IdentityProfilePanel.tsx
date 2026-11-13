"use client";

// The profile of one identity source: its field mapping, what validation found, and, for a caller
// who holds `admin:identity`, Set / Validate / Enable / Disable.
//
// The mapping names QRadar properties, never a username. Nothing is written until the operator
// presses a button, and each button says what it does first. A refusal is shown as the server
// worded it (a coded sentence in the interface language); the operator's own text is never echoed
// back by the server, so none is here either. Without the grant the profile is read-only and the
// panel names the permission.

import { useId, useState } from "react";
import { useTranslations } from "next-intl";

import { UtcTime } from "@/components/ui/UtcTime";
import { useBackendMessage } from "@/i18n/backendMessage";
import { ApiError, actionErrorMessage, identityApi, type IdentityProfile } from "@/lib/api";
import { asTextTranslator } from "@/i18n/textTranslator";
import { serverMessage } from "@/lib/fieldMapping";
import { permissionOf } from "@/lib/suppression";

import { ADMIN_PERMISSION, IDENTITY_EVENTS, eventKey, notCollectedWhy } from "./identityFormat";

import "@/components/ui/controls.css";
import "./identity.css";

type Busy = "save" | "validate" | "enable" | "disable" | null;
type Msg = { tone: "ok" | "crit" | "warn"; text: string } | null;

interface Draft {
  event_id_property: string;
  username_property: string;
  logon_type_property: string;
  product_label: string;
  enabled_event_ids: number[];
}

function draftOf(p: IdentityProfile | null): Draft {
  return {
    event_id_property: p?.event_id_property ?? "",
    username_property: p?.username_property ?? "username",
    logon_type_property: p?.logon_type_property ?? "",
    product_label: p?.product_label ?? "",
    enabled_event_ids: p ? [...p.enabled_event_ids] : [...IDENTITY_EVENTS],
  };
}

export function IdentityProfilePanel({
  logSourceId,
  profile: initial,
  canAdmin,
}: {
  logSourceId: string;
  profile: IdentityProfile | null;
  canAdmin: boolean;
}) {
  const t = useTranslations("identity.profile");
  const events = useTranslations("identity.events");
  const say = asTextTranslator(useTranslations());
  const backendMessage = useBackendMessage();
  const [profile, setProfile] = useState<IdentityProfile | null>(initial);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Draft>(() => draftOf(initial));
  const [busy, setBusy] = useState<Busy>(null);
  const [msg, setMsg] = useState<Msg>(null);
  const base = useId();
  const ids = {
    eventId: `${base}-event`,
    user: `${base}-user`,
    logon: `${base}-logon`,
    label: `${base}-label`,
    events: `${base}-events`,
  };

  const fail = (err: unknown) => {
    if (err instanceof ApiError && err.status === 403) {
      setMsg({
        tone: "crit",
        text: t("forbidden", { permission: permissionOf(err) ?? ADMIN_PERMISSION }),
      });
    } else if (err instanceof ApiError && (err.status === 422 || err.status === 409)) {
      setMsg({ tone: "warn", text: serverMessage(err, backendMessage) || t("rejected") });
    } else {
      setMsg({ tone: "crit", text: t("failed", { reason: actionErrorMessage(err, say) }) });
    }
  };

  async function run(kind: Exclude<Busy, null>, call: () => Promise<IdentityProfile>, done: string) {
    setBusy(kind);
    setMsg(null);
    try {
      const next = await call();
      setProfile(next);
      setDraft(draftOf(next));
      setEditing(false);
      setMsg({ tone: "ok", text: done });
    } catch (err) {
      fail(err);
    } finally {
      setBusy(null);
    }
  }

  const why = profile ? notCollectedWhy(profile) : null;
  // Enable needs the current version validated, whatever else `why` leads with.
  const valid =
    profile !== null &&
    profile.validation_status === "VALID" &&
    profile.validated_profile_version === profile.profile_version;
  const toggle = (id: number) =>
    setDraft((d) => ({
      ...d,
      enabled_event_ids: d.enabled_event_ids.includes(id)
        ? d.enabled_event_ids.filter((x) => x !== id)
        : [...d.enabled_event_ids, id],
    }));
  const draftOk =
    draft.event_id_property.trim() !== "" &&
    draft.username_property.trim() !== "" &&
    draft.enabled_event_ids.length > 0;

  return (
    <section className="stack-6 identity-profile" aria-labelledby="identity-profile-title">
      <h2 className="section-title" id="identity-profile-title">
        {t("heading")}
      </h2>

      {profile === null ? (
        <div className="notice" role="status">
          <p>
            <strong>{t("noneTitle")}</strong>
          </p>
          <p>{canAdmin ? t("noneAdmin") : t("noneReader", { permission: ADMIN_PERMISSION })}</p>
        </div>
      ) : (
        <table className="kv-table" aria-labelledby="identity-profile-title">
          <tbody>
            <tr>
              <th scope="row">{t("state")}</th>
              <td>
                <span className={`pill pill-strong ${why === null ? "ok" : "warn"}`}>
                  {why === null ? t("collected") : t(`why.${why}`)}
                </span>
              </td>
            </tr>
            <tr>
              <th scope="row">{t("validation")}</th>
              <td>
                {t(`status.${profile.validation_status}` as never)}
                <span className="cell-sub">
                  {profile.validated_profile_version === null
                    ? t("neverValidated", { version: profile.profile_version })
                    : t("validatedFor", {
                        validated: profile.validated_profile_version,
                        version: profile.profile_version,
                      })}
                </span>
                {profile.validated_at && (
                  <span className="cell-sub">
                    <UtcTime value={profile.validated_at} />
                  </span>
                )}
              </td>
            </tr>
            <tr>
              <th scope="row">{t("eventIdProperty")}</th>
              <td className="identity-name">{profile.event_id_property}</td>
            </tr>
            <tr>
              <th scope="row">{t("usernameProperty")}</th>
              <td className="identity-name">{profile.username_property}</td>
            </tr>
            <tr>
              <th scope="row">{t("logonTypeProperty")}</th>
              <td className="identity-name">{profile.logon_type_property ?? t("notMapped")}</td>
            </tr>
            <tr>
              <th scope="row">{t("enabledEvents")}</th>
              <td>
                <ul className="identity-events">
                  {profile.enabled_event_ids.map((id) => (
                    <li key={id}>
                      <span className="num">{id}</span> {events(eventKey(id))}
                    </li>
                  ))}
                </ul>
              </td>
            </tr>
          </tbody>
        </table>
      )}

      {msg && (
        <p
          className={`fm-banner is-${msg.tone}`}
          role={msg.tone === "ok" ? "status" : "alert"}
        >
          {msg.text}
        </p>
      )}

      {!canAdmin ? (
        // With no profile the notice above already names the permission.
        profile !== null && <p className="subtitle">{t("readOnly", { permission: ADMIN_PERMISSION })}</p>
      ) : (
        <>
          {editing || profile === null ? (
            <form
              className="rule-form" aria-label={t("formLabel")}
              onSubmit={(e) => {
                e.preventDefault();
                if (!draftOk) return;
                void run(
                  "save",
                  () =>
                    identityApi.saveProfile(logSourceId, {
                      event_id_property: draft.event_id_property.trim(),
                      username_property: draft.username_property.trim(),
                      logon_type_property: draft.logon_type_property.trim() || null,
                      enabled_event_ids: IDENTITY_EVENTS.filter((id) =>
                        draft.enabled_event_ids.includes(id),
                      ),
                      product_label: draft.product_label.trim() || null,
                    }),
                  t("saved"),
                );
              }}
            >
              <div className="rule-form-grid">
                <span className="outage-form-label">
                  <label htmlFor={ids.eventId}>{t("eventIdProperty")}</label>
                </span>
                <div>
                  <input
                    id={ids.eventId}
                    className="field"
                    value={draft.event_id_property}
                    onChange={(e) => setDraft({ ...draft, event_id_property: e.target.value })}
                    autoComplete="off"
                    required
                  />
                  <p className="field-hint">{t("eventIdHelp")}</p>
                </div>
                <span className="outage-form-label">
                  <label htmlFor={ids.user}>{t("usernameProperty")}</label>
                </span>
                <div>
                  <input
                    id={ids.user}
                    className="field"
                    value={draft.username_property}
                    onChange={(e) => setDraft({ ...draft, username_property: e.target.value })}
                    autoComplete="off"
                    required
                  />
                </div>
                <span className="outage-form-label">
                  <label htmlFor={ids.logon}>{t("logonTypeProperty")}</label>
                </span>
                <div>
                  <input
                    id={ids.logon}
                    className="field"
                    value={draft.logon_type_property}
                    onChange={(e) => setDraft({ ...draft, logon_type_property: e.target.value })}
                    autoComplete="off"
                  />
                </div>
                <span className="outage-form-label">
                  <label htmlFor={ids.label}>{t("productLabel")}</label>
                </span>
                <div>
                  <input
                    id={ids.label}
                    className="field"
                    value={draft.product_label}
                    onChange={(e) => setDraft({ ...draft, product_label: e.target.value })}
                    autoComplete="off"
                  />
                </div>
                <span className="outage-form-label" id={ids.events}>
                  {t("enabledEvents")}
                </span>
                <div className="fm-radios" role="group" aria-labelledby={ids.events}>
                  {IDENTITY_EVENTS.map((id) => (
                    <label key={id} className="fm-radio">
                      <input
                        type="checkbox"
                        checked={draft.enabled_event_ids.includes(id)}
                        onChange={() => toggle(id)}
                      />
                      <span>
                        <span className="num">{id}</span> {events(eventKey(id))}
                      </span>
                    </label>
                  ))}
                </div>
              </div>
              <p className="subtitle">{t("setNote")}</p>
              <div className="identity-actions">
                <button type="submit" className="action" disabled={!draftOk || busy !== null}>
                  {busy === "save" ? t("saving") : t("set")}
                </button>
                {profile !== null && (
                  <button
                    type="button"
                    className="action secondary"
                    disabled={busy !== null}
                    onClick={() => {
                      setEditing(false);
                      setDraft(draftOf(profile));
                    }}
                  >
                    {t("cancel")}
                  </button>
                )}
              </div>
            </form>
          ) : (
            <div className="identity-actions">
              <button type="button" className="action secondary" onClick={() => setEditing(true)}>
                {t("edit")}
              </button>
              <button
                type="button"
                className="action secondary"
                disabled={busy !== null}
                onClick={() =>
                  void run("validate", () => identityApi.validate(logSourceId), t("validated"))
                }
              >
                {busy === "validate" ? t("validating") : t("validate")}
              </button>
              {profile.enabled ? (
                <button
                  type="button"
                  className="action secondary"
                  disabled={busy !== null}
                  onClick={() =>
                    void run("disable", () => identityApi.disable(logSourceId), t("disabled"))
                  }
                >
                  {busy === "disable" ? t("disabling") : t("disable")}
                </button>
              ) : (
                <button
                  type="button"
                  className="action"
                  disabled={busy !== null || !valid}
                  onClick={() =>
                    void run("enable", () => identityApi.enable(logSourceId), t("enabled"))
                  }
                >
                  {busy === "enable" ? t("enabling") : t("enable")}
                </button>
              )}
            </div>
          )}
          {profile !== null && !editing && (
            <p className="subtitle">
              {profile.enabled ? t("disableNote") : valid ? t("enableNote") : t("enableBlocked")}
            </p>
          )}
        </>
      )}
    </section>
  );
}
