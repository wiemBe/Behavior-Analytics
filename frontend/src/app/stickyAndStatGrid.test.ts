// UI-5.9 points 3 and 4, guarded as the CSS contract they are (jsdom does not lay out).
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(resolve(__dirname, "globals.css"), "utf8");

describe("the pinned actions column draws its rule only while the table scrolls", () => {
  it("has no static rule on the pinned cells", () => {
    const m = /\n\.sticky-actions th:last-child,\n\.sticky-actions td\.row-actions\s*\{([^}]*)\}/.exec(
      css,
    );
    expect(m).not.toBeNull();
    expect(m![1]).not.toMatch(/box-shadow/);
  });
  it("draws it from the scroll position of the table's scroller, and removes it at the end", () => {
    expect(css).toMatch(/animation-timeline:\s*scroll\(nearest inline\)/);
    const keyframes = /@keyframes sticky-actions-rule\s*\{[\s\S]*?\n\}/.exec(css)![0];
    expect(keyframes).toMatch(/from,\s*96%\s*\{\s*box-shadow:\s*inset 1px 0 0 var\(--border\)/);
    expect(keyframes).toMatch(/to\s*\{\s*box-shadow:\s*inset 0 0 0 transparent/);
  });
});

describe("the folded stat-card grid does not stretch a row to the tallest row", () => {
  it("lets rows size to their own cards at the 2x2 fold", () => {
    const fold = /@media \(max-width: 1080px\)\s*\{\s*\.grid-4\s*\{([^}]*)\}/.exec(css);
    expect(fold).not.toBeNull();
    expect(fold![1]).toMatch(/repeat\(2,/);
    expect(fold![1]).toMatch(/grid-auto-rows:\s*auto/);
  });
});
