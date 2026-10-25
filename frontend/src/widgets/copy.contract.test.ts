import { describe, expect, it } from "vitest";

import en from "../../messages/en";
import tr from "../../messages/tr";
import { messageKey, templateDescription, templateName, widgetBlockedBy, widgetDescription, widgetTitle } from "./copy";
import { MONITORED_SOURCES, PROPERTY_WATCH_OPTIONS, WAF_SOURCE_ENDPOINTS, WAF_SOURCES } from "./optionSources";
import { WIDGETS } from "./registry";
import { SEARCH_METRIC_OPTIONS, SEARCH_OPTIONS } from "./searchResultOptions";
import { TEMPLATES } from "./templates";
import { createLocaleTranslator } from "@/i18n/fallback";

const say = (locale: "en" | "tr") => {
  const root = createLocaleTranslator(locale);
  const t = (key: string) => root(`widgets.${key}`);
  return Object.assign(t, { has: (key: string) => root.has?.(`widgets.${key}`) ?? false });
};

describe("the registry's words", () => {
  it("has a title and a description in both languages for every entry, and the English is the registry's own", () => {
    for (const locale of ["en", "tr"] as const) {
      const t = say(locale);
      for (const def of WIDGETS) {
        const title = widgetTitle(t, def);
        const description = widgetDescription(t, def);
        expect(title, `${locale} ${def.type}`).not.toBe("");
        expect(description, `${locale} ${def.type}`).not.toBe("");
        if (locale === "en") {
          expect(title).toBe(def.title);
          expect(description).toBe(def.description);
        } else {
          // A Turkish entry that fell back to the English source would be a missing message.
          expect(tr.widgets.registry[messageKey(def.type) as keyof typeof tr.widgets.registry]).toBeDefined();
        }
      }
    }
  });

  it("has no message for a type the registry does not hold", () => {
    expect(Object.keys(en.widgets.registry).sort()).toEqual(WIDGETS.map((w) => messageKey(w.type)).sort());
    expect(Object.keys(tr.widgets.registry).sort()).toEqual(WIDGETS.map((w) => messageKey(w.type)).sort());
  });

  it("words a blocked widget's reason in both languages", () => {
    for (const def of WIDGETS.filter((w) => w.status === "blocked")) {
      expect(widgetBlockedBy(say("en"), def)).toBe(def.blockedBy);
      expect(widgetBlockedBy(say("tr"), def)).not.toBe(def.blockedBy);
    }
  });

  it("shows the registry's English for a type with no message, never an empty string", () => {
    const stray = { type: "x.unknown", title: "Stray", description: "A stray widget." };
    expect(widgetTitle(say("tr"), stray)).toBe("Stray");
    expect(widgetDescription(say("tr"), stray)).toBe("A stray widget.");
  });

  it("labels every config field of every widget in both languages", () => {
    for (const def of WIDGETS) {
      for (const [key, field] of Object.entries(def.config)) {
        for (const locale of ["en", "tr"] as const) {
          const entry = (locale === "en" ? en : tr).widgets.registry[messageKey(def.type) as keyof typeof en.widgets.registry] as {
            fields?: Record<string, string>;
          };
          expect(entry.fields?.[key], `${locale} ${def.type}.${key}`).toBeTruthy();
          if (locale === "en") expect(entry.fields?.[key]).toBe((field as { label: string }).label);
        }
      }
    }
  });
});

describe("the templates' words", () => {
  it("has a name and a description in both languages, the English being the template's own", () => {
    for (const template of TEMPLATES) {
      expect(templateName(say("en"), template)).toBe(template.name);
      expect(templateDescription(say("en"), template)).toBe(template.description);
      expect(templateName(say("tr"), template)).not.toBe(template.name);
      expect(templateDescription(say("tr"), template)).not.toBe(template.description);
    }
  });
});

describe("the option sources' words", () => {
  it("has what / emptyReason (and needs) in both languages, the English being the source's own", () => {
    const sources = [MONITORED_SOURCES, WAF_SOURCES, WAF_SOURCE_ENDPOINTS, PROPERTY_WATCH_OPTIONS, SEARCH_OPTIONS, SEARCH_METRIC_OPTIONS];
    for (const source of sources) {
      expect(source.messageKey).toBeTruthy();
      const english = en.widgets.options[source.messageKey as keyof typeof en.widgets.options] as Record<string, string>;
      const turkish = tr.widgets.options[source.messageKey as keyof typeof tr.widgets.options] as Record<string, string>;
      expect(english.what).toBe(source.what);
      expect(english.emptyReason).toBe(source.emptyReason);
      expect(turkish.what).toBeTruthy();
      expect(turkish.emptyReason).toBeTruthy();
      if (source.needs) {
        expect(english.needs).toBe(source.needs);
        expect(turkish.needs).toBeTruthy();
      }
    }
  });
});
