// UIQ-3: at 1024 px the alerts table must fit its frame. The title column's floor is what the
// other columns' fixed widths are measured against (jsdom does not lay out, so the rule is read
// from the stylesheet); 18ch was 12 px too wide and cut the "Last seen" column.

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(resolve(__dirname, "alerts.css"), "utf8");

describe("td.alert-title floor", () => {
  it("is at most 16ch so the table fits its frame at 1024 px, and still wraps long titles", () => {
    const m = /\ntd\.alert-title\s*\{([^}]*)\}/.exec(css);
    expect(m).not.toBeNull();
    const floor = /min-width:\s*(\d+)ch/.exec(m![1]);
    expect(floor).not.toBeNull();
    expect(Number(floor![1])).toBeLessThanOrEqual(16);
    expect(Number(floor![1])).toBeGreaterThanOrEqual(12);
    expect(m![1]).toMatch(/overflow-wrap:\s*anywhere/);
  });
});
