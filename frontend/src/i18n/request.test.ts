import { beforeEach, describe, expect, it, vi } from "vitest";

const cookie = vi.hoisted(() => ({ value: undefined as string | undefined }));
vi.mock("next/headers", () => ({
  cookies: async () => ({
    get: (name: string) => (name === "ba_locale" && cookie.value != null ? { value: cookie.value } : undefined),
  }),
}));

import { requestConfiguration } from "./request";
import { enableTurkish } from "../../vitest.setup";

describe("request locale", () => {
  beforeEach(() => {
    cookie.value = undefined;
  });

  it.each([
    ["tr", "en"],
    ["en", "en"],
    ["de", "en"],
    [undefined, "en"],
  ])("maps cookie %s to %s", async (value, expected) => {
    cookie.value = value;
    const config = await requestConfiguration();
    expect(config.locale).toBe(expected);
    expect(config.timeZone).toBe("UTC");
    expect(config.messages).toHaveProperty("common.product", "Behavior Analytics");
    expect(config.messages).toHaveProperty("account.title", "Account settings");
  });

  it("serves the Turkish catalog for a tr cookie once Turkish is enabled (D-41)", async () => {
    enableTurkish();
    cookie.value = "tr";
    const config = await requestConfiguration();
    expect(config.locale).toBe("tr");
    expect(config.messages).toHaveProperty("account.title", "Hesap ayarları");
  });
});
