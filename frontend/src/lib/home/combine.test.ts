import { describe, expect, it } from "vitest";

import type { ResourceState } from "@/hooks/useApiResource";

import { combine2 } from "./combine";

const ok = <T,>(
  data: T,
  receivedAt: string,
  over: Partial<Extract<ResourceState<T>, { status: "ok" }>> = {},
): ResourceState<T> => ({
  status: "ok",
  data,
  receivedAt,
  refreshing: false,
  refreshFailed: null,
  ...over,
});

describe("combine2", () => {
  it("is ok with both values and the older receive time", () => {
    const s = combine2(ok(1, "2026-10-14T10:05:00Z"), ok("x", "2026-10-14T10:00:00Z"));
    expect(s).toMatchObject({ status: "ok", data: [1, "x"], receivedAt: "2026-10-14T10:00:00Z" });
  });

  it("is forbidden when either read is refused, ahead of an error", () => {
    expect(
      combine2({ status: "error", httpStatus: 500 }, { status: "forbidden", httpStatus: 403 }),
    ).toEqual({ status: "forbidden", httpStatus: 403 });
  });

  it("is an error when one failed, even while the other loads", () => {
    expect(combine2({ status: "loading" }, { status: "error", httpStatus: 502 })).toEqual({
      status: "error",
      httpStatus: 502,
    });
  });

  it("is loading until both are in", () => {
    expect(combine2(ok(1, "2026-10-14T10:00:00Z"), { status: "loading" })).toEqual({
      status: "loading",
    });
  });

  it("reports a failed refresh of either read", () => {
    const s = combine2(
      ok(1, "2026-10-14T10:00:00Z"),
      ok(2, "2026-10-14T10:00:00Z", { refreshFailed: { httpStatus: 503 } }),
    );
    expect(s).toMatchObject({ status: "ok", refreshFailed: { httpStatus: 503 } });
  });
});
