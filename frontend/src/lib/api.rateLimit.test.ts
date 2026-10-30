import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, actionErrorMessage, parseRetryAfter, request } from "@/lib/api";

// A 429 keeps the response's Retry-After on the ApiError (whole seconds), so a page can say
// when to retry. Only the delay-seconds form is read.

afterEach(() => vi.unstubAllGlobals());

async function failWith(headers: Record<string, string>): Promise<ApiError> {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      new Response(JSON.stringify({ detail: "rate limit exceeded", limit: "60 per 1 minute" }), {
        status: 429,
        headers,
      }),
    ),
  );
  return request("/alerts").then(
    () => {
      throw new Error("expected a failure");
    },
    (err: unknown) => err as ApiError,
  );
}

describe("ApiError.retryAfter", () => {
  it("keeps Retry-After seconds from a 429", async () => {
    const err = await failWith({ "Retry-After": "42" });
    expect(err.status).toBe(429);
    expect(err.retryAfter).toBe(42);
  });

  it("is null when the 429 sent none", async () => {
    expect((await failWith({})).retryAfter).toBeNull();
  });

  it.each(["0", "-3", "abc", "Wed, 21 Oct 2026 07:28:00 GMT", "1.5", ""])(
    "reads %j as no wait",
    (value) => expect(parseRetryAfter(value)).toBeNull(),
  );

  it("names the wait in the action message", () => {
    expect(actionErrorMessage(new ApiError(429, "x", undefined, undefined, 7))).toContain("in 7 s.");
    expect(actionErrorMessage(new ApiError(429, "x"))).toContain("in a minute.");
  });
});
