// The visible form of a history-backfill decision. The string helpers in historyFormat stay
// for any caller that needs plain text; what the analyst reads uses <UtcTime>.

import type { ReactNode } from "react";
import { useTranslations } from "next-intl";

import type { HistoryConsole } from "@/lib/api";
import { plainCount } from "@/lib/figureText";
import { UtcTime } from "@/components/ui/UtcTime";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";

import { decidedBy } from "./historyFormat";

export function DecisionLine({ console: consoleState }: { console: HistoryConsole }): ReactNode {
  const t = useTranslations("history");
  const i18n = useBehaviorI18n();
  const decision = consoleState.decision;
  if (decision === null) {
    return t("line.undecided");
  }
  const by = decidedBy(decision, i18n);
  const time = () => <UtcTime value={decision.decided_at} />;
  if (decision.answer === "NO") {
    return <>{t.rich("line.no", { by, time })}</>;
  }
  const days =
    decision.days === null
      ? ""
      : t("line.yesDays", { days: plainCount(decision.days, i18n.locale) });
  return <>{t.rich("line.yes", { days, by, time })}</>;
}

/** The requests made after the answer, each with its instant as <UtcTime>. */
export function RequestLines({ console: consoleState }: { console: HistoryConsole }): ReactNode {
  const t = useTranslations("history");
  const d = consoleState.decision;
  if (d === null) return null;
  const operator = t("decidedBy.operator");
  return (
    <>
      {d.start_requested_at ? (
        <>
          {t.rich("line.startAsked", {
            by: d.start_requested_by ?? operator,
            time: () => <UtcTime value={d.start_requested_at} />,
          })}
        </>
      ) : null}
      {d.stop_requested_at ? (
        <>
          {t.rich("line.stopAsked", {
            by: d.stop_requested_by ?? operator,
            time: () => <UtcTime value={d.stop_requested_at} />,
          })}
        </>
      ) : null}
    </>
  );
}
