// The messages the browser receives. The guide's text is long and only server components read it
// (`getTranslations("guide")`), so the root layout hands the client provider the catalog without
// that namespace. Everything else stays: per-route namespaces are I18N-12b's.

/** Namespaces only the server renders. */
export const SERVER_ONLY_NAMESPACES = ["guide"] as const;

export function clientMessages<T extends Record<string, unknown>>(
  messages: T,
): Omit<T, (typeof SERVER_ONLY_NAMESPACES)[number]> {
  const kept: Record<string, unknown> = {};
  for (const [namespace, value] of Object.entries(messages)) {
    if (!(SERVER_ONLY_NAMESPACES as readonly string[]).includes(namespace)) kept[namespace] = value;
  }
  return kept as Omit<T, (typeof SERVER_ONLY_NAMESPACES)[number]>;
}
