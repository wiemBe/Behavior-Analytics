"use client";

// One family: its state control, where the value comes from, what it costs and when it last ran.
// A change is sent only after the answer to a confirmation where one is needed; the row shows the
// server's answer, never its own guess, and a refusal is quoted as the API worded it.

import { useTranslations } from "next-intl";
import Link from "next/link";
import { useEffect, useId, useRef, useState } from "react";

import { ChangeStatus, type ChangeOutcome } from "@/components/ui/ChangeStatus";
import { UtcTime } from "@/components/ui/UtcTime";
import { ApiError, featuresApi, type FeatureRow } from "@/lib/api";
import {
  blockedBy,
  DATA_PAGE,
  isCollecting,
  isStale,
  layoutOf,
  loadOf,
  needsConfirm,
  needsOf,
  refusalMessage,
  sourceLines,
  stateOf,
  stepsFor,
  storedKeys,
  type FamilyState,
  type Step,
} from "@/lib/features";
import { cadenceLabel, outcomeInfo } from "@/lib/searchCadence";
import { relativeAge } from "@/lib/time";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";

interface Pending {
  confirm: { steps: Step[] | "reset"; kind: "off" | "shadow" | "reset"; name: string } | null;
  busy: boolean;
}

export function FamilyRow({
  row,
  now,
  canWrite,
  problems,
  onChanged,
}: {
  row: FeatureRow;
  now: Date;
  canWrite: boolean;
  problems: string[];
  /** Re-reads the overview; false when that read failed. */
  onChanged: () => Promise<boolean>;
}) {
  const t = useTranslations("features");
  const errors = useTranslations("errors");
  const states = useTranslations("states");
  const i18n = useBehaviorI18n();
  const groupId = useId();
  const layout = layoutOf(row);
  const [{ confirm, busy }, setPending] = useState<Pending>({ confirm: null, busy: false });
  const [outcome, setOutcome] = useState<ChangeOutcome>({ kind: "idle" });
  // The control that started a change gets focus back once the row is idle again (guidelines §10).
  const trigger = useRef<HTMLElement | null>(null);
  const remember = () => {
    trigger.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  };
  useEffect(() => {
    if (!busy && trigger.current?.isConnected) trigger.current.focus();
  }, [busy]);
  const known = Object.hasOwn(DATA_PAGE, row.family);
  const name = known ? t(`family.${row.family}.name` as "family.waf.name") : row.family;
  const purpose = t.has(`family.${row.family}.purpose` as "family.catchall.purpose")
    ? t(`family.${row.family}.purpose` as "family.catchall.purpose")
    : null;
  const switchLabel = (key: string) =>
    t.has(`switch.${key}` as "switch.geo_collection_enabled") ? t(`switch.${key}` as "switch.geo_collection_enabled") : key;

  async function run(steps: Step[] | "reset") {
    const resetting = steps === "reset";
    const list: (Step | { key: string; enabled: null })[] = resetting
      ? storedKeys(row).map((key) => ({ key, enabled: null }))
      : steps;
    setPending({ confirm: null, busy: true });
    setOutcome({ kind: "pending" });
    let done = 0;
    let failed = false;
    try {
      for (const step of list) {
        await (step.enabled === null
          ? featuresApi.reset(step.key)
          : featuresApi.set(step.key, step.enabled));
        done += 1;
      }
      setOutcome({ kind: "ok", text: t("write.saved") });
    } catch (err) {
      failed = true;
      setOutcome(failure(err, done, resetting));
    } finally {
      // The row shows what the server now holds, also after a half-done change.
      const read = done > 0 ? await onChanged() : true;
      if (!read && !failed) setOutcome({ kind: "notice", text: t("write.savedStale") });
      setPending({ confirm: null, busy: false });
    }
  }

  function failure(err: unknown, done: number, resetting: boolean): ChangeOutcome {
    if (!(err instanceof ApiError)) return { kind: "error", text: t("write.noAnswer") };
    if (err.status === 403) return { kind: "forbidden", text: t("write.forbidden") };
    if (err.status === 429) {
      return {
        kind: "error",
        text:
          err.retryAfter === null
            ? errors("rateLimited")
            : errors("rateLimitedAfter", { seconds: err.retryAfter }),
      };
    }
    const message = refusalMessage(err.detail, err.message);
    if (done > 0) {
      return { kind: "error", text: t(resetting ? "write.resetPartial" : "write.partial", { message }) };
    }
    return {
      kind: "error",
      text:
        err.status === 422
          ? t("write.refused", { message })
          : t("write.failed", { status: err.status, message }),
    };
  }

  function ask(steps: Step[] | "reset", kind: "off" | "shadow" | "reset", what: string) {
    remember();
    setOutcome({ kind: "idle" });
    setPending({ confirm: { steps, kind, name: what }, busy: false });
  }

  function choose(from: FamilyState, to: FamilyState) {
    if (layout.kind !== "state") return;
    const steps = stepsFor(from, to, layout);
    if (needsConfirm(from, to)) ask(steps, to === "off" ? "off" : "shadow", name);
    else {
      remember();
      void run(steps);
    }
  }

  function toggle(key: string, enabled: boolean) {
    const step = [{ key, enabled }];
    if (enabled) {
      remember();
      void run(step);
    }
    else ask(step, key.includes("alert") ? "shadow" : "off", `${name} ${switchLabel(key).toLowerCase()}`);
  }

  const lines = sourceLines(row);
  const stored = storedKeys(row);
  const load = loadOf(row);
  const needs = needsOf(row);
  const last = row.last_run;
  const outcomeText = last?.outcome ? outcomeInfo(last.outcome, i18n) : null;
  const stale = isStale(row, layout, now);
  const collecting = isCollecting(row, layout);
  const disabled = !canWrite || busy;
  const shown = (key: string) => row.switches[key];
  const inert = layout.kind === "state" && shown(layout.alerting)?.inert === true;

  const control =
    layout.kind === "state" ? (
      <>
        <div className="fm-radios" role="radiogroup" aria-label={t("state.group", { name })}>
          {(["off", "shadow", "alerting"] as const).map((value) => {
            const current = stateOf(row, layout);
            return (
              <label key={value} className="fm-radio">
                <input
                  type="radio"
                  name={groupId}
                  checked={current === value}
                  disabled={disabled}
                  onChange={() => choose(current, value)}
                />
                <span>{t(`state.${value}`)}</span>
              </label>
            );
          })}
        </div>
        {inert && <span className="cell-sub">{t("state.inert")}</span>}
        {layout.extras.length > 0 && (
          <div className="ft-extras">{layout.extras.map((key) => toggleBox(key))}</div>
        )}
      </>
    ) : layout.keys.length === 0 ? (
      <span className="muted">{t("state.always")}</span>
    ) : (
      <div className="ft-extras">{layout.keys.map((key) => toggleBox(key))}</div>
    );

  function toggleBox(key: string) {
    const view = shown(key);
    if (!view) return null;
    const needed = blockedBy(row, key);
    const label = switchLabel(key);
    return (
      <span key={key}>
        <label className="fm-radio">
          <input
            type="checkbox"
            checked={view.enabled}
            disabled={disabled || (needed !== null && !view.enabled)}
            aria-label={`${name}: ${label}`}
            onChange={(e) => toggle(key, e.target.checked)}
          />
          <span>{label}</span>
        </label>
        {needed !== null && !view.enabled && (
          <span className="cell-sub">
            {t("state.needs", { name: switchLabel(needed).toLowerCase() })}
          </span>
        )}
      </span>
    );
  }

  const unit = (u: string, count: number) =>
    t.has(`load.unit.${u}` as "load.unit.enabled_rules") ? t(`load.unit.${u}` as "load.unit.enabled_rules", { count }) : u;
  const kindWord =
    row.family === "property_watch" ? "watches" : row.family === "custom_rules" ? "rules" : "profiles";
  const extra = outcome.kind !== "idle" || confirm !== null;

  return (
    <>
      <tr className={extra ? "has-status" : undefined}>
        <td className="cell-family">
          {known ? <Link href={DATA_PAGE[row.family] ?? "/"}>{name}</Link> : name}
          {purpose && !problems.length && <span className="cell-sub">{purpose}</span>}
          {problems.length > 0 && (
            <details className="ft-conflict">
              <summary className="pill pill-quiet warn">{t("page.settings")}</summary>
              {problems.map((text) => (
                <code key={text} className="ft-problem">
                  {text}
                </code>
              ))}
            </details>
          )}
          {row.prerequisites !== null && (
            <>
              <span
                className="cell-sub"
                title={t(`prerequisites.unit.${kindWord}` as "prerequisites.unit.rules", { count: row.prerequisites.total })}
              >
                {t("prerequisites.enabled", {
                  enabled: row.prerequisites.enabled,
                  total: row.prerequisites.total,
                })}
              </span>
              {row.prerequisites.validated !== undefined && (
                <span className="cell-sub">
                  {t("prerequisites.validated", { count: row.prerequisites.validated })}
                </span>
              )}
            </>
          )}
        </td>
        <td className="ft-state">
          {control}
          <div className="ft-source">
          {lines.map((line) => (
            <span key={line.source} className="cell-sub">
              {line.source === "db" ? (
                <>
                  {line.by ? t("source.db", { user: line.by }) : t("source.dbAnon")}
                  {canWrite && stored.length > 0 && (
                    <>
                      {" · "}
                      <button
                        type="button"
                        className="ft-reset"
                        disabled={busy}
                        aria-label={t("source.resetAria", { name })}
                        title={t("source.reset")}
                        onClick={() => ask("reset", "reset", name)}
                      >
                        {t("source.resetShort")}
                      </button>
                    </>
                  )}
                  {" · "}
                  <UtcTime value={line.at} variant="compact" age={relativeAge(line.at, now)} />
                </>
              ) : (
                t(`source.${line.source}`)
              )}
            </span>
          ))}
          </div>
        </td>
        <td className="num">
          {load.kind === "bound" ? (
            <>
              <span className={collecting ? "nowrap" : "nowrap muted"}>
                {t("load.bound", { count: load.perHour })}
              </span>
              {row.cadence_seconds !== null && (
                <>
                  {" · "}
                  <Link href="/admin/search-cadence" className="nowrap">
                {t("load.every", { duration: cadenceLabel(row.cadence_seconds ?? 0, i18n) })}
              </Link>
                </>
              )}
              <span className="cell-sub" title={load.measured === null ? t("load.notCollected") : undefined}>
                {t("load.detail", {
                  count: load.count,
                  unit: unit(load.unit, load.count),
                  measured:
                    load.measured === null
                      ? t("load.measuredNone")
                      : t("load.measured", { count: load.measured }),
                })}
              </span>
            </>
          ) : load.kind === "insufficient" ? (
            <span className="muted" title={t("load.insufficientTitle")}>
              {states("insufficientData")}
            </span>
          ) : load.kind === "none" ? (
            <span className="muted">{t("load.none", { unit: unit(load.unit, 2) })}</span>
          ) : (
            <span className="muted" title={t("load.notEstimatedTitle")}>
              {states("notCollected")}
            </span>
          )}
          {load.kind !== "bound" && row.cadence_seconds !== null && (
            <span className="cell-sub">
              <Link href="/admin/search-cadence" className="nowrap">
                {t("load.every", { duration: cadenceLabel(row.cadence_seconds, i18n) })}
              </Link>
            </span>
          )}
        </td>
        <td>
          {needs.kind === "reachable" || needs.kind === "nothing" ? (
            <span>{t(`needs.${needs.kind}`)}</span>
          ) : needs.kind === "adminOnly" || needs.kind === "noConsole" ? (
            <span className="muted">{t(`needs.${needs.kind}`)}</span>
          ) : (
            <Link href="/admin/qradar-access">
              {needs.kind === "refused" ? t("needs.refused") : t(`needs.${needs.kind}`)}
            </Link>
          )}
          {needs.kind === "refused" && (
            <span className="cell-sub ft-clip" title={needs.titles.join(", ")}>
              {needs.titles.join(", ")}
            </span>
          )}
        </td>
        <td className="num ft-wrap">
          {last ? (
            <>
              <UtcTime
                value={last.at}
                variant="compact"
                className="nowrap"
                age={relativeAge(last.at, now)}
                ageClassName="muted nowrap"
              />
              {outcomeText && last.outcome !== "ok" && (
                <span className="cell-sub">{outcomeText.label}</span>
              )}
              {stale && (
                <span className="cell-sub">
                  <span className="pill pill-quiet warn" title={t("run.staleTitle")}>
                    {states("stale")}
                  </span>
                </span>
              )}
            </>
          ) : (
            <span className="muted" title={t("run.neverTitle")}>
              {states("notCollected")}
            </span>
          )}
        </td>
      </tr>
      {extra && (
        <tr className="status-row">
          <td colSpan={5}>
            {confirm && (
              <div className="ft-confirm" role="group" aria-label={t(`confirm.${confirm.kind}`, { name: confirm.name })}>
                <p>{t(`confirm.${confirm.kind}`, { name: confirm.name })}</p>
                <button type="button" className="action" onClick={() => void run(confirm.steps)}>
                  {t(`confirm.${confirm.kind}Button`)}
                </button>
                <button
                  type="button"
                  className="action secondary"
                  autoFocus
                  onClick={() => {
                    setPending({ confirm: null, busy });
                    trigger.current?.focus();
                  }}
                >
                  {t("confirm.keep")}
                </button>
              </div>
            )}
            <ChangeStatus outcome={outcome} />
          </td>
        </tr>
      )}
    </>
  );
}
