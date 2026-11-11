// The shift handover's pure decisions (SOC-14b): which frozen rows are Watch and which are the
// rest, the qualifiers of a row, the sources a record must name, and how a cut-off list is
// worded. No text lives here: the page words these results from the `handover` catalog.

import type {
  HandoverActedOnSource,
  HandoverBody,
  HandoverItem,
  QueueSource,
} from "@/lib/api";

/** The preview and the record hold at most this many watch keys (`app/schemas/handover.py`). */
export const MAX_WATCH = 20;
/** The note's length range, characters (`handover.note_required`, `handover.note_too_long`). */
export const NOTE_MAX = 4000;
/** The earlier-handovers list loads this many at a time. */
export const EARLIER_PAGE = 20;

export type RowFlag = "unassigned" | "overSla" | "new" | "updated";

/** Kinds that have no owner by nature: an empty owner is not "unassigned" for them. */
const OWNERLESS = new Set(["source_review", "catchall_sender"]);

/**
 * The qualifiers of a frozen row, in reading order. `over_sla` null means the SLA is not known
 * for the row, and then nothing is said: unknown is not "within SLA".
 */
export function rowFlags(item: Pick<HandoverItem, "kind" | "owner" | "over_sla" | "change">): RowFlag[] {
  const flags: RowFlag[] = [];
  if (item.owner === null && !OWNERLESS.has(item.kind)) flags.push("unassigned");
  if (item.over_sla === true) flags.push("overSla");
  if (item.change === "NEW") flags.push("new");
  else if (item.change === "UPDATED") flags.push("updated");
  return flags;
}

/** One row appears once: the Watch rows first, the others after them (Watch wins). */
export function splitItems(body: Pick<HandoverBody, "items">): {
  watch: HandoverItem[];
  others: HandoverItem[];
} {
  return {
    watch: body.items.filter((item) => item.watch),
    others: body.items.filter((item) => !item.watch),
  };
}

export type SourceLine =
  | { scope: "open"; source: QueueSource }
  | { scope: "acted"; source: HandoverActedOnSource };

/** Every frozen source that was not read cleanly. An `OK` source needs no line. */
export function sourceLines(body: Pick<HandoverBody, "sources" | "acted_on_sources">): SourceLine[] {
  return [
    ...body.sources.filter((s) => s.state !== "OK").map((source) => ({ scope: "open" as const, source })),
    ...body.acted_on_sources
      .filter((s) => s.state !== "OK")
      .map((source) => ({ scope: "acted" as const, source })),
  ];
}

export type Cutoff =
  | { kind: "none" }
  | { kind: "exact"; shown: number; total: number }
  | { kind: "atLeast"; shown: number; total: number };

/** "50 of N" when the total is exact, "50 of at least N" when it is a lower bound. */
export function cutoff(body: Pick<HandoverBody, "truncated" | "total" | "total_exact" | "items">): Cutoff {
  if (!body.truncated) return { kind: "none" };
  const base = { shown: body.items.length, total: body.total };
  return body.total_exact ? { kind: "exact", ...base } : { kind: "atLeast", ...base };
}

/** The record is incomplete when any frozen source (open rows or acted-on) was not `OK`. */
export function snapshotIncomplete(body: Pick<HandoverBody, "sources" | "acted_on_sources">): boolean {
  return sourceLines(body).length > 0;
}

/** The note as the backend counts it: characters, and an empty note after trimming is refused. */
export function noteProblem(note: string): "required" | "tooLong" | null {
  if (note.trim().length === 0) return "required";
  if (note.length > NOTE_MAX) return "tooLong";
  return null;
}

/** The shared key of a row for the watch set. */
export const watchKey = (item: Pick<HandoverItem, "kind" | "id">): string => `${item.kind}:${item.id}`;
