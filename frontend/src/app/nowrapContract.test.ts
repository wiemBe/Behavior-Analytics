// `.nowrap` must hold at every width, on every page. jsdom does not lay out, so it is
// guarded as the CSS contract it is (guidelines §11). Found in UI-5.6b: above 1200 px a
// `td.nowrap` time wrapped on pages that do not load `components/ui/controls.css`, because
// `globals.css` defined the rule only inside `@media (max-width: 1200px)`.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(resolve(__dirname, "globals.css"), "utf8");

/** The stylesheet with every at-rule block (media queries, keyframes) cut out. */
function outsideAtRules(source: string): string {
  let depth = 0;
  let atDepth = -1;
  let out = "";
  for (let i = 0; i < source.length; i += 1) {
    const ch = source[i];
    if (ch === "{") {
      if (atDepth < 0 && /@[a-z-]+[^{};]*$/.test(source.slice(Math.max(0, i - 200), i))) {
        atDepth = depth;
      }
      depth += 1;
    }
    if (atDepth < 0) out += ch;
    if (ch === "}") {
      depth -= 1;
      if (depth === atDepth) atDepth = -1;
    }
  }
  return out;
}

describe(".nowrap is unconditional", () => {
  it("is defined outside every media query", () => {
    expect(outsideAtRules(css)).toMatch(/(?:^|\n)\.nowrap\s*\{[^}]*white-space:\s*nowrap/);
  });

  it("lets a narrow-viewport timestamp fold only when it does not carry .nowrap", () => {
    expect(css).toMatch(/td\.num\.cell-when\s*\{\s*white-space:\s*normal/);
    expect(css).toMatch(/td\.num\.cell-when\.nowrap\s*\{\s*white-space:\s*nowrap/);
  });

  it("beats the fleet-table sub-cell rule by matching it at the same specificity, after it", () => {
    // Found in UI-6.6: `.fleet-table td .cell-sub` (0,2,1) set `white-space: normal` and
    // outranked `.nowrap` (0,1,0), so an override's change time wrapped to two lines.
    expect(css).toMatch(
      /\.fleet-table td \.cell-sub\s*\{[^}]*\}[^{}]*\.fleet-table td \.nowrap\s*\{[^}]*white-space:\s*nowrap/,
    );
  });
});
