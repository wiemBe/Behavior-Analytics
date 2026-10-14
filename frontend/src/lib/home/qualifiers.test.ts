import { describe, expect, it } from "vitest";

import { badgeText, insufficient, notCollected, partial, truncated } from "./qualifiers";

describe("badgeText", () => {
  it("is null without a caveat", () => {
    expect(badgeText([])).toBeNull();
  });

  it("names each kind in words", () => {
    expect(badgeText([partial(20, 24, "steps")])).toBe("Partial: 20 of 24 steps collected");
    expect(badgeText([truncated(200, 345)])).toBe("Truncated: first 200 of 345");
    expect(badgeText([notCollected("x")])).toBe("Not collected");
    expect(badgeText([insufficient("x")])).toBe("Insufficient data");
  });

  it("puts a state with nothing to draw before a caveat on what is drawn", () => {
    expect(badgeText([partial(1, 2, "steps"), insufficient("x")])).toBe("Insufficient data");
    expect(badgeText([partial(1, 2, "steps"), truncated(1, 2)])).toBe("Truncated: first 1 of 2");
  });
});
