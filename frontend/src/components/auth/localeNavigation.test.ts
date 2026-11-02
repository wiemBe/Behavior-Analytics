import { afterEach, describe, expect, it, vi } from "vitest";

import { sessionNavigation } from "@/lib/api";

import { continueInAccountLanguage } from "./localeNavigation";
import { enableTurkish } from "../../../vitest.setup";

afterEach(() => {
  document.cookie = "ba_locale=; Path=/; Max-Age=0";
  vi.restoreAllMocks();
});

describe("continueInAccountLanguage", () => {
  it("loads the document when the account language differs from the rendered page", () => {
    enableTurkish();
    document.cookie = "ba_locale=tr; Path=/";
    const assign = vi.spyOn(sessionNavigation, "assign").mockImplementation(() => {});
    const replace = vi.fn();
    continueInAccountLanguage("en", "/alerts", replace);
    expect(assign).toHaveBeenCalledWith("/alerts");
    expect(replace).not.toHaveBeenCalled();
  });

  it("keeps a client navigation when the cookie names a locale that is not enabled (D-41)", () => {
    document.cookie = "ba_locale=tr; Path=/";
    const assign = vi.spyOn(sessionNavigation, "assign").mockImplementation(() => {});
    const replace = vi.fn();
    continueInAccountLanguage("en", "/alerts", replace);
    expect(replace).toHaveBeenCalledWith("/alerts");
    expect(assign).not.toHaveBeenCalled();
  });

  it("keeps a client navigation when the rendered language already matches", () => {
    document.cookie = "ba_locale=en; Path=/";
    const assign = vi.spyOn(sessionNavigation, "assign").mockImplementation(() => {});
    const replace = vi.fn();
    continueInAccountLanguage("en", "/alerts", replace);
    expect(replace).toHaveBeenCalledWith("/alerts");
    expect(assign).not.toHaveBeenCalled();
  });
});
