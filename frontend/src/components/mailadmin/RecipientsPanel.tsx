"use client";

// Who receives alert e-mail. Stored recipients are edited here; the addresses in
// `NOTIFY_EMAIL_RECIPIENTS` in `.env` are listed apart and cannot be changed. A site with neither
// sends no alert e-mail, and the page says so instead of showing an empty table.

import { useTranslations } from "next-intl";
import { useId, useState, type FormEvent } from "react";

import { errorTexts } from "@/components/auth/reasons";
import { TableScroll } from "@/components/ui/TableScroll";
import { UtcTime } from "@/components/ui/UtcTime";
import { useEnumLabels } from "@/i18n/enumLabels";
import {
  ApiError,
  mailAdminApi,
  type EmailAlertType,
  type EmailRecipient,
  type EmailRecipientBody,
  type EmailRecipientList,
} from "@/lib/api";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";

import { recipientErrors, type RecipientFieldErrors } from "./mail";
import { PartError } from "./PartError";
import type { Part } from "./usePart";

const SEVERITIES: EmailRecipientBody["min_severity"][] = ["INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"];

interface FormState {
  /** Null while adding. */
  id: string | null;
  address: string;
  minSeverity: EmailRecipientBody["min_severity"];
  everyType: boolean;
  types: string[];
  enabled: boolean;
}

const BLANK: FormState = {
  id: null,
  address: "",
  minSeverity: "LOW",
  everyType: true,
  types: [],
  enabled: true,
};

export function RecipientsPanel({
  part,
  alertTypes,
  onChanged,
  onRetry,
}: {
  part: Part<EmailRecipientList>;
  /** Null when the template list, which carries them, could not be read. */
  alertTypes: readonly EmailAlertType[] | null;
  onChanged: () => void;
  onRetry: () => void;
}) {
  const t = useTranslations("mailAdmin.recipients");
  const headingId = useId();
  return (
    <section className="mail-section" aria-labelledby={headingId}>
      <h2 className="mail-h" id={headingId}>
        {t("heading")}
      </h2>
      {part.status === "loading" ? (
        <p className="notice" role="status" aria-live="polite">
          {t("loading")}
        </p>
      ) : null}
      {part.status === "error" ? (
        <PartError
          what={t("what")}
          httpStatus={part.httpStatus}
          retryAfter={part.retryAfter}
          onRetry={onRetry}
        />
      ) : null}
      {part.status === "ok" ? (
        <Loaded list={part.data} alertTypes={alertTypes} onChanged={onChanged} />
      ) : null}
    </section>
  );
}

function typeLabel(key: string, alertTypes: readonly EmailAlertType[] | null): string {
  return alertTypes?.find((type) => type.key === key)?.label ?? key;
}

function Loaded({
  list,
  alertTypes,
  onChanged,
}: {
  list: EmailRecipientList;
  alertTypes: readonly EmailAlertType[] | null;
  onChanged: () => void;
}) {
  const t = useTranslations("mailAdmin.recipients");
  const { label } = useEnumLabels();
  const { t: say, locale } = useBehaviorI18n();
  const [form, setForm] = useState<FormState | null>(null);
  const [removing, setRemoving] = useState<EmailRecipient | null>(null);
  const [removeLines, setRemoveLines] = useState<string[]>([]);
  const [working, setWorking] = useState(false);
  const [errors, setErrors] = useState<RecipientFieldErrors | null>(null);

  const active = list.recipients.filter((item) => item.enabled);
  const nobody = active.length === 0 && list.from_env.length === 0;

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!form) return;
    const body: EmailRecipientBody = {
      address: form.address.trim(),
      min_severity: form.minSeverity,
      alert_types: form.everyType ? null : form.types,
      enabled: form.enabled,
    };
    setWorking(true);
    setErrors(null);
    try {
      if (form.id === null) await mailAdminApi.addRecipient(body);
      else await mailAdminApi.saveRecipient(form.id, body);
      setForm(null);
      onChanged();
    } catch (err) {
      setErrors(
        err instanceof ApiError
          ? recipientErrors(err, locale, say, errorTexts(err, say, locale))
          : { address: [], types: [], general: errorTexts(err, say, locale) },
      );
    } finally {
      setWorking(false);
    }
  }

  async function remove() {
    if (!removing) return;
    setWorking(true);
    setRemoveLines([]);
    try {
      await mailAdminApi.removeRecipient(removing.id);
      setRemoving(null);
      onChanged();
    } catch (err) {
      // A recipient that is already gone is the state the removal asked for.
      if (err instanceof ApiError && err.status === 404) {
        setRemoving(null);
        onChanged();
      } else {
        setRemoveLines(errorTexts(err, say, locale));
      }
    } finally {
      setWorking(false);
    }
  }

  function edit(item: EmailRecipient) {
    setRemoving(null);
    setErrors(null);
    setForm({
      id: item.id,
      address: item.address,
      minSeverity: item.min_severity,
      everyType: item.alert_types === null,
      types: item.alert_types ?? [],
      enabled: item.enabled,
    });
  }

  return (
    <>
      {nobody ? (
        <div className="banner warn" role="status">
          <div>
            <p>
              <strong>{t("nobody.heading")}</strong>
            </p>
            <p>
              {list.recipients.length === 0
                ? t.rich("nobody.noneStored", { code: (chunks) => <code>{chunks}</code> })
                : t.rich("nobody.allOff", { code: (chunks) => <code>{chunks}</code> })}
            </p>
          </div>
        </div>
      ) : null}

      {list.recipients.length > 0 ? (
        <TableScroll label={t("tableLabel")}>
          <table className="mail-table">
            <caption className="sr-only">{t("tableLabel")}</caption>
            <thead>
              <tr>
                <th scope="col">{t("col.address")}</th>
                <th scope="col">{t("col.severity")}</th>
                <th scope="col">{t("col.types")}</th>
                <th scope="col">{t("col.enabled")}</th>
                <th scope="col">{t("col.updated")}</th>
                <th scope="col">
                  <span className="sr-only">{t("col.actions")}</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {list.recipients.map((item) => (
                <tr key={item.id}>
                  <td className="cell-name">
                    <span className="mail-mono">{item.address}</span>
                  </td>
                  <td>{label("severity", item.min_severity)}</td>
                  <td>
                    {item.alert_types === null
                      ? t("everyType")
                      : item.alert_types.map((key) => typeLabel(key, alertTypes)).join(", ")}
                  </td>
                  <td>{item.enabled ? t("yes") : t("no")}</td>
                  <td className="num">
                    <UtcTime value={item.updated_at} variant="compact" wrapZone />
                    {item.updated_by ? <span className="cell-sub">{item.updated_by}</span> : null}
                  </td>
                  <td className="mail-row-actions">
                    <button
                      type="button"
                      className="action secondary"
                      aria-label={t("editNamed", { address: item.address })}
                      disabled={working}
                      onClick={() => edit(item)}
                    >
                      {t("edit")}
                    </button>
                    <button
                      type="button"
                      className="action secondary"
                      aria-label={t("removeNamed", { address: item.address })}
                      disabled={working}
                      onClick={() => {
                        setForm(null);
                        setRemoving(item);
                        setRemoveLines([]);
                      }}
                    >
                      {t("remove")}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableScroll>
      ) : (
        <p className="mail-hint">{t("noneStored")}</p>
      )}

      {removing ? (
        <div className="banner warn" role="alert">
          <div>
            <p>
              <strong>{t("removeHeading", { address: removing.address })}</strong>
            </p>
            <p>{t("removeBody")}</p>
            {removeLines.length > 0 ? (
              <ul className="mail-lines">
                {removeLines.map((line, index) => (
                  <li key={`${index}-${line}`}>{line}</li>
                ))}
              </ul>
            ) : null}
            <div className="actions">
              <button type="button" className="action" disabled={working} onClick={() => void remove()}>
                {working ? t("removing") : t("removeConfirm")}
              </button>
              <button
                type="button"
                className="action secondary"
                disabled={working}
                onClick={() => setRemoving(null)}
              >
                {t("removeKeep")}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {form ? (
        <RecipientForm
          form={form}
          alertTypes={alertTypes}
          errors={errors}
          working={working}
          onChange={(change) => setForm({ ...form, ...change })}
          onSubmit={(event) => void save(event)}
          onCancel={() => {
            setForm(null);
            setErrors(null);
          }}
        />
      ) : (
        <div className="actions">
          <button
            type="button"
            className="action"
            onClick={() => {
              setErrors(null);
              setRemoving(null);
              setForm(BLANK);
            }}
          >
            {t("add")}
          </button>
        </div>
      )}

      <div className="mail-env">
        <h3 className="mail-h3">{t("env.heading")}</h3>
        {list.from_env.length === 0 ? (
          <p className="mail-hint">
            {t.rich("env.none", { code: (chunks) => <code>{chunks}</code> })}
          </p>
        ) : (
          <>
            <p className="mail-hint">
              {t.rich("env.note", { code: (chunks) => <code>{chunks}</code> })}
            </p>
            <ul className="mail-env-list" aria-label={t("env.heading")}>
              {list.from_env.map((address) => (
                <li key={address}>
                  <span className="mail-mono">{address}</span>
                </li>
              ))}
            </ul>
          </>
        )}
      </div>
    </>
  );
}

function RecipientForm({
  form,
  alertTypes,
  errors,
  working,
  onChange,
  onSubmit,
  onCancel,
}: {
  form: FormState;
  alertTypes: readonly EmailAlertType[] | null;
  errors: RecipientFieldErrors | null;
  working: boolean;
  onChange: (change: Partial<FormState>) => void;
  onSubmit: (event: FormEvent) => void;
  onCancel: () => void;
}) {
  const t = useTranslations("mailAdmin.recipients");
  const { label } = useEnumLabels();
  const ids = { address: useId(), severity: useId(), enabled: useId() };
  const addressErrors = errors?.address ?? [];
  const typeErrors = errors?.types ?? [];

  function toggle(key: string, on: boolean) {
    onChange({ types: on ? [...form.types, key] : form.types.filter((item) => item !== key) });
  }

  return (
    <form
      className="mail-form mail-recipient-form"
      onSubmit={onSubmit}
      aria-label={form.id === null ? t("form.addLabel") : t("form.editLabel")}
    >
      <h3 className="mail-h3">{form.id === null ? t("form.addHeading") : t("form.editHeading")}</h3>
      <div className="mail-field">
        <label htmlFor={ids.address}>{t("form.address")}</label>
        <input
          id={ids.address}
          value={form.address}
          inputMode="email"
          autoComplete="off"
          spellCheck={false}
          aria-invalid={addressErrors.length > 0 ? true : undefined}
          aria-describedby={addressErrors.length > 0 ? `${ids.address}-err` : undefined}
          onChange={(event) => onChange({ address: event.target.value })}
        />
        {addressErrors.length > 0 ? (
          <p className="mail-field-error" id={`${ids.address}-err`} role="alert">
            {addressErrors.join(" ")}
          </p>
        ) : null}
      </div>
      <div className="mail-field mail-field-inline">
        <label htmlFor={ids.severity}>{t("form.severity")}</label>
        <select
          id={ids.severity}
          className="select-control"
          value={form.minSeverity}
          onChange={(event) =>
            onChange({ minSeverity: event.target.value as FormState["minSeverity"] })
          }
        >
          {SEVERITIES.map((value) => (
            <option key={value} value={value}>
              {label("severity", value)}
            </option>
          ))}
        </select>
        <p className="mail-hint">{t("form.severityHint")}</p>
      </div>
      <fieldset className="mail-fieldset">
        <legend>{t("form.types")}</legend>
        <label className="mail-radio">
          <input
            type="radio"
            name="mail-scope"
            checked={form.everyType}
            onChange={() => onChange({ everyType: true })}
          />
          <span>{t("form.everyType")}</span>
        </label>
        <label className="mail-radio">
          <input
            type="radio"
            name="mail-scope"
            checked={!form.everyType}
            disabled={alertTypes === null}
            onChange={() => onChange({ everyType: false })}
          />
          <span>{t("form.onlyTypes")}</span>
        </label>
        {alertTypes === null ? <p className="mail-hint">{t("form.typesUnavailable")}</p> : null}
        {!form.everyType && alertTypes !== null ? (
          <div className="mail-types" role="group" aria-label={t("form.typesGroup")}>
            {alertTypes.map((type) => (
              <label className="mail-radio" key={type.key}>
                <input
                  type="checkbox"
                  checked={form.types.includes(type.key)}
                  onChange={(event) => toggle(type.key, event.target.checked)}
                />
                <span>{type.label}</span>
              </label>
            ))}
          </div>
        ) : null}
        {!form.everyType && form.types.length === 0 ? (
          <p className="mail-hint">{t("form.typesNone")}</p>
        ) : null}
        {typeErrors.length > 0 ? (
          <p className="mail-field-error" role="alert">
            {typeErrors.join(" ")}
          </p>
        ) : null}
      </fieldset>
      <div className="mail-check">
        <input
          id={ids.enabled}
          type="checkbox"
          checked={form.enabled}
          onChange={(event) => onChange({ enabled: event.target.checked })}
        />
        <label htmlFor={ids.enabled}>{t("form.enabled")}</label>
      </div>
      {errors && errors.general.length > 0 ? (
        <div className="banner warn" role="alert">
          <div>
            <p>
              <strong>{t("form.notSaved")}</strong>
            </p>
            <ul className="mail-lines">
              {errors.general.map((line, index) => (
                <li key={`${index}-${line}`}>{line}</li>
              ))}
            </ul>
          </div>
        </div>
      ) : null}
      <div className="actions">
        <button
          type="submit"
          className="action"
          disabled={working || form.address.trim() === "" || (!form.everyType && form.types.length === 0)}
        >
          {working ? t("form.saving") : form.id === null ? t("form.add") : t("form.save")}
        </button>
        <button type="button" className="action secondary" disabled={working} onClick={onCancel}>
          {t("form.cancel")}
        </button>
      </div>
    </form>
  );
}
