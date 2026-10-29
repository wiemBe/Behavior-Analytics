"use client";

// The first-run dialog (HISTORY-BACKFILL §B.10.1): the one question, what answering costs, and
// the two answers with the same weight. A native `<dialog>` in the pattern of the widget Catalog
// and Expand dialogs: focus moves to the first control on open, Escape closes it, and focus
// returns to where it was. Closing without choosing does not decide.

import { useTranslations } from "next-intl";
import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from "react";

import { ApiError, api, type HistoryConsole, type HistoryConsoleEstimate } from "@/lib/api";
import { plainCount } from "@/lib/figureText";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";

import { DecisionLine } from "./DecisionLine";
import {
  ADMIN_PERMISSION,
  assumptionTexts,
  conflictConsole,
  costHours,
  estimateText,
  familyName,
  familyStateText,
  questionText,
  requestedDays,
} from "./historyFormat";
import { shellSentenceReader } from "./historyShellReader";

import "@/components/ui/controls.css";
import "./history.css";

type Estimates =
  | { status: "loading" }
  | { status: "ok"; byConsole: HistoryConsoleEstimate[] }
  | { status: "error" };

type RowState =
  | { phase: "idle" }
  | { phase: "saving" }
  | { phase: "done"; text: ReactNode }
  | { phase: "refused"; text: ReactNode }
  | { phase: "error"; text: ReactNode };

function ConsoleEstimateTable({
  console: consoleState,
  estimate,
  settled,
}: {
  console: HistoryConsole;
  estimate: HistoryConsoleEstimate | undefined;
  /** False while the estimate is being read or could not be: no figure is claimed missing. */
  settled: boolean;
}) {
  const t = useTranslations("history.dialog");
  const i18n = useBehaviorI18n();
  const read = shellSentenceReader(i18n);
  return (
    <table className="hb-families">
      <caption className="sr-only">{t("caption", { name: consoleState.name })}</caption>
      <thead>
        <tr>
          <th scope="col">{t("family")}</th>
          <th scope="col">{t("state")}</th>
          <th scope="col">{t("estimate")}</th>
        </tr>
      </thead>
      <tbody>
        {consoleState.families.map((family) => {
          const est = settled
            ? estimateText(estimate?.families.find((f) => f.family === family.family), i18n, read)
            : { figures: "—", note: null };
          return (
            <tr key={family.family}>
              <th scope="row">{familyName(family.family, i18n)}</th>
              <td>{familyStateText(family, i18n, read)}</td>
              <td className="hb-est">
                {est.figures}
                {est.note && <span className="cell-sub">{est.note}</span>}
                {settled && (
                  <ul className="hb-assumptions" aria-label={t("assumptionsFor", { name: familyName(family.family, i18n) })}>
                    {assumptionTexts(
                      estimate?.families.find((f) => f.family === family.family),
                      read,
                    ).map((a) => (
                      <li key={a}>{a}</li>
                    ))}
                  </ul>
                )}
              </td>
            </tr>
          );
        })}
        <tr>
          <th scope="row">{t("geography")}</th>
          <td>{t("leftOut", { why: t("noBaseline") })}</td>
          <td className="muted">—</td>
        </tr>
      </tbody>
    </table>
  );
}

export function FirstRunDialog({
  consoles,
  onClose,
}: {
  consoles: HistoryConsole[];
  onClose: () => void;
}) {
  const id = useId();
  const t = useTranslations("history.dialog");
  const common = useTranslations("common");
  const i18n = useBehaviorI18n();
  const dialogRef = useRef<HTMLDialogElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const [estimates, setEstimates] = useState<Estimates>({ status: "loading" });
  const [rows, setRows] = useState<Record<string, RowState>>({});
  const [recorded, setRecorded] = useState<Record<string, HistoryConsole>>({});

  const loadEstimates = useCallback(async () => {
    setEstimates({ status: "loading" });
    try {
      setEstimates({ status: "ok", byConsole: await api.historyBackfillEstimate() });
    } catch {
      setEstimates({ status: "error" });
    }
  }, []);

  useEffect(() => {
    void loadEstimates();
  }, [loadEstimates]);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    if (!dialog.open) dialog.showModal();
    closeRef.current?.focus();
    return () => {
      if (dialog.open) dialog.close();
      if (opener && opener.isConnected) opener.focus();
    };
  }, []);

  const byConsole = estimates.status === "ok" ? estimates.byConsole : null;
  const days = requestedDays(consoles, byConsole);
  const open = consoles.filter((c) => (rows[c.instance_id]?.phase ?? "idle") !== "done" && !recorded[c.instance_id]);
  const saving = consoles.some((c) => rows[c.instance_id]?.phase === "saving");
  const ready = estimates.status === "ok";

  async function answer(target: HistoryConsole, choice: "YES" | "NO") {
    const key = target.instance_id;
    setRows((r) => ({ ...r, [key]: { phase: "saving" } }));
    try {
      const next = await api.decideHistoryBackfill(key, choice);
      setRows((r) => ({ ...r, [key]: { phase: "done", text: <DecisionLine console={next} /> } }));
      setRecorded((r) => ({ ...r, [key]: next }));
      const left = consoles.filter(
        (c) => c.instance_id !== key && !recorded[c.instance_id] && rows[c.instance_id]?.phase !== "done",
      );
      const anyRefused = consoles.some((c) => rows[c.instance_id]?.phase === "refused");
      if (left.length === 0 && !anyRefused) onClose();
    } catch (err) {
      const now = conflictConsole(err);
      if (now !== null && now.decision !== null) {
        setRecorded((r) => ({ ...r, [key]: now }));
        setRows((r) => ({
          ...r,
          [key]: {
            phase: "refused",
            text: (
              <>
                {t("meantime")}
                <DecisionLine console={now} />
              </>
            ),
          },
        }));
      } else if (err instanceof ApiError && err.status === 403) {
        setRows((r) => ({
          ...r,
          [key]: {
            phase: "error",
            text: t("forbidden", { permission: ADMIN_PERMISSION }),
          },
        }));
      } else {
        setRows((r) => ({
          ...r,
          [key]: {
            phase: "error",
            text: t("failed"),
          },
        }));
      }
    }
  }

  return (
    <dialog
      ref={dialogRef}
      className="hb-dialog"
      aria-labelledby={`${id}-title`}
      aria-busy={saving || estimates.status === "loading"}
      onCancel={(event) => {
        // Esc: the dialog closes by unmounting, so this component owns the focus return.
        event.preventDefault();
        onClose();
      }}
      onClose={onClose}
    >
      <header className="hb-dialog-head">
        <h2 id={`${id}-title`} className="hb-dialog-title">
          {questionText(days, i18n)}
        </h2>
        <button
          type="button"
          ref={closeRef}
          className="action secondary"
          disabled={saving}
          onClick={onClose}
        >
          {open.length > 0 ? t("decideLater") : t("close")}
        </button>
      </header>

      <div className="hb-dialog-body" role="region" aria-label={t("region")} tabIndex={0}>
      {estimates.status === "loading" && (
        <p className="hb-dialog-note" role="status">
          {t("loading")}
        </p>
      )}

      {estimates.status === "error" && (
        <div className="notice" role="alert">
          <p>
            <strong>{t("estimateFailed")}</strong>
          </p>
          <p>{t("estimateFailedHelp")}</p>
          <p>
            <button type="button" className="action secondary" onClick={() => void loadEstimates()}>
              {common("tryAgain")}
            </button>
          </p>
        </div>
      )}

      {consoles.map((c) => (
        <section key={c.instance_id} className="hb-dialog-section" aria-labelledby={`${id}-${c.instance_id}`}>
          {consoles.length > 1 && (
            <h3 id={`${id}-${c.instance_id}`} className="hb-dialog-console">
              {c.name}
            </h3>
          )}
          <ConsoleEstimateTable
            console={c}
            estimate={byConsole?.find((e) => e.instance_id === c.instance_id)}
            settled={ready}
          />
          {ready && (
            <p className="hb-assumptions-title">{t("modelNote")}</p>
          )}
        </section>
      ))}

      <div className="hb-dialog-section">
        <p className="hb-dialog-lead">
          {t.rich("nothingOld", { strong: (chunks) => <strong>{chunks}</strong> })}
        </p>
        {consoles.map((c) => {
          const hours = costHours(byConsole?.find((e) => e.instance_id === c.instance_id));
          const prefix = consoles.length > 1 ? `${c.name}: ` : "";
          return (
            <p key={c.instance_id} className="hb-dialog-lead">
              {prefix}
              {hours === null
                ? t("costUnknown")
                : t("cost", { hours: plainCount(hours, i18n.locale) })}
            </p>
          );
        })}
        <p className="hb-dialog-note">{t("alreadyCollected")}</p>
      </div>
      </div>

      <div className="hb-decisions" role="group" aria-label={t("answer")}>
        {consoles.map((c) => {
          const row = rows[c.instance_id] ?? { phase: "idle" as const };
          const finished = row.phase === "done" || row.phase === "refused";
          return (
            <div key={c.instance_id} className="hb-decision-row" aria-busy={row.phase === "saving"}>
              <span id={`${id}-name-${c.instance_id}`} className="hb-decision-row-name">
                {c.name}
              </span>
              {!finished && (
                <div className="hb-decision-buttons">
                  <button
                    type="button"
                    className="action secondary"
                    aria-describedby={`${id}-name-${c.instance_id}`}
                    disabled={!ready || row.phase === "saving"}
                    onClick={() => void answer(c, "NO")}
                  >
                    {t("no")}
                  </button>
                  <button
                    type="button"
                    className="action secondary"
                    aria-describedby={`${id}-name-${c.instance_id}`}
                    disabled={!ready || row.phase === "saving"}
                    onClick={() => void answer(c, "YES")}
                  >
                    {t("yes")}
                  </button>
                </div>
              )}
              {row.phase === "saving" && (
                <p className="hb-row-status" role="status">
                  {common("saving")}
                </p>
              )}
              {row.phase === "done" && (
                <p className="hb-row-status ok" role="status">
                  {t("done")}
                  {row.text}
                </p>
              )}
              {row.phase === "refused" && (
                <p className="hb-row-status crit" role="alert">
                  {row.text}
                </p>
              )}
              {row.phase === "error" && (
                <p className="hb-row-status crit" role="alert">
                  {row.text}
                </p>
              )}
            </div>
          );
        })}
      </div>
    </dialog>
  );
}

/** The status could not be read: say what failed, that nothing changed, and what to try. */
export function FirstRunErrorDialog({
  onClose,
  onRetry,
}: {
  onClose: () => void;
  onRetry: () => void;
}) {
  const id = useId();
  const t = useTranslations("history.dialog");
  const common = useTranslations("common");
  const dialogRef = useRef<HTMLDialogElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    if (!dialog.open) dialog.showModal();
    closeRef.current?.focus();
    return () => {
      if (dialog.open) dialog.close();
      if (opener && opener.isConnected) opener.focus();
    };
  }, []);

  return (
    <dialog
      ref={dialogRef}
      className="hb-dialog"
      aria-labelledby={`${id}-title`}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClose={onClose}
    >
      <header className="hb-dialog-head">
        <h2 id={`${id}-title`} className="hb-dialog-title">
          {t("errorTitle")}
        </h2>
        <button type="button" ref={closeRef} className="action secondary" onClick={onClose}>
          {t("close")}
        </button>
      </header>
      <div className="notice" role="alert">
        <p>
          <strong>{t("errorLead")}</strong>
        </p>
        <p>{t("errorHelp")}</p>
        <p>
          <button type="button" className="action secondary" onClick={onRetry}>
            {common("tryAgain")}
          </button>
        </p>
      </div>
    </dialog>
  );
}
