"use client";

// The routing rules. A rule adds destinations for the alerts it matches; every matching rule
// applies, and `stop_default` additionally drops the .env channels and e-mail recipients for those
// alerts. The window is typed and shown in UTC and says so next to the field.

import { useTranslations } from "next-intl";
import { useId, useState, type FormEvent } from "react";

import { errorTexts } from "@/components/auth/reasons";
import type { Part } from "@/components/mailadmin/usePart";
import { TableScroll } from "@/components/ui/TableScroll";
import { useEnumLabels } from "@/i18n/enumLabels";
import {
  ApiError,
  notificationRoutingApi,
  type NotificationDestination,
  type NotificationRule,
} from "@/lib/api";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";

import { PartError } from "./PartError";
import {
  BLANK_RULE,
  FAMILIES,
  MAX_DESTINATIONS,
  MAX_RULES,
  RULE_SEVERITIES,
  formFromRule,
  minuteText,
  parseMinute,
  ruleBody,
  windowWraps,
  type RuleForm,
  type RuleFormProblem,
} from "./routing";

export function RulesPanel({
  part,
  destinations,
  onChanged,
  onRetry,
  onRetryDestinations,
}: {
  part: Part<NotificationRule[]>;
  destinations: Part<NotificationDestination[]>;
  onChanged: () => void;
  onRetry: () => void;
  onRetryDestinations: () => void;
}) {
  const t = useTranslations("notifyRouting.rules");
  const headingId = useId();
  return (
    <section className="nr-section" aria-labelledby={headingId}>
      <h2 className="nr-h" id={headingId}>
        {part.status === "ok"
          ? t("heading", { count: part.data.length, max: MAX_RULES })
          : t("headingNoCount")}
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
        <Loaded
          list={part.data}
          destinations={destinations.status === "ok" ? destinations.data : null}
          destinationsState={destinations.status}
          onRetryDestinations={onRetryDestinations}
          onChanged={onChanged}
        />
      ) : null}
    </section>
  );
}

function When({ rule }: { rule: NotificationRule }) {
  const t = useTranslations("notifyRouting.rules");
  const family = useTranslations("notifyRouting.family");
  const { label } = useEnumLabels();
  const parts: string[] = [
    t("when.severity", { severity: label("severity", rule.min_severity) }),
  ];
  if (rule.families) {
    parts.push(
      t("when.families", {
        list: rule.families
          .map((key) =>
            (FAMILIES as readonly string[]).includes(key) ? family(key as "waf") : key,
          )
          .join(", "),
      }),
    );
  }
  if (rule.owners) parts.push(t("when.owners", { list: rule.owners.join(", ") }));
  if (rule.source_classes) {
    parts.push(
      t("when.classes", { list: rule.source_classes.map((c) => label("sourceClass", c)).join(", ") }),
    );
  }
  if (rule.group_ids) parts.push(t("when.groups", { list: rule.group_ids.join(", ") }));
  if (rule.window_start_minute !== null && rule.window_end_minute !== null) {
    const wraps = windowWraps(rule.window_start_minute, rule.window_end_minute);
    parts.push(
      t(wraps ? "when.windowWraps" : "when.window", {
        start: minuteText(rule.window_start_minute),
        end: minuteText(rule.window_end_minute),
      }),
    );
  }
  return (
    <ul className="nr-flags">
      {parts.map((part) => (
        <li key={part}>{part}</li>
      ))}
    </ul>
  );
}

function Loaded({
  list,
  destinations,
  destinationsState,
  onRetryDestinations,
  onChanged,
}: {
  list: NotificationRule[];
  /** Null when the destination list is not loaded: still loading, or it failed. */
  destinations: NotificationDestination[] | null;
  destinationsState: Part<NotificationDestination[]>["status"];
  onRetryDestinations: () => void;
  onChanged: () => void;
}) {
  const t = useTranslations("notifyRouting.rules");
  const { t: say, locale } = useBehaviorI18n();
  const [form, setForm] = useState<RuleForm | null>(null);
  const [formErrors, setFormErrors] = useState<string[]>([]);
  const [problem, setProblem] = useState<RuleFormProblem | null>(null);
  const [removing, setRemoving] = useState<NotificationRule | null>(null);
  const [removeLines, setRemoveLines] = useState<string[]>([]);
  const [rowErrors, setRowErrors] = useState<{ id: string; lines: string[] } | null>(null);
  const [working, setWorking] = useState(false);
  const full = list.length >= MAX_RULES;
  const noDestinations = destinations === null || destinations.length === 0;

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!form) return;
    const built = ruleBody(form);
    if ("problem" in built) {
      setProblem(built.problem);
      return;
    }
    setProblem(null);
    setWorking(true);
    setFormErrors([]);
    try {
      if (form.id === null) await notificationRoutingApi.createRule(built.body);
      else await notificationRoutingApi.saveRule(form.id, built.body);
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
      await notificationRoutingApi.deleteRule(removing.id);
      setRemoving(null);
      onChanged();
    } catch (err) {
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

  async function toggle(rule: NotificationRule) {
    setWorking(true);
    setRowErrors(null);
    try {
      await notificationRoutingApi.saveRule(rule.id, { enabled: !rule.enabled });
      onChanged();
    } catch (err) {
      setRowErrors({ id: rule.id, lines: errorTexts(err, say, locale) });
    } finally {
      setWorking(false);
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
                <th scope="col" className="num">
                  {t("col.priority")}
                </th>
                <th scope="col">{t("col.name")}</th>
                <th scope="col">{t("col.when")}</th>
                <th scope="col">{t("col.where")}</th>
                <th scope="col">
                  <span className="sr-only">{t("col.actions")}</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {list.map((rule) => (
                <tr key={rule.id}>
                  <td className="num">{rule.priority}</td>
                  <td className="cell-name">
                    {rule.name}
                    {rule.enabled ? null : <span className="pill nr-pill">{t("disabled")}</span>}
                  </td>
                  <td>
                    <When rule={rule} />
                  </td>
                  <td>
                    <ul className="nr-flags">
                      <li>
                        {rule.destinations.length > 0
                          ? rule.destinations.map((d) => d.name).join(", ")
                          : t("noDestinations")}
                      </li>
                      {rule.stop_default ? (
                        <li>
                          <span className="pill warn">{t("stopDefault")}</span>
                        </li>
                      ) : null}
                    </ul>
                    {rowErrors?.id === rule.id ? (
                      <ul className="nr-lines" role="alert">
                        {rowErrors.lines.map((line, index) => (
                          <li key={`${index}-${line}`}>{line}</li>
                        ))}
                      </ul>
                    ) : null}
                  </td>
                  <td className="nr-actions">
                    <button
                      type="button"
                      className="action secondary"
                      aria-label={t(rule.enabled ? "disableNamed" : "enableNamed", {
                        name: rule.name,
                      })}
                      disabled={working}
                      onClick={() => void toggle(rule)}
                    >
                      {rule.enabled ? t("disable") : t("enable")}
                    </button>
                    <button
                      type="button"
                      className="action secondary"
                      aria-label={t("editNamed", { name: rule.name })}
                      disabled={working}
                      onClick={() => {
                        setRemoving(null);
                        setFormErrors([]);
                        setProblem(null);
                        setForm(formFromRule(rule));
                      }}
                    >
                      {t("edit")}
                    </button>
                    <button
                      type="button"
                      className="action secondary"
                      aria-label={t("deleteNamed", { name: rule.name })}
                      disabled={working}
                      onClick={() => {
                        setForm(null);
                        setRemoveLines([]);
                        setRemoving(rule);
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

      {form && destinations ? (
        <RuleFormView
          form={form}
          destinations={destinations}
          errors={formErrors}
          problem={problem}
          working={working}
          onChange={(change) => setForm({ ...form, ...change })}
          onSubmit={(event) => void save(event)}
          onCancel={() => {
            setForm(null);
            setFormErrors([]);
            setProblem(null);
          }}
        />
      ) : (
        <div className="actions">
          <button
            type="button"
            className="action"
            disabled={full || noDestinations}
            aria-describedby={full || noDestinations ? "nr-rule-limit" : undefined}
            onClick={() => {
              setRemoving(null);
              setFormErrors([]);
              setProblem(null);
              setForm(BLANK_RULE);
            }}
          >
            {t("add")}
          </button>
          {full || noDestinations ? (
            <p className="nr-hint" id="nr-rule-limit">
              {full
                ? t("limit", { max: MAX_RULES })
                : destinations === null
                  ? t(destinationsState === "loading" ? "destinationsLoading" : "destinationsFailed")
                  : t("needDestination")}
            </p>
          ) : null}
          {!full && destinationsState === "error" ? (
            <button type="button" className="action secondary" onClick={onRetryDestinations}>
              {t("destinationsRetry")}
            </button>
          ) : null}
        </div>
      )}
    </>
  );
}

function RuleFormView({
  form,
  destinations,
  errors,
  problem,
  working,
  onChange,
  onSubmit,
  onCancel,
}: {
  form: RuleForm;
  destinations: NotificationDestination[];
  errors: string[];
  problem: RuleFormProblem | null;
  working: boolean;
  onChange: (change: Partial<RuleForm>) => void;
  onSubmit: (event: FormEvent) => void;
  onCancel: () => void;
}) {
  const t = useTranslations("notifyRouting.rules.form");
  const family = useTranslations("notifyRouting.family");
  const { label } = useEnumLabels();
  const ids = {
    name: useId(),
    priority: useId(),
    enabled: useId(),
    severity: useId(),
    owners: useId(),
    classes: useId(),
    groups: useId(),
    from: useId(),
    to: useId(),
    stop: useId(),
    windowOn: useId(),
  };
  const creating = form.id === null;
  const start = parseMinute(form.windowStart);
  const end = parseMinute(form.windowEnd);
  const wraps = form.windowOn && start !== null && end !== null && start !== end && windowWraps(start, end);
  const toggleIn = (list: string[], key: string, on: boolean) =>
    on ? [...list, key] : list.filter((item) => item !== key);
  const tooMany = form.destinationIds.length > MAX_DESTINATIONS;

  return (
    <form
      className="nr-form"
      onSubmit={onSubmit}
      aria-label={creating ? t("addLabel") : t("editLabel")}
      noValidate
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
      <div className="nr-field nr-field-inline">
        <label htmlFor={ids.priority}>{t("priority")}</label>
        <input
          id={ids.priority}
          className="nr-narrow"
          inputMode="numeric"
          value={form.priority}
          aria-invalid={problem === "priority" ? true : undefined}
          onChange={(event) => onChange({ priority: event.target.value })}
        />
        <p className="nr-hint">{t("priorityHint")}</p>
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
      <div className="nr-field nr-field-inline">
        <label htmlFor={ids.severity}>{t("severity")}</label>
        <select
          id={ids.severity}
          className="select-control"
          value={form.minSeverity}
          onChange={(event) =>
            onChange({ minSeverity: event.target.value as RuleForm["minSeverity"] })
          }
        >
          {RULE_SEVERITIES.map((value) => (
            <option key={value} value={value}>
              {label("severity", value)}
            </option>
          ))}
        </select>
        <p className="nr-hint">{t("severityHint")}</p>
      </div>
      <fieldset className="nr-fieldset">
        <legend>{t("families")}</legend>
        <p className="nr-hint">{t("familiesHint")}</p>
        <div className="nr-checks">
          {FAMILIES.map((key) => (
            <label className="nr-radio" key={key}>
              <input
                type="checkbox"
                checked={form.families.includes(key)}
                onChange={(event) =>
                  onChange({ families: toggleIn(form.families, key, event.target.checked) })
                }
              />
              <span>{family(key)}</span>
            </label>
          ))}
        </div>
      </fieldset>
      <div className="nr-field">
        <label htmlFor={ids.owners}>{t("owners")}</label>
        <input
          id={ids.owners}
          value={form.owners}
          spellCheck={false}
          onChange={(event) => onChange({ owners: event.target.value })}
        />
        <p className="nr-hint">{t("ownersHint")}</p>
      </div>
      <div className="nr-field">
        <label htmlFor={ids.classes}>{t("classes")}</label>
        <input
          id={ids.classes}
          value={form.classes}
          spellCheck={false}
          onChange={(event) => onChange({ classes: event.target.value })}
        />
        <p className="nr-hint">{t("classesHint")}</p>
      </div>
      <div className="nr-field">
        <label htmlFor={ids.groups}>{t("groups")}</label>
        <input
          id={ids.groups}
          inputMode="numeric"
          value={form.groups}
          aria-invalid={problem === "groups" ? true : undefined}
          onChange={(event) => onChange({ groups: event.target.value })}
        />
        <p className="nr-hint">{t("groupsHint")}</p>
        {problem === "groups" ? (
          <p className="nr-field-error" role="alert">
            {t("groupsInvalid")}
          </p>
        ) : null}
      </div>

      <fieldset className="nr-fieldset">
        <legend>{t("windowUtc")}</legend>
        <div className="nr-check">
          <input
            id={ids.windowOn}
            type="checkbox"
            checked={form.windowOn}
            onChange={(event) => onChange({ windowOn: event.target.checked })}
          />
          <label htmlFor={ids.windowOn}>{t("windowOn")}</label>
        </div>
        {form.windowOn ? (
          <>
            <div className="nr-window">
              <label htmlFor={ids.from}>{t("windowStart")}</label>
              <input
                id={ids.from}
                className="nr-narrow"
                placeholder="22:00"
                inputMode="numeric"
                value={form.windowStart}
                aria-invalid={problem === "window" ? true : undefined}
                onChange={(event) => onChange({ windowStart: event.target.value })}
              />
              <label htmlFor={ids.to}>{t("windowEnd")}</label>
              <input
                id={ids.to}
                className="nr-narrow"
                placeholder="06:00"
                inputMode="numeric"
                value={form.windowEnd}
                aria-invalid={problem === "window" ? true : undefined}
                onChange={(event) => onChange({ windowEnd: event.target.value })}
              />
              <span className="nr-utc">{t("windowUtc")}</span>
            </div>
            <p className="nr-hint">{t("windowHint")}</p>
            {wraps && start !== null && end !== null ? (
              <p className="nr-hint" role="status">
                {t("windowWrap", { start: minuteText(start), end: minuteText(end) })}
              </p>
            ) : null}
            {problem === "window" ? (
              <p className="nr-field-error" role="alert">
                {t("windowInvalid")}
              </p>
            ) : null}
          </>
        ) : null}
      </fieldset>

      <fieldset className="nr-fieldset">
        <legend>{t("destinations")}</legend>
        <p className="nr-hint">{t("destinationsHint")}</p>
        <div className="nr-checks">
          {destinations.map((destination) => (
            <label className="nr-radio" key={destination.id}>
              <input
                type="checkbox"
                checked={form.destinationIds.includes(destination.id)}
                onChange={(event) =>
                  onChange({
                    destinationIds: toggleIn(form.destinationIds, destination.id, event.target.checked),
                  })
                }
              />
              <span>
                {destination.enabled
                  ? destination.name
                  : t("destinationOff", { name: destination.name })}
              </span>
            </label>
          ))}
        </div>
        {form.destinationIds.length === 0 ? (
          <p className="nr-hint">{t("destinationsNone")}</p>
        ) : null}
        {tooMany ? <p className="nr-hint">{t("destinationsMax", { max: MAX_DESTINATIONS })}</p> : null}
      </fieldset>

      <div className="nr-flag-field">
        <div className="nr-check">
          <input
            id={ids.stop}
            type="checkbox"
            checked={form.stopDefault}
            aria-describedby={`${ids.stop}-warn`}
            onChange={(event) => onChange({ stopDefault: event.target.checked })}
          />
          <label htmlFor={ids.stop}>{t("stopDefault")}</label>
        </div>
        <p className="nr-hint" id={`${ids.stop}-warn`}>
          {t("stopDefaultWarn")}
        </p>
      </div>

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
        <button
          type="submit"
          className="action"
          disabled={
            working || form.name.trim() === "" || form.destinationIds.length === 0 || tooMany
          }
        >
          {working ? t("saving") : creating ? t("add") : t("save")}
        </button>
        <button type="button" className="action secondary" disabled={working} onClick={onCancel}>
          {t("cancel")}
        </button>
      </div>
    </form>
  );
}
