// The visible form of a history-backfill decision. The string helpers in historyFormat stay
// for any caller that needs plain text; what the analyst reads uses <UtcTime>.

import type { ReactNode } from "react";

import type { HistoryConsole } from "@/lib/api";
import { UtcTime } from "@/components/ui/UtcTime";

import { decidedBy } from "./historyFormat";

export function DecisionLine({ console: consoleState }: { console: HistoryConsole }): ReactNode {
  const decision = consoleState.decision;
  if (decision === null) {
    return "History backfill: not decided. Nothing is read from QRadar's past.";
  }
  const by = decidedBy(decision);
  const when = <UtcTime value={decision.decided_at} />;
  if (decision.answer === "NO") {
    return (
      <>
        History backfill: No, history is not read. Recorded by {by} on {when}.
      </>
    );
  }
  const days = decision.days === null ? "" : `, read ${decision.days} days`;
  return (
    <>
      History backfill: Yes{days}. Recorded by {by} on {when}.
    </>
  );
}

/** The requests made after the answer, each with its instant as <UtcTime>. */
export function RequestLines({ console: consoleState }: { console: HistoryConsole }): ReactNode {
  const d = consoleState.decision;
  if (d === null) return null;
  return (
    <>
      {d.start_requested_at ? (
        <>
          {" "}
          Read history now was asked by {d.start_requested_by ?? "an operator"} on{" "}
          <UtcTime value={d.start_requested_at} />.
        </>
      ) : null}
      {d.stop_requested_at ? (
        <>
          {" "}
          Stop reading was asked by {d.stop_requested_by ?? "an operator"} on{" "}
          <UtcTime value={d.stop_requested_at} />.
        </>
      ) : null}
    </>
  );
}
