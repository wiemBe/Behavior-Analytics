// The comparison line: which instant "New" and "Updated" are measured against, the chips that
// change it for this view only, and what happened to the stored marker on this visit.

import Link from "next/link";
import { useTranslations } from "next-intl";

import { utcTimeText } from "@/components/ui/UtcTime";
import type { QueueCompareKind } from "@/lib/api";
import { COMPARE_CHIPS, queueHref, withFirstPage, type QueueQuery } from "@/lib/queue";

export type MarkerNote =
  | { kind: "advanced"; at: string }
  | { kind: "kept-override" }
  | { kind: "kept-narrowed" }
  | { kind: "kept-failed"; marker: string | null; sources: string[] }
  | { kind: "changed"; marker: string | null }
  | { kind: "refused" }
  | { kind: "post-failed" };

export interface MarkerShown {
  kind: QueueCompareKind;
  from: string;
}

export function MarkerLine({
  shown,
  query,
  note,
  onReload,
}: {
  shown: MarkerShown;
  query: QueueQuery;
  note: MarkerNote | null;
  onReload: () => void;
}) {
  const t = useTranslations("queue.marker");
  const time = utcTimeText(shown.from);

  let line: string;
  if (query.compare) line = t("override", { time, chip: t(`chip.${query.compare}`) });
  else if (shown.kind === "DEFAULT_WINDOW") line = t("first", { time });
  else line = t("stored", { time });

  let noteText: string | null = null;
  let tone: "" | "warn" | "crit" = "";
  let reload = false;
  switch (note?.kind) {
    case "advanced":
      noteText = t("advanced", { time: utcTimeText(note.at) });
      break;
    case "kept-override":
      noteText = t("keptOverride");
      break;
    case "kept-narrowed":
      noteText = t("keptNarrowed");
      break;
    case "kept-failed":
      noteText = note.marker
        ? t("keptFailed", { marker: utcTimeText(note.marker), sources: note.sources.join(", ") })
        : t("keptFailedNone", { sources: note.sources.join(", ") });
      tone = "warn";
      break;
    case "changed":
      noteText = note.marker ? t("changed", { time: utcTimeText(note.marker) }) : t("changedUnknown");
      tone = "warn";
      reload = true;
      break;
    case "refused":
      noteText = t("refused");
      tone = "warn";
      break;
    case "post-failed":
      noteText = t("postFailed");
      tone = "warn";
      reload = true;
      break;
    default:
      break;
  }

  return (
    <div className="queue-marker">
      <p className="queue-marker-line">{line}</p>
      <div className="range-chips queue-compare" role="group" aria-label={t("chipsLabel")}>
        <span className="range-chip-list">
          {COMPARE_CHIPS.map(({ key }) => (
            <Link
              key={key}
              href={queueHref(query, withFirstPage({ compare: query.compare === key ? "" : key }))}
              className="range-chip"
              aria-current={query.compare === key ? "true" : undefined}
              aria-label={t(`chipFull.${key}`)}
            >
              {t(`chip.${key}`)}
            </Link>
          ))}
        </span>
        {query.compare && (
          <Link href={queueHref(query, withFirstPage({ compare: "" }))} className="queue-back">
            {t("back")}
          </Link>
        )}
      </div>
      {noteText && (
        <p className={`queue-marker-note${tone ? ` ${tone}` : ""}`} role="status">
          {noteText}
          {reload && (
            <>
              {" "}
              <button type="button" className="action secondary" onClick={onReload}>
                {t("reload")}
              </button>
            </>
          )}
        </p>
      )}
    </div>
  );
}
