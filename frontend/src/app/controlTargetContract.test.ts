// The checkbox, pager links, radio labels, row toggles and toggle labels on the list and
// admin pages are the operator's click targets. WCAG 2.5.8 wants 24 px for each; jsdom lays
// nothing out, so the size is pinned as the CSS contract it is (guidelines §11), the same way
// `WidgetFrame.chrome.test.tsx` pins its own controls. Found in UI-6.6: five of them measured
// 19–21 px.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(join(__dirname, "globals.css"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
const ruleFor = (selector: string, property: string) =>
  [...css.matchAll(/([^{}]+)\{([^}]*)\}/g)].some(
    ([, selectors, body]) =>
      selectors.split(",").some((s) => s.trim() === selector) && body.includes(property),
  );

describe("hit targets of at least --target-min", () => {
  it.each([".row-toggle", "label.filter-field.filter-check", ".pagination-links a", ".fm-radio", ".toggle"])(
    "%s has the minimum height",
    (selector) => {
      expect(ruleFor(selector, "min-height: var(--target-min)")).toBe(true);
    },
  );

  it("gives the bare controls a flex box the min-height can act on", () => {
    expect(ruleFor(".row-toggle", "display: inline-flex")).toBe(true);
    expect(ruleFor(".pagination-links a", "display: inline-flex")).toBe(true);
    expect(ruleFor(".fm-radio", "display: inline-flex")).toBe(true);
    expect(ruleFor(".toggle", "display: inline-flex")).toBe(true);
  });

  it("centres the content of the targets that gained height", () => {
    expect(ruleFor(".row-toggle", "align-items: center")).toBe(true);
    expect(ruleFor(".pagination-links a", "align-items: center")).toBe(true);
    expect(ruleFor(".fm-radio", "align-items: center")).toBe(true);
    expect(ruleFor(".toggle", "align-items: center")).toBe(true);
  });

  it("grows the targets without changing a type size", () => {
    // A larger font would change the layout of the table pages; the target grows, not the text.
    expect(ruleFor(".row-toggle", "font-size: var(--fs-xs)")).toBe(true);
    expect(ruleFor(".fm-radio", "font-size: var(--fs-sm)")).toBe(true);
  });
});
