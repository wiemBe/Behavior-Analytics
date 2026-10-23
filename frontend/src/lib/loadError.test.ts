import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api";
import { loadErrorMessage } from "@/lib/loadError";

describe("loadErrorMessage", () => {
  it("names the grant on a 403", () => {
    expect(loadErrorMessage(new ApiError(403, "no"), "anomalies", "read:anomalies")).toBe(
      "You do not have permission to view anomalies. Ask an administrator for the read:anomalies grant.",
    );
  });

  it("says the session expired on a 401", () => {
    expect(loadErrorMessage(new ApiError(401, "no"), "alerts", "read:alerts")).toBe(
      "Your session has expired. Sign in again to continue.",
    );
  });

  it("falls back for any other ApiError", () => {
    expect(loadErrorMessage(new ApiError(500, "boom"), "alerts", "read:alerts")).toBe(
      "Alerts could not be loaded. The backend may be unreachable; reload the page to try again.",
    );
  });

  it("treats a plain Error (a network failure) as the third case", () => {
    expect(loadErrorMessage(new Error("fetch failed"), "log sources", "read:log_sources")).toBe(
      "Log sources could not be loaded. The backend may be unreachable; reload the page to try again.",
    );
  });
});
