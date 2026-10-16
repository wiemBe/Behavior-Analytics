import { afterEach, describe, expect, it, vi } from "vitest";

import pkg from "../../../../package.json";
import { GET } from "./route";

afterEach(() => vi.unstubAllEnvs());

describe("health route", () => {
  it("reports the package version when the build set none", async () => {
    vi.stubEnv("NEXT_PUBLIC_APP_VERSION", "");
    expect(await GET().json()).toEqual({ status: "ok", version: pkg.version });
  });

  it("prefers NEXT_PUBLIC_APP_VERSION", async () => {
    vi.stubEnv("NEXT_PUBLIC_APP_VERSION", "9.8.7");
    expect(await GET().json()).toEqual({ status: "ok", version: "9.8.7" });
  });
});
