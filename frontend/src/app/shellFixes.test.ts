// The three shell defects left by T-021. jsdom does not lay out, so the two CSS
// ones are guarded as the CSS contract they are (guidelines §11).
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(resolve(__dirname, "globals.css"), "utf8");

function rule(selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = new RegExp(`(?:^|\\n)${escaped}\\s*\\{([^}]*)\\}`).exec(css);
  expect(match, `rule ${selector}`).not.toBeNull();
  return match![1];
}

describe("sidebar runs the full page height", () => {
  it("paints the panel on a grid-row-spanning pseudo-element, not on the sticky sidebar", () => {
    expect(rule(".layout::before")).toMatch(/background:\s*var\(--surface\)/);
    expect(rule(".sidebar")).not.toMatch(/background/);
  });
  it("keeps the sidebar and the panel in the same cell", () => {
    expect(rule(".layout::before")).toMatch(/grid-column:\s*1/);
    expect(rule(".sidebar")).toMatch(/grid-column:\s*1/);
    expect(rule(".sidebar")).toMatch(/position:\s*sticky/);
  });
});

describe("the stat-card row has even rows", () => {
  it("sizes every row of the four-up grid alike", () => {
    expect(rule(".grid-4")).toMatch(/grid-auto-rows:\s*1fr/);
  });
});
