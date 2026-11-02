import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { enableTurkish } from "../../vitest.setup";
import {
  ENABLED_LOCALES,
  LOCALES,
  isEnabledLocale,
  isLocale,
  localeFromCookieString,
  readLocaleCookie,
  toLocale,
  writeLocaleCookie,
} from "./locale";

// D-41: English only; Turkish is frozen behind ENABLED_LOCALES, not deleted.

function clearCookie() {
  document.cookie = "ba_locale=; Path=/; Max-Age=0";
}

beforeEach(clearCookie);
afterEach(clearCookie);

describe("the locale switch (D-41)", () => {
  it("knows both languages and has only English switched on", () => {
    expect([...LOCALES]).toEqual(["en", "tr"]);
    expect([...ENABLED_LOCALES]).toEqual(["en"]);
    expect(isLocale("tr")).toBe(true);
    expect(isEnabledLocale("tr")).toBe(false);
    expect(isEnabledLocale("en")).toBe(true);
  });

  it("resolves a locale that is not enabled to English", () => {
    expect(toLocale("tr")).toBe("en");
    expect(toLocale("en")).toBe("en");
    expect(toLocale("de")).toBe("en");
    expect(toLocale(undefined)).toBe("en");
    expect(localeFromCookieString("a=1; ba_locale=tr; b=2")).toBe("en");
  });

  it("reads a tr cookie as English", () => {
    document.cookie = "ba_locale=tr; Path=/";
    expect(readLocaleCookie()).toBe("en");
  });

  it("never writes a locale that is not enabled", () => {
    expect(writeLocaleCookie("tr")).toBe(false);
    expect(document.cookie).not.toContain("ba_locale");
    expect(writeLocaleCookie("en")).toBe(true);
    expect(document.cookie).toContain("ba_locale=en");
  });

  it("restores the Turkish behaviour when Turkish is switched back on", () => {
    enableTurkish();
    expect(isEnabledLocale("tr")).toBe(true);
    expect(toLocale("tr")).toBe("tr");
    expect(localeFromCookieString("ba_locale=tr")).toBe("tr");
    expect(writeLocaleCookie("tr")).toBe(true);
    expect(readLocaleCookie()).toBe("tr");
  });

  it("is back to English only after a test that enabled Turkish", () => {
    expect(toLocale("tr")).toBe("en");
  });
});
