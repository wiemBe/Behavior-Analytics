// The pure parts of the notification routing page: the UTC window as minutes and as text, the
// filter lists as typed, and the request bodies. Nothing here reads a catalog or the network.

import type { DestinationBody, NotificationRule, RuleBody, RuleSeverity } from "@/lib/api";

export const MAX_DESTINATIONS = 10;
export const MAX_RULES = 50;
export const MAX_HEADERS = 5;
export const RULE_SEVERITIES: readonly RuleSeverity[] = ["CRITICAL", "HIGH", "MEDIUM", "LOW"];
export const FAMILIES = [
  "log_source",
  "waf",
  "ips",
  "property_watch",
  "rule",
  "catchall_sender",
  "custom_rule",
  "scheduled_search",
  "qradar_instance",
] as const;

/** `22:00` for 1320. */
export function minuteText(minute: number): string {
  const hours = Math.floor(minute / 60);
  const rest = minute % 60;
  return `${String(hours).padStart(2, "0")}:${String(rest).padStart(2, "0")}`;
}

/** Minutes after 00:00 UTC for `HH:MM`, or null when the text is not a time of day. */
export function parseMinute(text: string): number | null {
  const match = /^\s*(\d{1,2}):(\d{2})\s*$/.exec(text);
  if (!match) return null;
  const hours = Number(match[1]);
  const rest = Number(match[2]);
  if (hours > 23 || rest > 59) return null;
  return hours * 60 + rest;
}

/** A window whose end is before its start runs across midnight. */
export function windowWraps(start: number, end: number): boolean {
  return end < start;
}

/** Comma-separated text to a trimmed list without blanks or duplicates. */
export function splitList(text: string): string[] {
  return [...new Set(text.split(",").map((item) => item.trim()).filter((item) => item !== ""))];
}

/** Group ids as integers; null when any item is not a whole number. */
export function parseGroupIds(text: string): number[] | null {
  const items = splitList(text);
  const out: number[] = [];
  for (const item of items) {
    if (!/^\d{1,9}$/.test(item)) return null;
    out.push(Number(item));
  }
  return [...new Set(out)];
}

export interface RuleForm {
  id: string | null;
  name: string;
  priority: string;
  enabled: boolean;
  minSeverity: RuleSeverity;
  families: string[];
  owners: string;
  classes: string;
  groups: string;
  windowOn: boolean;
  windowStart: string;
  windowEnd: string;
  destinationIds: string[];
  stopDefault: boolean;
}

export const BLANK_RULE: RuleForm = {
  id: null,
  name: "",
  priority: "100",
  enabled: true,
  minSeverity: "HIGH",
  families: [],
  owners: "",
  classes: "",
  groups: "",
  windowOn: false,
  windowStart: "",
  windowEnd: "",
  destinationIds: [],
  stopDefault: false,
};

export function formFromRule(rule: NotificationRule): RuleForm {
  const windowOn = rule.window_start_minute !== null && rule.window_end_minute !== null;
  return {
    id: rule.id,
    name: rule.name,
    priority: String(rule.priority),
    enabled: rule.enabled,
    minSeverity: rule.min_severity,
    families: rule.families ?? [],
    owners: (rule.owners ?? []).join(", "),
    classes: (rule.source_classes ?? []).join(", "),
    groups: (rule.group_ids ?? []).join(", "),
    windowOn,
    windowStart: windowOn ? minuteText(rule.window_start_minute ?? 0) : "",
    windowEnd: windowOn ? minuteText(rule.window_end_minute ?? 0) : "",
    destinationIds: rule.destinations.map((destination) => destination.id),
    stopDefault: rule.stop_default,
  };
}

export type RuleFormProblem = "window" | "groups" | "priority";

/**
 * The request body for a rule form, or the field that cannot be sent. An empty filter is sent as
 * null, which the API reads as "any"; a PATCH carries the nulls too, so clearing a filter works.
 */
export function ruleBody(form: RuleForm): { body: RuleBody } | { problem: RuleFormProblem } {
  const priority = Number(form.priority);
  if (!/^\d{1,6}$/.test(form.priority.trim()) || priority > 100000) return { problem: "priority" };
  const groups = parseGroupIds(form.groups);
  if (groups === null) return { problem: "groups" };
  let start: number | null = null;
  let end: number | null = null;
  if (form.windowOn) {
    start = parseMinute(form.windowStart);
    end = parseMinute(form.windowEnd);
    if (start === null || end === null || start === end) return { problem: "window" };
  }
  const owners = splitList(form.owners);
  const classes = splitList(form.classes);
  return {
    body: {
      name: form.name.trim(),
      enabled: form.enabled,
      priority,
      min_severity: form.minSeverity,
      families: form.families.length > 0 ? form.families : null,
      owners: owners.length > 0 ? owners : null,
      source_classes: classes.length > 0 ? classes : null,
      group_ids: groups.length > 0 ? groups : null,
      window_start_minute: start,
      window_end_minute: end,
      destination_ids: form.destinationIds,
      stop_default: form.stopDefault,
    },
  };
}

export type SecretMode = "keep" | "replace" | "clear";

export interface HeaderRow {
  name: string;
  value: string;
}

export interface DestinationForm {
  id: string | null;
  name: string;
  enabled: boolean;
  /** Write-only: what the administrator typed, never the stored URL. */
  url: string;
  allowHttp: boolean;
  allowPrivate: boolean;
  secretMode: SecretMode;
  secret: string;
  /** What the API reported: the scheme://host, whether a secret exists, the header names. */
  storedAddress: string;
  secretSet: boolean;
  /** Names already stored; their values are unknown to the browser. */
  storedHeaderNames: string[];
  replaceHeaders: boolean;
  headers: HeaderRow[];
}

export function blankHeaders(): HeaderRow[] {
  return Array.from({ length: MAX_HEADERS }, () => ({ name: "", value: "" }));
}

export const BLANK_DESTINATION: DestinationForm = {
  id: null,
  name: "",
  enabled: true,
  url: "",
  allowHttp: false,
  allowPrivate: false,
  secretMode: "keep",
  secret: "",
  storedAddress: "",
  secretSet: false,
  storedHeaderNames: [],
  replaceHeaders: false,
  headers: blankHeaders(),
};

/**
 * The request body for a destination form. A new destination always sends its URL, secret and
 * headers it has. An edit sends the URL only when a replacement was typed, the secret only when
 * it is replaced (`null` clears it), and the headers only when they are replaced as a whole.
 */
export function destinationBody(form: DestinationForm): DestinationBody {
  const body: DestinationBody = {
    name: form.name.trim(),
    enabled: form.enabled,
    allow_http: form.allowHttp,
    allow_private: form.allowPrivate,
  };
  const url = form.url.trim();
  if (form.id === null || url !== "") body.url = url;
  if ((form.id === null || form.secretMode === "replace") && form.secret !== "") {
    body.hmac_secret = form.secret;
  }
  if (form.id !== null && form.secretMode === "clear") body.hmac_secret = null;
  if (form.id === null || form.replaceHeaders) {
    body.headers = Object.fromEntries(
      form.headers
        .filter((row) => row.name.trim() !== "")
        .map((row) => [row.name.trim(), row.value]),
    );
  }
  return body;
}
