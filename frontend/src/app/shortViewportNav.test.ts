// UI-5.9 point 8, guarded as the CSS contract it is (jsdom does not lay out). The measured fit
// at 1024x768 and 1280x800 is in docs/tasks/UI-5.9.report.md.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(resolve(__dirname, "globals.css"), "utf8");

describe("the sidebar fits a short viewport without scrolling", () => {
  const m = /@media \(max-height: 820px\)\s*\{([\s\S]*?)\n\}\n/.exec(css);

  it("tightens the group rhythm on short viewports, with tokens only", () => {
    expect(m).not.toBeNull();
    expect(m![1]).toMatch(/\.nav-group \+ \.nav-group\s*\{[^}]*margin-top:\s*var\(--sp-1\)/);
    expect(m![1]).toMatch(/\.nav-group-label\s*\{[^}]*margin-bottom:\s*0/);
    expect(m![1]).not.toMatch(/\d+px/);
  });

  it("leaves the entries' own target size alone", () => {
    expect(m![1]).not.toMatch(/\.nav-link/);
  });
});
