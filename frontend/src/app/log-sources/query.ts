// The address of the log source list: a search word and an offset.

/** Rows per page; the API's own default is 200 and its maximum is `api_max_page_size`. */
export const LOG_SOURCES_PAGE_SIZE = 100;
export const Q_MAX = 200;

export type RawQuery = Record<string, string | string[] | undefined>;

function first(value: string | string[] | undefined): string {
  return (Array.isArray(value) ? value[0] : value) ?? "";
}

/** The address is the state: a search word and an offset, nothing else. */
export function parseLogSourceQuery(raw: RawQuery): { q: string; offset: number } {
  const q = first(raw.q).trim().slice(0, Q_MAX);
  const offset = Number.parseInt(first(raw.offset), 10);
  return { q, offset: Number.isFinite(offset) && offset > 0 ? offset : 0 };
}
