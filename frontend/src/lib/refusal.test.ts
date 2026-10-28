import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api";

import { missingPermission, refusalSentences } from "./refusal";

const fail = (status: number, body: Record<string, unknown>) =>
  new ApiError(
    status,
    typeof body.detail === "string" ? body.detail : "failed",
    body.detail,
    body,
  );

describe("refusalSentences", () => {
  it("reads a string detail with its code and params", () => {
    const error = fail(409, {
      detail: "you already have a dashboard named 'Main'",
      detail_code: "dashboard.name_taken",
      detail_params: { name: "Main" },
    });
    expect(refusalSentences(error)).toEqual([
      {
        text: "you already have a dashboard named 'Main'",
        code: "dashboard.name_taken",
        params: { name: "Main" },
      },
    ]);
  });

  it("reads a string detail from an older server as text with no code", () => {
    expect(refusalSentences(fail(409, { detail: "duplicate" }))).toEqual([
      { text: "duplicate", code: null, params: null },
    ]);
  });

  it("reads a list, pairing each sentence with the code at its index", () => {
    const error = fail(422, {
      detail: ["too short", "equals the username", "something new"],
      detail_codes: [
        { code: "auth.password.too_short", params: {} },
        { code: "auth.password.equals_username", params: {} },
      ],
    });
    expect(refusalSentences(error)).toEqual([
      { text: "too short", code: "auth.password.too_short", params: {} },
      { text: "equals the username", code: "auth.password.equals_username", params: {} },
      { text: "something new", code: null, params: null },
    ]);
  });

  it("skips blanks and non-strings in a list, keeping the index of the rest", () => {
    const error = fail(422, {
      detail: ["", { msg: "schema" }, "kept"],
      detail_codes: [null, null, { code: "a.b", params: {} }],
    });
    expect(refusalSentences(error)).toEqual([{ text: "kept", code: "a.b", params: {} }]);
  });

  it("reads the message of an object detail and the code inside it", () => {
    const error = fail(409, {
      detail: {
        message: "this dashboard changed since you opened it",
        current_revision: 4,
        code: "dashboard.changed_since_opened",
        params: {},
      },
    });
    expect(refusalSentences(error)).toEqual([
      {
        text: "this dashboard changed since you opened it",
        code: "dashboard.changed_since_opened",
        params: {},
      },
    ]);
  });

  it("ignores a code that is not a string and params that are not an object", () => {
    const error = fail(409, { detail: "x", detail_code: 7, detail_params: ["a"] });
    expect(refusalSentences(error)).toEqual([{ text: "x", code: null, params: null }]);
  });

  it("is empty for an empty detail, an absent one, and a detail with no message", () => {
    expect(refusalSentences(fail(500, { detail: "" }))).toEqual([]);
    expect(refusalSentences(new ApiError(500, "boom"))).toEqual([]);
    expect(refusalSentences(fail(422, { detail: { reasons: ["a"] } }))).toEqual([]);
  });
});

describe("missingPermission", () => {
  const coded = (status: number, permission: unknown) =>
    fail(status, {
      detail: "missing required permission: admin:users",
      detail_code: "auth.permission.missing",
      detail_params: { permission },
    });

  it("names the permission of a coded 403", () => {
    expect(missingPermission(coded(403, "admin:users"))).toBe("admin:users");
  });

  it("is null for another status, another code, or a permission that is not text", () => {
    expect(missingPermission(coded(409, "admin:users"))).toBeNull();
    expect(missingPermission(coded(403, 5))).toBeNull();
    expect(missingPermission(coded(403, ""))).toBeNull();
    expect(
      missingPermission(fail(403, { detail: "no", detail_code: "dashboard.owner_only", detail_params: {} })),
    ).toBeNull();
    expect(missingPermission(new ApiError(403, "missing required permission: admin:users"))).toBeNull();
  });
});
