// What one lane of the entity timeline says, decided in one place so the table and its tests agree.
//
// The wire has no FORBIDDEN state: a lane the caller may not read is NOT_COLLECTED with a
// permission reason code and the `permission` field set. That is what tells it apart from a lane
// that was simply not collected. A `total` of null is "not measured" and is never drawn as 0; a
// `total` of 0 on an OK lane means absence was proven for the window, but only when the list is
// the whole window: on a page (a cursor, or a truncated list) a lane's count is for that page.

import type { EntityTimelineLaneOut } from "@/lib/api";

export const PERMISSION_REASON = "entity_timeline.reason.permission";

export type LaneWord =
  | "OK"
  | "NONE"
  | "NOT_COLLECTED"
  | "FORBIDDEN"
  | "PARTIAL"
  | "STALE"
  | "TRUNCATED"
  | "NOT_APPLICABLE";

export type LaneNote = "noneProven" | "notCounted" | "forbidden" | "reason" | null;

export interface LaneView {
  word: LaneWord;
  tone: "" | "warn";
  /** The count to print, or null when the lane has none to give (drawn as a dash, never 0). */
  count: number | null;
  note: LaneNote;
}

export function isForbiddenLane(lane: EntityTimelineLaneOut): boolean {
  return (
    lane.state === "NOT_COLLECTED" &&
    (lane.permission !== null || lane.reason_code === PERMISSION_REASON)
  );
}

export function laneView(lane: EntityTimelineLaneOut, paged: boolean): LaneView {
  if (isForbiddenLane(lane)) {
    return { word: "FORBIDDEN", tone: "warn", count: null, note: "forbidden" };
  }
  switch (lane.state) {
    case "OK": {
      if (lane.total === null) return { word: "OK", tone: "", count: null, note: "notCounted" };
      if (lane.total === 0 && !paged) {
        return { word: "NONE", tone: "", count: 0, note: "noneProven" };
      }
      return { word: "OK", tone: "", count: lane.total, note: null };
    }
    case "NOT_COLLECTED":
      return { word: "NOT_COLLECTED", tone: "warn", count: null, note: "reason" };
    case "PARTIAL":
    case "STALE":
    case "TRUNCATED":
      return { word: lane.state, tone: "warn", count: lane.total, note: "reason" };
    default:
      return { word: "NOT_APPLICABLE", tone: "", count: null, note: lane.reason_code ? "reason" : null };
  }
}
