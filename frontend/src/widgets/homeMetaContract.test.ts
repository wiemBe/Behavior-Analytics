// The dashboard header's meta row and the template-description line under it. jsdom lays
// nothing out, so the pieces that depend on layout are pinned as the CSS contract they are.
// Found in UI-6.6: the row rendered even when it held nothing (an empty band separates the
// title from the description), and at narrow widths the line hid three lines behind a clamp
// that nothing could open.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(join(__dirname, "dashboard.css"), "utf8");
const stripped = css.replace(/\/\*[\s\S]*?\*\//g, "");
const ruleFor = (selector: string, property: string) =>
  [...stripped.matchAll(/([^{}]+)\{([^}]*)\}/g)].some(
    ([, selectors, body]) =>
      selectors.split(",").some((s) => s.trim() === selector) && body.includes(property),
  );

describe("the empty meta row of a saved dashboard", () => {
  it("keeps the badge row's height when it does hold one", () => {
    expect(ruleFor(".home .page-meta", "min-height: var(--sp-6)")).toBe(true);
  });

  it("renders no box at all when the session passes nothing", () => {
    expect(ruleFor(".home .page-meta:empty", "display: none")).toBe(true);
  });
});

describe("the template-description line under the title", () => {
  it("clamps to two lines only while the layout reserves the row", () => {
    expect(ruleFor(".dg-template-desc", "-webkit-line-clamp: 2")).toBe(true);
  });

  it("unwraps the full line once the narrow layout takes over", () => {
    const fold = /@media \(max-width: 1080px\)\s*\{\s*\.dg-template-desc\s*\{([^}]*)\}/.exec(stripped);
    expect(fold).not.toBeNull();
    expect(fold![1]).toContain("display: block");
    expect(fold![1]).toContain("-webkit-line-clamp: unset");
    expect(fold![1]).toContain("overflow: visible");
  });
});
