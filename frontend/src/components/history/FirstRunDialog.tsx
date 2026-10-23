"use client";

// The first-run dialog (HISTORY-BACKFILL §B.10.1): the one question, what answering costs, and
// the two answers with the same weight. A native `<dialog>` in the pattern of the widget Catalog
// and Expand dialogs: focus moves to the first control on open, Escape closes it, and focus
// returns to where it was. Closing without choosing does not decide.

import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from "react";

import { ApiError, api, type HistoryConsole, type HistoryConsoleEstimate } from "@/lib/api";

import { DecisionLine } from "./DecisionLine";
import {
  ADMIN_PERMISSION,
  conflictConsole,
  costHours,
  estimateText,
  familyName,
  familyStateText,
  questionText,
  requestedDays,
} from "./historyFormat";

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

const LEFT_OUT = [{ family: "Geography", why: "No baseline to fill." }] as const;

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
  return (
    <table className="hb-families">
      <caption className="sr-only">
        Families read from {consoleState.name}, their state, and the estimate for each.
      </caption>
      <thead>
        <tr>
          <th scope="col">Family</th>
          <th scope="col">State</th>
          <th scope="col">Estimate</th>
        </tr>
      </thead>
      <tbody>
        {consoleState.families.map((family) => {
          const est = settled
            ? estimateText(estimate?.families.find((f) => f.family === family.family))
            : { figures: "—", note: null };
          return (
            <tr key={family.family}>
              <th scope="row">{familyName(family.family)}</th>
              <td>{familyStateText(family)}</td>
              <td className="hb-est">
                {est.figures}
                {est.note && <span className="cell-sub">{est.note}</span>}
                {settled && (
                  <ul className="hb-assumptions" aria-label={`Assumptions for ${familyName(family.family)}`}>
                    {[...new Set(estimate?.families.find((f) => f.family === family.family)?.assumptions ?? [])].map(
                      (a) => (
                        <li key={a}>{a}</li>
                      ),
                    )}
                  </ul>
                )}
              </td>
            </tr>
          );
        })}
        {LEFT_OUT.map((left) => (
          <tr key={left.family}>
            <th scope="row">{left.family}</th>
            <td>Left out. {left.why}</td>
            <td className="muted">—</td>
          </tr>
        ))}
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
                Your answer was not recorded. A decision was recorded in the meantime.{" "}
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
            text: `Nothing was changed. Your role cannot record the answer (permission ${ADMIN_PERMISSION}).`,
          },
        }));
      } else {
        setRows((r) => ({
          ...r,
          [key]: {
            phase: "error",
            text: "Nothing was changed. The answer could not be recorded. Check that the backend is reachable, then try again.",
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
          {questionText(days)}
        </h2>
        <button
          type="button"
          ref={closeRef}
          className="action secondary"
          disabled={saving}
          onClick={onClose}
        >
          {open.length > 0 ? "Decide later" : "Close"}
        </button>
      </header>

      <div className="hb-dialog-body" role="region" aria-label="What would be read, and what it costs" tabIndex={0}>
      {estimates.status === "loading" && (
        <p className="hb-dialog-note" role="status">
          Loading the history backfill status…
        </p>
      )}

      {estimates.status === "error" && (
        <div className="notice" role="alert">
          <p>
            <strong>The estimate could not be loaded.</strong>
          </p>
          <p>
            Nothing was changed, and no answer can be recorded without it. Check that the backend
            is reachable, then try again, or decide later from History backfill in Administration.
          </p>
          <p>
            <button type="button" className="action secondary" onClick={() => void loadEstimates()}>
              Try again
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
            <p className="hb-assumptions-title">
              These are estimates from a model, not measurements. Each family lists what it assumes.
            </p>
          )}
        </section>
      ))}

      <div className="hb-dialog-section">
        <p className="hb-dialog-lead">
          <strong>Nothing old raises an alert.</strong> Old data is used for learning only; it is
          never judged.
        </p>
        {consoles.map((c) => {
          const hours = costHours(byConsole?.find((e) => e.instance_id === c.instance_id));
          const prefix = consoles.length > 1 ? `${c.name}: ` : "";
          return (
            <p key={c.instance_id} className="hb-dialog-lead">
              {prefix}
              {hours === null
                ? "This sends read-only searches to QRadar; how long it takes is not estimated. The schedule you set in Search cadence still applies."
                : `This sends read-only searches to QRadar for about ${hours} hours; the schedule you set in Search cadence still applies.`}
            </p>
          );
        })}
        <p className="hb-dialog-note">
          WAF and IPS sources that already collected cannot read history; sources you enable from
          now on will. Property watches ask for history when you create the watch.
        </p>
      </div>
      </div>

      <div className="hb-decisions" role="group" aria-label="Your answer">
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
                    No
                  </button>
                  <button
                    type="button"
                    className="action secondary"
                    aria-describedby={`${id}-name-${c.instance_id}`}
                    disabled={!ready || row.phase === "saving"}
                    onClick={() => void answer(c, "YES")}
                  >
                    Yes
                  </button>
                </div>
              )}
              {row.phase === "saving" && (
                <p className="hb-row-status" role="status">
                  Saving…
                </p>
              )}
              {row.phase === "done" && (
                <p className="hb-row-status ok" role="status">
                  Recorded. {row.text}
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
          The history backfill status could not be loaded
        </h2>
        <button type="button" ref={closeRef} className="action secondary" onClick={onClose}>
          Close
        </button>
      </header>
      <div className="notice" role="alert">
        <p>
          <strong>Whether QRadar&apos;s past should be read is not known yet.</strong>
        </p>
        <p>
          Nothing was changed. Check that the backend is reachable, then try again. The question is
          asked again at your next page load.
        </p>
        <p>
          <button type="button" className="action secondary" onClick={onRetry}>
            Try again
          </button>
        </p>
      </div>
    </dialog>
  );
}
