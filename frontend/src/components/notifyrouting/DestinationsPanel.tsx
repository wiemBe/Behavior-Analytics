"use client";

// The webhook destinations: a table, a send-test per row, and one form for add and edit. The URL,
// the secret and the header values are write-only. They go out in the request and are never put
// back into a field: an edit shows the stored scheme://host and an explicit replace control.

import { useTranslations } from "next-intl";
import { useId, useState, type FormEvent } from "react";

import { errorTexts } from "@/components/auth/reasons";
import type { Part } from "@/components/mailadmin/usePart";
import { TableScroll } from "@/components/ui/TableScroll";
import { UtcTime } from "@/components/ui/UtcTime";
import { renderBackendMessage } from "@/i18n/backendMessage";
import {
  ApiError,
  notificationRoutingApi,
  type DestinationTestResult,
  type NotificationDestination,
} from "@/lib/api";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";

import { PartError } from "./PartError";
import {
  BLANK_DESTINATION,
  blankHeaders,
  MAX_DESTINATIONS,
  destinationBody,
  type DestinationForm,
  type SecretMode,
} from "./routing";

type TestView =
  | { kind: "sending"; id: string }
  | { kind: "outcome"; id: string; name: string; result: DestinationTestResult }
  | { kind: "error"; id: string; name: string; lines: string[] };

export function DestinationsPanel({
  part,
  onChanged,
  onRetry,
}: {
  part: Part<NotificationDestination[]>;
  onChanged: () => void;
  onRetry: () => void;
}) {
  const t = useTranslations("notifyRouting.destinations");
  const headingId = useId();
  const count = part.status === "ok" ? part.data.length : 0;
  return (
    <section className="nr-section" aria-labelledby={headingId}>
      <h2 className="nr-h" id={headingId}>
        {t("heading", { count, max: MAX_DESTINATIONS })}
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
      {part.status === "ok" ? <Loaded list={part.data} onChanged={onChanged} /> : null}
    </section>
  );
}

function formFrom(item: NotificationDestination): DestinationForm {
  return {
    id: item.id,
    name: item.name,
    enabled: item.enabled,
    url: "",
    allowHttp: item.allow_http,
    allowPrivate: item.allow_private,
    secretMode: "keep",
    secret: "",
    storedAddress: item.url_display,
    secretSet: item.secret_set,
    storedHeaderNames: item.header_names,
    replaceHeaders: false,
    headers: blankHeaders(),
  };
}

function Loaded({ list, onChanged }: { list: NotificationDestination[]; onChanged: () => void }) {
  const t = useTranslations("notifyRouting.destinations");
  const { t: say, locale } = useBehaviorI18n();
  const [form, setForm] = useState<DestinationForm | null>(null);
  const [formErrors, setFormErrors] = useState<string[]>([]);
  const [removing, setRemoving] = useState<NotificationDestination | null>(null);
  const [removeLines, setRemoveLines] = useState<string[]>([]);
  const [test, setTest] = useState<TestView | null>(null);
  const [working, setWorking] = useState(false);
  const full = list.length >= MAX_DESTINATIONS;

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!form) return;
    setWorking(true);
    setFormErrors([]);
    try {
      const body = destinationBody(form);
      if (form.id === null) await notificationRoutingApi.createDestination(body);
      else await notificationRoutingApi.saveDestination(form.id, body);
      setForm(null);
      onChanged();
    } catch (err) {
      setFormErrors(errorTexts(err, say, locale));
    } finally {
      setWorking(false);
    }
  }

  async function remove() {
    if (!removing) return;
    setWorking(true);
    setRemoveLines([]);
    try {
      await notificationRoutingApi.deleteDestination(removing.id);
      setRemoving(null);
      onChanged();
    } catch (err) {
      // A destination that is already gone is the state the deletion asked for.
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

  async function sendTest(item: NotificationDestination) {
    setTest({ kind: "sending", id: item.id });
    try {
      const result = await notificationRoutingApi.testDestination(item.id);
      setTest({ kind: "outcome", id: item.id, name: item.name, result });
      onChanged();
    } catch (err) {
      setTest({ kind: "error", id: item.id, name: item.name, lines: errorTexts(err, say, locale) });
    }
  }

  return (
    <>
      <p className="nr-hint">{t("intro")}</p>
      {list.length === 0 ? (
        <p className="notice">{t("empty")}</p>
      ) : (
        <TableScroll label={t("tableLabel")}>
          <table className="nr-table">
            <caption className="sr-only">{t("tableLabel")}</caption>
            <thead>
              <tr>
                <th scope="col">{t("col.name")}</th>
                <th scope="col">{t("col.address")}</th>
                <th scope="col">{t("col.options")}</th>
                <th scope="col">{t("col.test")}</th>
                <th scope="col">
                  <span className="sr-only">{t("col.actions")}</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {list.map((item) => (
                <tr key={item.id}>
                  <td className="cell-name">
                    {item.name}
                    {item.enabled ? null : <span className="pill nr-pill">{t("disabled")}</span>}
                  </td>
                  <td>
                    <span className="nr-mono">{item.url_display}</span>
                  </td>
                  <td>
                    <ul className="nr-flags">
                      {item.allow_http ? (
                        <li>
                          <span className="pill warn">{t("httpAllowed")}</span>
                        </li>
                      ) : null}
                      {item.allow_private ? (
                        <li>
                          <span className="pill warn">{t("privateAllowed")}</span>
                        </li>
                      ) : null}
                      <li>{item.secret_set ? t("signed") : t("unsigned")}</li>
                      <li>
                        {item.header_names.length > 0
                          ? t("headers", { names: item.header_names.join(", ") })
                          : t("noHeaders")}
                      </li>
                    </ul>
                  </td>
                  <td className="num">
                    <LastTest item={item} />
                  </td>
                  <td className="nr-actions">
                    <button
                      type="button"
                      className="action secondary"
                      aria-label={t("testNamed", { name: item.name })}
                      disabled={working || test?.kind === "sending"}
                      onClick={() => void sendTest(item)}
                    >
                      {test?.kind === "sending" && test.id === item.id ? t("testing") : t("test")}
                    </button>
                    <button
                      type="button"
                      className="action secondary"
                      aria-label={t("editNamed", { name: item.name })}
                      disabled={working}
                      onClick={() => {
                        setRemoving(null);
                        setFormErrors([]);
                        setForm(formFrom(item));
                      }}
                    >
                      {t("edit")}
                    </button>
                    <button
                      type="button"
                      className="action secondary"
                      aria-label={t("deleteNamed", { name: item.name })}
                      disabled={working}
                      onClick={() => {
                        setForm(null);
                        setRemoveLines([]);
                        setRemoving(item);
                      }}
                    >
                      {t("delete")}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableScroll>
      )}

      {test && test.kind !== "sending" ? <TestOutcome view={test} /> : null}

      {removing ? (
        <div className="banner warn" role="alert">
          <div>
            <p>
              <strong>{t("deleteHeading", { name: removing.name })}</strong>
            </p>
            <p>{t("deleteBody")}</p>
            {removeLines.length > 0 ? (
              <ul className="nr-lines">
                {removeLines.map((line, index) => (
                  <li key={`${index}-${line}`}>{line}</li>
                ))}
              </ul>
            ) : null}
            <div className="actions">
              <button type="button" className="action" disabled={working} onClick={() => void remove()}>
                {working ? t("deleting") : t("deleteConfirm")}
              </button>
              <button
                type="button"
                className="action secondary"
                disabled={working}
                onClick={() => setRemoving(null)}
              >
                {t("keep")}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {form ? (
        <DestinationFormView
          form={form}
          errors={formErrors}
          working={working}
          onChange={(change) => setForm({ ...form, ...change })}
          onSubmit={(event) => void save(event)}
          onCancel={() => {
            setForm(null);
            setFormErrors([]);
          }}
        />
      ) : (
        <div className="actions">
          <button
            type="button"
            className="action"
            disabled={full}
            aria-describedby={full ? "nr-destination-limit" : undefined}
            onClick={() => {
              setRemoving(null);
              setFormErrors([]);
              setForm(BLANK_DESTINATION);
            }}
          >
            {t("add")}
          </button>
          {full ? (
            <p className="nr-hint" id="nr-destination-limit">
              {t("limit", { max: MAX_DESTINATIONS })}
            </p>
          ) : null}
        </div>
      )}
    </>
  );
}

function LastTest({ item }: { item: NotificationDestination }) {
  const t = useTranslations("notifyRouting.destinations");
  if (item.last_test_at === null) return <span className="muted">{t("notTested")}</span>;
  const result = item.last_test_class ?? "";
  const failed = result !== "ok";
  const known = ["ok", "transport", "timeout", "http_status", "ssrf_refused", "redirect_refused"];
  return (
    <>
      <span className={failed ? "pill crit" : "pill ok"}>
        {known.includes(result)
          ? t(`testClass.${result}` as "testClass.ok")
          : result}
        {item.last_test_status !== null ? ` ${item.last_test_status}` : ""}
      </span>
      <span className="cell-sub">
        <UtcTime value={item.last_test_at} variant="compact" wrapZone />
      </span>
    </>
  );
}

function TestOutcome({ view }: { view: Exclude<TestView, { kind: "sending" }> }) {
  const t = useTranslations("notifyRouting.destinations.outcome");
  const { t: say, locale } = useBehaviorI18n();
  if (view.kind === "error") {
    return (
      <div className="banner warn" role="alert">
        <div>
          <p>
            <strong>{t("notSent", { name: view.name })}</strong>
          </p>
          <ul className="nr-lines">
            {view.lines.map((line, index) => (
              <li key={`${index}-${line}`}>{line}</li>
            ))}
          </ul>
        </div>
      </div>
    );
  }
  const { result } = view;
  const sentence = renderBackendMessage(
    { text: result.outcome.message, code: result.outcome.code, params: result.outcome.params },
    locale,
    say,
  );
  return (
    <div className={result.ok ? "banner ok" : "banner crit"} role={result.ok ? "status" : "alert"}>
      <div>
        <p>
          <strong>
            {result.ok ? t("okHeading", { name: view.name }) : t("failedHeading", { name: view.name })}
          </strong>
        </p>
        <p>{sentence}</p>
        <p>{t(`next.${result.result}` as "next.ok")}</p>
        <p className="nr-code-line">
          {t("code", {
            result: result.status === null ? result.result : `${result.result} ${result.status}`,
          })}
        </p>
      </div>
    </div>
  );
}

function DestinationFormView({
  form,
  errors,
  working,
  onChange,
  onSubmit,
  onCancel,
}: {
  form: DestinationForm;
  errors: string[];
  working: boolean;
  onChange: (change: Partial<DestinationForm>) => void;
  onSubmit: (event: FormEvent) => void;
  onCancel: () => void;
}) {
  const t = useTranslations("notifyRouting.destinations.form");
  const ids = {
    name: useId(),
    url: useId(),
    enabled: useId(),
    http: useId(),
    priv: useId(),
    secret: useId(),
  };
  const creating = form.id === null;
  const canSubmit =
    form.name.trim() !== "" &&
    (!creating || form.url.trim() !== "") &&
    (form.secretMode !== "replace" || form.secret !== "" || creating);

  function setHeader(index: number, change: Partial<{ name: string; value: string }>) {
    const rows = form.headers.map((row, at) => (at === index ? { ...row, ...change } : row));
    onChange({ headers: rows });
  }
  const showHeaderRows = creating || form.replaceHeaders;

  return (
    <form
      className="nr-form"
      onSubmit={onSubmit}
      aria-label={creating ? t("addLabel") : t("editLabel")}
      autoComplete="off"
    >
      <h3 className="nr-h3">{creating ? t("addHeading") : t("editHeading", { name: form.name })}</h3>
      <div className="nr-field">
        <label htmlFor={ids.name}>{t("name")}</label>
        <input
          id={ids.name}
          value={form.name}
          maxLength={64}
          onChange={(event) => onChange({ name: event.target.value })}
        />
      </div>
      <div className="nr-field">
        <label htmlFor={ids.url}>{creating ? t("url") : t("urlReplace")}</label>
        {creating ? null : (
          <p className="nr-hint nr-current">{t("urlCurrent", { address: form.storedAddress })}</p>
        )}
        <input
          id={ids.url}
          value={form.url}
          inputMode="url"
          spellCheck={false}
          autoComplete="off"
          maxLength={2048}
          aria-describedby={`${ids.url}-hint`}
          onChange={(event) => onChange({ url: event.target.value })}
        />
        <p className="nr-hint" id={`${ids.url}-hint`}>
          {creating ? t("urlHint") : t("urlReplaceHint")}
        </p>
      </div>
      <div className="nr-check">
        <input
          id={ids.enabled}
          type="checkbox"
          checked={form.enabled}
          onChange={(event) => onChange({ enabled: event.target.checked })}
        />
        <label htmlFor={ids.enabled}>{t("enabled")}</label>
      </div>
      <div className="nr-flag-field">
        <div className="nr-check">
          <input
            id={ids.http}
            type="checkbox"
            checked={form.allowHttp}
            aria-describedby={`${ids.http}-warn`}
            onChange={(event) => onChange({ allowHttp: event.target.checked })}
          />
          <label htmlFor={ids.http}>{t("allowHttp")}</label>
        </div>
        <p className="nr-hint" id={`${ids.http}-warn`}>
          {t("allowHttpWarn")}
        </p>
      </div>
      <div className="nr-flag-field">
        <div className="nr-check">
          <input
            id={ids.priv}
            type="checkbox"
            checked={form.allowPrivate}
            aria-describedby={`${ids.priv}-warn`}
            onChange={(event) => onChange({ allowPrivate: event.target.checked })}
          />
          <label htmlFor={ids.priv}>{t("allowPrivate")}</label>
        </div>
        <p className="nr-hint" id={`${ids.priv}-warn`}>
          {t("allowPrivateWarn")}
        </p>
      </div>

      <fieldset className="nr-fieldset">
        <legend>{t("secret")}</legend>
        <p className="nr-hint">{t("secretHint")}</p>
        {creating ? (
          <div className="nr-field">
            <label htmlFor={ids.secret}>{t("secretUse")}</label>
            <input
              id={ids.secret}
              type="password"
              autoComplete="new-password"
              value={form.secret}
              maxLength={4096}
              onChange={(event) => onChange({ secret: event.target.value })}
            />
          </div>
        ) : (
          <>
            <p className="nr-hint nr-current">
              {form.secretSet ? t("secretSetNow") : t("secretNotSet")}
            </p>
            {(["keep", "replace", ...(form.secretSet ? ["clear"] : [])] as SecretMode[]).map(
              (mode) => (
                <label className="nr-radio" key={mode}>
                  <input
                    type="radio"
                    name="nr-secret-mode"
                    checked={form.secretMode === mode}
                    onChange={() => onChange({ secretMode: mode, secret: "" })}
                  />
                  <span>
                    {mode === "keep"
                      ? t("secretKeep")
                      : mode === "replace"
                        ? t("secretReplace")
                        : t("secretClear")}
                  </span>
                </label>
              ),
            )}
            {form.secretMode === "replace" ? (
              <div className="nr-field">
                <label htmlFor={ids.secret}>{t("secretValue")}</label>
                <input
                  id={ids.secret}
                  type="password"
                  autoComplete="new-password"
                  value={form.secret}
                  maxLength={4096}
                  onChange={(event) => onChange({ secret: event.target.value })}
                />
              </div>
            ) : null}
          </>
        )}
      </fieldset>

      <fieldset className="nr-fieldset">
        <legend>{t("headers")}</legend>
        <p className="nr-hint">{t("headersHint")}</p>
        {creating ? null : (
          <>
            <p className="nr-hint nr-current">
              {form.storedHeaderNames.length > 0
                ? t("headersCurrent", { names: form.storedHeaderNames.join(", ") })
                : t("headersNone")}
            </p>
            <label className="nr-radio">
              <input
                type="radio"
                name="nr-header-mode"
                checked={!form.replaceHeaders}
                onChange={() => onChange({ replaceHeaders: false, headers: blankHeaders() })}
              />
              <span>{t("headersKeep")}</span>
            </label>
            <label className="nr-radio">
              <input
                type="radio"
                name="nr-header-mode"
                checked={form.replaceHeaders}
                onChange={() => onChange({ replaceHeaders: true })}
              />
              <span>{t("headersReplace")}</span>
            </label>
          </>
        )}
        {showHeaderRows ? (
          <>
            {creating ? null : <p className="nr-hint">{t("headersReplaceHint")}</p>}
            <div className="nr-headers">
              {form.headers.map((row, index) => (
                <div className="nr-header-row" key={index}>
                  <input
                    aria-label={t("headerName", { index: index + 1 })}
                    placeholder={t("headerNamePlaceholder")}
                    value={row.name}
                    maxLength={64}
                    spellCheck={false}
                    autoComplete="off"
                    onChange={(event) => setHeader(index, { name: event.target.value })}
                  />
                  <input
                    aria-label={t("headerValue", { index: index + 1 })}
                    placeholder={t("headerValuePlaceholder")}
                    type="password"
                    value={row.value}
                    maxLength={4096}
                    autoComplete="new-password"
                    onChange={(event) => setHeader(index, { value: event.target.value })}
                  />
                </div>
              ))}
            </div>
          </>
        ) : null}
      </fieldset>

      {errors.length > 0 ? (
        <div className="banner warn" role="alert">
          <div>
            <p>
              <strong>{t("notSaved")}</strong>
            </p>
            <ul className="nr-lines">
              {errors.map((line, index) => (
                <li key={`${index}-${line}`}>{line}</li>
              ))}
            </ul>
          </div>
        </div>
      ) : null}
      <div className="actions">
        <button type="submit" className="action" disabled={working || !canSubmit}>
          {working ? t("saving") : creating ? t("add") : t("save")}
        </button>
        <button type="button" className="action secondary" disabled={working} onClick={onCancel}>
          {t("cancel")}
        </button>
      </div>
    </form>
  );
}
