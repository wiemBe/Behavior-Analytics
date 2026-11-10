// The entity timeline's URL state and the pure decisions around it (SOC-16c).
//
// The URL is the view: `value`, `kind`, `range` and `cursor` live in it, so a link reproduces what
// the sender saw. Nothing here classifies a value: the API decides whether a bare value is an IP,
// and refuses the rest with a coded message that asks for a kind.

type Raw = string | string[] | undefined;

export type EntityKindParam = "ip" | "host" | "user";
export const ENTITY_KINDS: readonly EntityKindParam[] = ["ip", "host", "user"];

const HOUR_MS = 3_600_000;
export const ENTITY_RANGES = [
  { key: "1h", ms: HOUR_MS },
  { key: "24h", ms: 24 * HOUR_MS },
  { key: "7d", ms: 7 * 24 * HOUR_MS },
] as const;
export type EntityRangeKey = (typeof ENTITY_RANGES)[number]["key"];
export const DEFAULT_ENTITY_RANGE: EntityRangeKey = "24h";

/** The API's `value` ceiling (`app/api/routes/entities.py`). */
export const ENTITY_VALUE_MAX = 255;
/** The API's `cursor` ceiling. */
const CURSOR_MAX = 2048;

/** The code the API answers with when a bare value is not an IP address. */
export const KIND_REQUIRED_CODE = "entity_timeline.input.kind_required";

export interface EntityQuery {
  /** The typed text, trimmed; "" when the page was opened with none. */
  value: string;
  /** The kind the URL named; "" when it named none or one this page does not know. */
  kind: EntityKindParam | "";
  range: EntityRangeKey;
  cursor: string;
  /** The value is longer than the API accepts; no read is made. */
  valueTooLong: boolean;
}

function first(value: Raw): string {
  return (Array.isArray(value) ? value[0] : value) ?? "";
}

export function parseEntityQuery(params: Record<string, Raw>): EntityQuery {
  const value = first(params.value).trim();
  const kind = first(params.kind).trim().toLowerCase();
  const range = first(params.range).trim();
  const cursor = first(params.cursor).trim();
  return {
    value,
    kind: (ENTITY_KINDS as readonly string[]).includes(kind) ? (kind as EntityKindParam) : "",
    range: ENTITY_RANGES.find((r) => r.key === range)?.key ?? DEFAULT_ENTITY_RANGE,
    cursor: cursor.length <= CURSOR_MAX ? cursor : "",
    valueTooLong: value.length > ENTITY_VALUE_MAX,
  };
}

/** The API's window for a range: the last `range`, ending now. */
export function entityWindow(range: EntityRangeKey, nowMs: number): { start: string; end: string } {
  const ms = ENTITY_RANGES.find((r) => r.key === range)?.ms ?? 24 * HOUR_MS;
  return { start: new Date(nowMs - ms).toISOString(), end: new Date(nowMs).toISOString() };
}

/**
 * The address of the entity timeline. The one place a value meets a URL: every part is
 * percent-encoded, nothing is interpolated as typed. A default range is left out, and so is a
 * missing kind (the API then classifies the value).
 */
export function entityHref(parts: {
  value: string;
  kind?: EntityKindParam | "";
  range?: EntityRangeKey | "";
  cursor?: string;
}): string {
  const pairs: string[] = [];
  if (parts.kind) pairs.push(`kind=${encodeURIComponent(parts.kind)}`);
  pairs.push(`value=${encodeURIComponent(parts.value)}`);
  if (parts.range && parts.range !== DEFAULT_ENTITY_RANGE) {
    pairs.push(`range=${encodeURIComponent(parts.range)}`);
  }
  if (parts.cursor) pairs.push(`cursor=${encodeURIComponent(parts.cursor)}`);
  return `/entities?${pairs.join("&")}`;
}

/**
 * The entity kind an investigation dimension holds, or null. Decided from the dimension's name
 * and nothing else: a value that merely looks like an address in some other dimension is not
 * linked (design §6, decision 5).
 */
export function entityKindForDimension(dimension: string): EntityKindParam | null {
  if (dimension === "source_ip" || dimension === "destination_ip") return "ip";
  if (dimension === "username") return "user";
  return null;
}

/** The timeline address of an evidence value, or null when its dimension holds no entity. */
export function evidenceEntityHref(dimension: string, value: string): string | null {
  const kind = entityKindForDimension(dimension);
  if (kind === null || value.trim() === "" || value.trim().length > ENTITY_VALUE_MAX) return null;
  return entityHref({ kind, value: value.trim() });
}

/**
 * Whether a server-supplied route may be rendered as a link: a path on this origin. A scheme, a
 * protocol-relative `//host`, a backslash variant and control characters are all refused.
 */
export function isSameOriginPath(href: string): boolean {
  if (!href.startsWith("/") || href.startsWith("//") || href.startsWith("/\\")) return false;
  // eslint-disable-next-line no-control-regex
  return !/[\u0000-\u001f\u007f\\]/.test(href);
}
