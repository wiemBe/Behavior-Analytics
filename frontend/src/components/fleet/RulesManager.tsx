"use client";

// The classification rules, grouped by the level they decide at.
//
// A built-in rule offers its enable toggle and nothing else: the API refuses
// any other change with a 409, so no control for one is drawn. A custom rule can
// be edited and deleted. The pattern is validated by the server, and its
// message is shown at the field it concerns.

import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { useTranslations } from "next-intl";

import { ChangeStatus, type ChangeOutcome } from "@/components/ui/ChangeStatus";
import { Disclosure } from "@/components/ui/Disclosure";
import { TableScroll } from "@/components/ui/TableScroll";
import {
  fleetApi,
  type ClassificationRule,
  type ClassificationRuleKind,
  type SourceClass,
} from "@/lib/api";
import {
  ASSIGNABLE_CLASSES,
  RULE_KINDS,
  RULE_LEVELS,
  classLabel,
  patternHint,
  ruleFailure,
  ruleKindLabel,
  ruleLevelTitle,
  rulesAtLevel,
  sortRules,
} from "@/lib/fleet";
import { useFleetText } from "@/lib/fleetText";
import { plainCount } from "@/lib/figureText";

import "@/components/ui/controls.css";

function FieldError({ id, message }: { id: string; message: string | null }) {
  if (!message) return null;
  return (
    <p id={id} className="field-error" role="alert">
      {message}
    </p>
  );
}

interface RuleDraft {
  pattern: string;
  target_class: SourceClass;
  priority: string;
  description: string;
}

function RuleEditor({
  rule,
  onSaved,
  onCancel,
}: {
  rule: ClassificationRule;
  onSaved: (rule: ClassificationRule) => void;
  onCancel: () => void;
}) {
  const t = useTranslations("classification.manager");
  const text = useFleetText();
  const uid = useId();
  const [draft, setDraft] = useState<RuleDraft>({
    pattern: rule.pattern,
    target_class: rule.target_class,
    priority: String(rule.priority),
    description: rule.description ?? "",
  });
  const [pending, setPending] = useState(false);
  const [patternError, setPatternError] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<ChangeOutcome>({ kind: "idle" });

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (pending) return;
    setPending(true);
    setPatternError(null);
    setOutcome({ kind: "pending" });
    try {
      const saved = await fleetApi.updateRule(rule.id, {
        pattern: draft.pattern,
        target_class: draft.target_class,
        priority: Number(draft.priority),
        description: draft.description.trim() === "" ? null : draft.description,
      });
      onSaved(saved);
    } catch (err) {
      const failure = ruleFailure(err, text);
      if (failure.field === "pattern") {
        setPatternError(failure.text);
        setOutcome({ kind: "idle" });
      } else {
        setOutcome({ kind: failure.kind, text: failure.text });
      }
    } finally {
      setPending(false);
    }
  }

  return (
    <form
      className="rule-form"
      onSubmit={(e) => void submit(e)}
      aria-label={t("editAria", { pattern: rule.pattern })}
    >
      <div className="rule-form-grid">
        <label htmlFor={`${uid}-pattern`}>{t("pattern")}</label>
        <div>
          <input
            id={`${uid}-pattern`}
            className="field"
            value={draft.pattern}
            aria-invalid={patternError ? true : undefined}
            aria-describedby={patternError ? `${uid}-pattern-err` : undefined}
            onChange={(e) => setDraft({ ...draft, pattern: e.target.value })}
          />
          <FieldError id={`${uid}-pattern-err`} message={patternError} />
        </div>
        <label htmlFor={`${uid}-class`}>{t("targetClass")}</label>
        <select
          id={`${uid}-class`}
          className="select-control"
          value={draft.target_class}
          onChange={(e) => setDraft({ ...draft, target_class: e.target.value as SourceClass })}
        >
          {ASSIGNABLE_CLASSES.map((c) => (
            <option key={c} value={c}>
              {classLabel(c, text)}
            </option>
          ))}
        </select>
        <label htmlFor={`${uid}-priority`}>{t("priority")}</label>
        <input
          id={`${uid}-priority`}
          className="field field-narrow"
          type="number"
          min={0}
          max={10000}
          value={draft.priority}
          onChange={(e) => setDraft({ ...draft, priority: e.target.value })}
        />
        <label htmlFor={`${uid}-desc`}>{t("description")}</label>
        <input
          id={`${uid}-desc`}
          className="field"
          value={draft.description}
          onChange={(e) => setDraft({ ...draft, description: e.target.value })}
        />
      </div>
      <div className="actions">
        <button type="submit" className="action" disabled={pending}>
          {t("saveRule")}
        </button>
        <button type="button" className="action secondary" disabled={pending} onClick={onCancel}>
          {t("cancel")}
        </button>
      </div>
      <ChangeStatus outcome={outcome} />
    </form>
  );
}

function RuleRow({
  rule,
  onChange,
  onRemove,
}: {
  rule: ClassificationRule;
  onChange: (rule: ClassificationRule) => void;
  onRemove: (id: string) => void;
}) {
  const t = useTranslations("classification.manager");
  const text = useFleetText();
  const [pending, setPending] = useState(false);
  const [editing, setEditing] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [outcome, setOutcome] = useState<ChangeOutcome>({ kind: "idle" });
  const deleteRef = useRef<HTMLButtonElement>(null);
  const keepRef = useRef<HTMLButtonElement>(null);
  const restoreDeleteFocus = useRef(false);

  useEffect(() => {
    if (confirming) {
      keepRef.current?.focus();
    } else if (restoreDeleteFocus.current) {
      restoreDeleteFocus.current = false;
      deleteRef.current?.focus();
    }
  }, [confirming]);

  function closeDelete() {
    restoreDeleteFocus.current = true;
    setConfirming(false);
  }

  async function toggle(enabled: boolean) {
    if (pending) return;
    setPending(true);
    setOutcome({ kind: "pending" });
    try {
      onChange(await fleetApi.updateRule(rule.id, { enabled }));
      setOutcome({
        kind: "ok",
        text: enabled ? t("enabledDone") : t("disabledDone"),
      });
    } catch (err) {
      const failure = ruleFailure(err, text);
      setOutcome({ kind: failure.kind, text: failure.text });
    } finally {
      setPending(false);
    }
  }

  async function remove() {
    if (pending) return;
    setPending(true);
    setOutcome({ kind: "pending" });
    try {
      await fleetApi.deleteRule(rule.id);
      onRemove(rule.id);
    } catch (err) {
      const failure = ruleFailure(err, text);
      setOutcome({ kind: failure.kind, text: failure.text });
      setConfirming(false);
    } finally {
      setPending(false);
    }
  }

  const label = `${ruleKindLabel(rule.kind, text)} ${rule.pattern}`;

  return (
    <>
      <tr className={rule.enabled ? undefined : "rule-off"}>
        <td className="num">{plainCount(rule.priority, text.locale)}</td>
        <th scope="row" className="cell-rule">
          <code>{rule.pattern}</code>
          <span className="cell-sub">
            {ruleKindLabel(rule.kind, text)}
            {rule.description ? ` · ${rule.description}` : ""}
          </span>
        </th>
        <td>{classLabel(rule.target_class, text)}</td>
        <td>
          {rule.built_in ? t("builtIn") : t("custom")}
          <span className="cell-sub">
            {rule.instance_id ? t("oneInstance") : t("allInstances")}
            {rule.created_by ? ` · ${rule.created_by}` : ""}
          </span>
        </td>
        <td>
          <label className="toggle">
            <input
              type="checkbox"
              checked={rule.enabled}
              disabled={pending}
              aria-label={t("enabledAria", { label })}
              onChange={(e) => void toggle(e.target.checked)}
            />
            <span>{rule.enabled ? t("on") : t("off")}</span>
          </label>
        </td>
        <td className="cell-actions">
          {rule.built_in ? (
            <span className="muted">—</span>
          ) : confirming ? (
            <div
              className="actions"
              onKeyDown={(event) => {
                if (event.key === "Escape" && !pending) {
                  event.stopPropagation();
                  closeDelete();
                }
              }}
            >
              <button
                type="button"
                className="action"
                disabled={pending}
                onClick={() => void remove()}
              >
                {t("confirmDelete")}
              </button>
              <button
                type="button"
                ref={keepRef}
                className="action secondary"
                disabled={pending}
                onClick={closeDelete}
              >
                {t("keep")}
              </button>
            </div>
          ) : (
            <div className="actions">
              <button
                type="button"
                className="action secondary"
                aria-label={t("editRowAria", { label })}
                disabled={pending}
                onClick={() => setEditing(true)}
              >
                {t("edit")}
              </button>
              <button
                type="button"
                ref={deleteRef}
                className="action secondary"
                aria-label={t("deleteRowAria", { label })}
                disabled={pending}
                onClick={() => {
                  setOutcome({ kind: "idle" });
                  setConfirming(true);
                }}
              >
                {t("delete")}
              </button>
            </div>
          )}
        </td>
      </tr>
      {(editing || outcome.kind !== "idle") && (
        <tr className="status-row">
          <td colSpan={6}>
            {editing ? (
              <RuleEditor
                rule={rule}
                onCancel={() => setEditing(false)}
                onSaved={(saved) => {
                  setEditing(false);
                  onChange(saved);
                  setOutcome({ kind: "ok", text: t("saved") });
                }}
              />
            ) : (
              <ChangeStatus outcome={outcome} />
            )}
          </td>
        </tr>
      )}
    </>
  );
}

function CreateRule({ onCreated }: { onCreated: (rule: ClassificationRule) => void }) {
  const t = useTranslations("classification.manager");
  const text = useFleetText();
  const uid = useId();
  const [kind, setKind] = useState<ClassificationRuleKind>("NAME_REGEX");
  const [pattern, setPattern] = useState("");
  const [target, setTarget] = useState<SourceClass>("OTHER");
  const [priority, setPriority] = useState("100");
  const [description, setDescription] = useState("");
  const [pending, setPending] = useState(false);
  const [patternError, setPatternError] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<ChangeOutcome>({ kind: "idle" });

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (pending) return;
    setPending(true);
    setPatternError(null);
    setOutcome({ kind: "pending" });
    try {
      const created = await fleetApi.createRule({
        kind,
        pattern,
        target_class: target,
        priority: Number(priority),
        description: description.trim() === "" ? null : description,
      });
      onCreated(created);
      setPattern("");
      setDescription("");
      setOutcome({ kind: "ok", text: t("added") });
    } catch (err) {
      const failure = ruleFailure(err, text);
      if (failure.field === "pattern") {
        setPatternError(failure.text);
        setOutcome({ kind: "idle" });
      } else {
        setOutcome({ kind: failure.kind, text: failure.text });
      }
    } finally {
      setPending(false);
    }
  }

  return (
    <form className="rule-form" onSubmit={(e) => void submit(e)} aria-label={t("addTitle")}>
      <div className="rule-form-grid">
        <label htmlFor={`${uid}-kind`}>{t("matches")}</label>
        <select
          id={`${uid}-kind`}
          className="select-control"
          value={kind}
          onChange={(e) => setKind(e.target.value as ClassificationRuleKind)}
        >
          {RULE_KINDS.map((k) => (
            <option key={k} value={k}>
              {ruleKindLabel(k, text)}
            </option>
          ))}
        </select>
        <label htmlFor={`${uid}-pattern`}>{t("pattern")}</label>
        <div>
          <input
            id={`${uid}-pattern`}
            className="field"
            value={pattern}
            aria-invalid={patternError ? true : undefined}
            aria-describedby={`${uid}-hint${patternError ? ` ${uid}-pattern-err` : ""}`}
            onChange={(e) => setPattern(e.target.value)}
          />
          <p id={`${uid}-hint`} className="field-hint">
            {patternHint(kind, text)}
          </p>
          <FieldError id={`${uid}-pattern-err`} message={patternError} />
        </div>
        <label htmlFor={`${uid}-class`}>{t("targetClass")}</label>
        <select
          id={`${uid}-class`}
          className="select-control"
          value={target}
          onChange={(e) => setTarget(e.target.value as SourceClass)}
        >
          {ASSIGNABLE_CLASSES.map((c) => (
            <option key={c} value={c}>
              {classLabel(c, text)}
            </option>
          ))}
        </select>
        <label htmlFor={`${uid}-priority`}>{t("priority")}</label>
        <div>
          <input
            id={`${uid}-priority`}
            className="field field-narrow"
            type="number"
            min={0}
            max={10000}
            value={priority}
            onChange={(e) => setPriority(e.target.value)}
          />
          <p className="field-hint">{t("priorityHint")}</p>
        </div>
        <label htmlFor={`${uid}-desc`}>{t("description")}</label>
        <input
          id={`${uid}-desc`}
          className="field"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
      </div>
      <div className="actions">
        <button type="submit" className="action" disabled={pending || pattern.trim() === ""}>
          {t("addRule")}
        </button>
      </div>
      <ChangeStatus outcome={outcome} />
    </form>
  );
}

export function RulesManager({ initial }: { initial: ClassificationRule[] }) {
  const t = useTranslations("classification.manager");
  const text = useFleetText();
  const [rules, setRules] = useState(() => sortRules(initial));

  const replace = (rule: ClassificationRule) =>
    setRules((prev) => sortRules(prev.map((r) => (r.id === rule.id ? rule : r))));
  const remove = (id: string) => setRules((prev) => prev.filter((r) => r.id !== id));

  return (
    <>
      {RULE_LEVELS.map((level) => {
        const atLevel = rulesAtLevel(rules, level);
        const title = ruleLevelTitle(level, text);
        return (
          <section key={level.id} aria-labelledby={`level-${level.id}`}>
            <h2 className="section-title" id={`level-${level.id}`}>
              {title}
            </h2>
            {atLevel.length === 0 ? (
              <div className="notice">
                <p>
                  <strong>
                    {t("levelEmptyTitle", {
                      level: text.locale === "tr" ? title : title.toLowerCase(),
                    })}
                  </strong>
                </p>
                <p>{t("levelEmptyText")}</p>
              </div>
            ) : (
              <TableScroll label={title}>
                <table className="fleet-table rules-table">
                  <caption className="sr-only">
                    {t("caption", { level: title })}
                  </caption>
                  <thead>
                    <tr>
                      <th scope="col" className="num">
                        {t("priority")}
                      </th>
                      <th scope="col">{t("pattern")}</th>
                      <th scope="col">{t("class")}</th>
                      <th scope="col">{t("origin")}</th>
                      <th scope="col">{t("enabled")}</th>
                      <th scope="col">{t("actions")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {atLevel.map((r) => (
                      <RuleRow key={r.id} rule={r} onChange={replace} onRemove={remove} />
                    ))}
                  </tbody>
                </table>
              </TableScroll>
            )}
          </section>
        );
      })}

      <section aria-label={t("addTitle")} className="rule-add">
        <Disclosure summary={t("addTitle")} note={t("addNote")}>
          <CreateRule
            onCreated={(rule) => setRules((prev) => sortRules([...prev, rule]))}
          />
        </Disclosure>
      </section>
    </>
  );
}
