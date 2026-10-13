import { describe, expect, it } from "vitest";

import { formatRate } from "./ruleStatistics";

describe("rule statistics presentation", () => {
  it("formats every match rate with one precision rule", () => {
    expect(formatRate(2.451)).toBe("2.45");
    expect(formatRate(0.305)).toBe("0.31");
    expect(formatRate(0.199)).toBe("0.20");
    expect(formatRate(0.073)).toBe("0.07");
    expect(formatRate(0.0304)).toBe("0.03");
    expect(formatRate(0.003)).toBe("< 0.01");
    expect(formatRate(0)).toBe("0.00");
    expect(formatRate(1200.4)).toBe("1,200.40");
  });
});
