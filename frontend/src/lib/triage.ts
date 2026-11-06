// Anomaly triage helpers (SOC-1b): the family an anomaly's triage is keyed by, the list filters
// read from the URL, and the sentences of the activity log.

import type {
  AnalystTriageDisposition,
  AnomalySummary,
  SubjectKind,
  TriageDisposition,
  TriageEvent,
  TriageStatus,
} from "@/lib/api";

import type { SearchParamValue } from "./anomalyQuery";

/** The bounds the backend enforces (app/models/triage.py); the form stops a longer text. */
export const OWNER_MAX = 255;
export const NOTE_MAX = 1000;
export const COMMENT_MAX = 2000;
export const ACTIVITY_PAGE = 50;

export const TRIAGE_STATUSES: readonly TriageStatus[] = ["NEW", "IN_PROGRESS", "CLOSED"];
export const DISPOSITIONS: readonly TriageDisposition[] = [
  "TRUE_POSITIVE",
  "BENIGN_TRUE_POSITIVE",
  "NOISE",
  "COLLECTION_PROBLEM",
  "KNOWN_CHANGE",
  "STALE",
];
/** The close form is analyst-only: the stale job, never an analyst, sets STALE. */
export const CLOSE_DISPOSITIONS: readonly AnalystTriageDisposition[] = [
  "TRUE_POSITIVE",
  "BENIGN_TRUE_POSITIVE",
  "NOISE",
  "COLLECTION_PROBLEM",
  "KNOWN_CHANGE",
];

const FAMILY_OF: Record<SubjectKind, "log_source" | "waf" | "ips" | "property_watch"> = {
  LOG_SOURCE: "log_source",
  WAF_ENDPOINT: "waf",
  IPS_ENTITY: "ips",
  IPS_SOURCE: "ips",
  PW_VALUE: "property_watch",
};

/**
 * The triage family of an anomaly, or `null` for a subject kind that has none. A missing
 * `subject_kind` is a Phase A log-source row (the unified list's older shape). A kind this build
 * does not know is `null`, not a guess: the page then says triage is unavailable.
 */
export function triageFamilyOf(a: Pick<AnomalySummary, "subject_kind">) {
  const kind = a.subject_kind ?? "LOG_SOURCE";
  return Object.hasOwn(FAMILY_OF, kind) ? FAMILY_OF[kind] : null;
}

export interface TriageQuery {
  triage_status: TriageStatus | "";
  /** `me`, `none`, a subject, or "". */
  owner: string;
  disposition: TriageDisposition | "";
}

function first(value: SearchParamValue): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

/**
 * The triage filters of the URL. Status and disposition are checked against their allow-lists; an
 * owner is `me`, `none` or free text up to the backend's bound with no control characters. The
 * filter form posts `owner_kind` (+ `owner_name`); a link or a chip carries `owner` directly.
 */
export function parseTriageQuery(params: Record<string, SearchParamValue>): TriageQuery {
  const status = first(params.triage_status);
  const disposition = first(params.disposition);
  const kind = first(params.owner_kind);
  let owner = first(params.owner) ?? "";
  if (kind === "me" || kind === "none") owner = kind;
  else if (kind === "named") owner = first(params.owner_name) ?? "";
  else if (kind === "") owner = first(params.owner) ?? "";
  owner = owner.trim();
  if (owner.length > OWNER_MAX || /[\u0000-\u001f\u007f]/.test(owner)) owner = "";
  return {
    triage_status: TRIAGE_STATUSES.find((s) => s === status) ?? "",
    owner,
    disposition: DISPOSITIONS.find((d) => d === disposition) ?? "",
  };
}

/** What the activity log says about one event, with the catalog's words passed in. */
export interface EventWords {
  status: (value: string) => string;
  disposition: (value: string) => string;
  unassigned: string;
}

export function eventSentence(
  event: TriageEvent,
  words: EventWords,
  say: (key: string, values?: Record<string, string>) => string,
): string {
  const from = event.from_value;
  const to = event.to_value;
  switch (event.kind) {
    case "STATUS":
      return say("events.status", {
        from: from ? words.status(from) : words.status("NEW"),
        to: to ? words.status(to) : "—",
      });
    case "OWNER":
      return to
        ? say(from ? "events.ownerChanged" : "events.ownerSet", { from: from ?? "", to })
        : say("events.ownerCleared", { from: from ?? words.unassigned });
    case "DISPOSITION":
      return to
        ? say("events.dispositionSet", { to: words.disposition(to) })
        : say("events.dispositionCleared", {});
    case "NOTE":
      return say(event.comment ? "events.noteSet" : "events.noteCleared", {});
    case "COMMENT":
      return say("events.comment", {});
  }
}
