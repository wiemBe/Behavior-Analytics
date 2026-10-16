import { describe, expect, it } from "vitest";

import { accountStatus, isLocked } from "./accountStatus";

const now = Date.parse("2026-10-16T00:00:00Z");

describe("accountStatus", () => {
  it("locks only a timestamp in the future", () => {
    expect(isLocked("2999-01-01T00:00:00Z", now)).toBe(true);
    expect(isLocked("2000-01-01T00:00:00Z", now)).toBe(false);
    expect(isLocked(null, now)).toBe(false);
    expect(isLocked("not-a-time", now)).toBe(false);
  });

  it("can be disabled and locked together, and is active otherwise", () => {
    expect(accountStatus({ disabled: false, locked_until: null }, now)).toEqual(["active"]);
    expect(accountStatus({ disabled: true, locked_until: null }, now)).toEqual(["disabled"]);
    expect(accountStatus({ disabled: false, locked_until: "2999-01-01T00:00:00Z" }, now)).toEqual([
      "locked",
    ]);
    expect(accountStatus({ disabled: true, locked_until: "2999-01-01T00:00:00Z" }, now)).toEqual([
      "disabled",
      "locked",
    ]);
  });
});
