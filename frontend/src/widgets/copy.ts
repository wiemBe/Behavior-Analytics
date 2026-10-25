// The interface words of the widget system that belong to a registry entry or a template, looked
// up by its `type` / `key`. The registry and the templates keep their English text as the source
// of truth for tests and for a type this catalog does not know (a test registry, a type added
// before its messages): a missing message falls back to that text, never to an empty string.
// `copy.contract.test.ts` pins that every entry of the real registry has both languages.

import { useLocale, useTranslations } from "next-intl";

import type { AnyWidgetDef, WidgetCategory, WidgetRange } from "./types";

/** `kpi.total_eps` is not a message path (a dot reads as nesting), so it becomes `kpi_total_eps`. */
export function messageKey(type: string): string {
  return type.replace(/\./g, "_");
}

/** next-intl's translator for one namespace, as far as this module needs it. */
interface Say {
  (key: string, values?: Record<string, string | number>): string;
  has(key: string): boolean;
}

function lookup(t: Say, key: string | null, fallback: string): string {
  return key !== null && t.has(key) ? t(key) : fallback;
}

/** The message path of an entry; none for a definition that carries no `type` (a test's stand-in). */
const path = (type: string | undefined, rest: string): string | null =>
  type ? `registry.${messageKey(type)}.${rest}` : null;

export function widgetTitle(t: Say, def: Pick<AnyWidgetDef, "type" | "title">): string {
  return lookup(t, path(def.type, "title"), def.title);
}

export function widgetDescription(
  t: Say,
  def: Pick<AnyWidgetDef, "type" | "description">,
): string {
  return lookup(t, path(def.type, "description"), def.description);
}

export function widgetBlockedBy(t: Say, def: Pick<AnyWidgetDef, "type" | "blockedBy">): string {
  return lookup(t, path(def.type, "blockedBy"), def.blockedBy ?? "");
}

export function templateName(t: Say, template: { key: string; name: string }): string {
  return lookup(t, template.key ? `templates.${template.key}.name` : null, template.name);
}

export function templateDescription(
  t: Say,
  template: { key: string; description: string },
): string {
  return lookup(t, template.key ? `templates.${template.key}.description` : null, template.description);
}

/** The words of the registry and the templates, bound to the current interface language. */
export function useWidgetCopy() {
  const t = useTranslations("widgets") as unknown as Say;
  const units = useTranslations("units") as unknown as Say;
  const locale = useLocale();
  return {
    title: (def: Pick<AnyWidgetDef, "type" | "title">) => widgetTitle(t, def),
    description: (def: Pick<AnyWidgetDef, "type" | "description">) => widgetDescription(t, def),
    blockedBy: (def: Pick<AnyWidgetDef, "type" | "blockedBy">) => widgetBlockedBy(t, def),
    templateName: (template: { key: string; name: string }) => templateName(t, template),
    templateDescription: (template: { key: string; description: string }) =>
      templateDescription(t, template),
    category: (category: WidgetCategory) => t(`categories.${category}`),
    range: (range: WidgetRange) => units(`range.${range}`),
    /** "Not available: <reason>." for a widget that cannot be placed. */
    notAvailable: (def: Pick<AnyWidgetDef, "type" | "blockedBy">) =>
      t("notAvailable", { reason: widgetBlockedBy(t, def) }),
    locale,
  };
}

/** The range text outside a hook (a translator for the `units` namespace is passed in). */
export function rangeText(units: Say, range: WidgetRange): string {
  return units(`range.${range}`);
}
