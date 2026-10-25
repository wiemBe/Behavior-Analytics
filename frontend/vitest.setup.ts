import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { createTranslator } from "next-intl";
import { afterEach, vi } from "vitest";

import en from "./messages/en.json";
import tr from "./messages/tr.json";

// Existing tests predate the provider and deliberately keep rendering components directly. The
// mock supplies real next-intl translators backed by the English catalog, preserving those tests
// unchanged. New localization tests opt into Turkish with setTestLocale and every test resets to
// English below, so locale state cannot leak across the suite.
const testI18n = vi.hoisted(() => ({
  locale: "en" as "en" | "tr",
  translators: new Map<string, ReturnType<typeof createTranslator>>(),
}));

export function setTestLocale(locale: "en" | "tr") {
  testI18n.locale = locale;
}

function translator(namespace?: string) {
  const cacheKey = `${testI18n.locale}:${namespace ?? ""}`;
  const cached = testI18n.translators.get(cacheKey);
  if (cached) return cached;
  const created = createTranslator({
    locale: testI18n.locale,
    messages: testI18n.locale === "tr" ? tr : en,
    namespace: namespace as never,
    timeZone: "UTC",
  });
  testI18n.translators.set(cacheKey, created);
  return created;
}

vi.mock("next-intl", async (importOriginal) => {
  const actual = await importOriginal<typeof import("next-intl")>();
  return {
    ...actual,
    useLocale: () => testI18n.locale,
    useMessages: () => (testI18n.locale === "tr" ? tr : en),
    useTranslations: (namespace?: string) => translator(namespace),
  };
});

vi.mock("next-intl/server", () => ({
  getRequestConfig: (configure: unknown) => configure,
  getLocale: async () => testI18n.locale,
  getMessages: async () => (testI18n.locale === "tr" ? tr : en),
  getTranslations: async (namespace?: string) => translator(namespace),
}));

// jsdom implements neither of these, and the trend chart depends on both.
// Stubbing them here keeps every component test from re-declaring the same
// scaffolding.
if (!("ResizeObserver" in globalThis)) {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}
if (!("matchMedia" in globalThis)) {
  Object.defineProperty(globalThis, "matchMedia", {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  });
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  testI18n.locale = "en";
});
