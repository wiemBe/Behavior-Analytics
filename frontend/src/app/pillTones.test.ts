// COLOR-2 / D-85. Colour is spent only where the analyst must act: of the four tone classes on a
// `.pill`, only `.crit` keeps the outlined, filled badge. `.ok` and `.info` read as plain
// secondary text, `.warn` as warning-toned text without a fill, and the word stays in every case
// (guidelines §10). Selected UI (`.tab-active`) uses the focus role, not the data accent.
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(path.resolve(__dirname, "globals.css"), "utf8");

/** The declaration block of the first rule whose selector list is exactly `selector`. */
function block(selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/,\s*/g, ",\\s*");
  const match = new RegExp(`(?:\\}|\\*/)\\s*${escaped}\\s*\\{([^}]*)\\}`, "m").exec(css);
  if (!match) throw new Error(`no rule for ${selector}`);
  return match[1];
}

describe("pill tones (D-85)", () => {
  it("drops fill, border and padding for ok, info and warn", () => {
    const plain = block(".pill.ok,\n.pill.info,\n.pill.warn");
    expect(plain).toMatch(/border:\s*0/);
    expect(plain).toMatch(/background:\s*none/);
    expect(plain).toMatch(/padding:\s*0/);
  });

  it("renders ok and info as plain secondary text", () => {
    expect(block(".pill.ok,\n.pill.info")).toMatch(/color:\s*var\(--text-2\)/);
  });

  it("renders warn as warning text with no fill", () => {
    const warn = block(".pill.warn");
    expect(warn).toMatch(/color:\s*var\(--warn-text\)/);
    expect(warn).not.toMatch(/background|border/);
  });

  it("keeps the crit pill filled and outlined", () => {
    const crit = block(".pill.crit");
    expect(crit).toMatch(/background:\s*var\(--crit-bg\)/);
    expect(crit).toMatch(/border-color:\s*var\(--crit-border\)/);
  });

  it("underlines the selected tab with the focus role, not the data accent", () => {
    const active = block(".tab-active");
    expect(active).toMatch(/border-bottom-color:\s*var\(--focus\)/);
    expect(active).not.toMatch(/--info/);
  });

  it("reads essential small text at --text-2", () => {
    for (const sel of ["th", ".card .k", ".metric-note", ".measure-note", ".fm-desc"]) {
      expect(block(sel), sel).toMatch(/color:\s*var\(--text-2\)/);
    }
  });

  it("has no second .pill.info rule restoring the cyan fill", () => {
    expect(css.match(/\.pill\.info\s*\{/g)).toHaveLength(1);
  });

  it("draws a quiet CRITICAL/HIGH severity as a text label without padding", () => {
    expect(block(".pill.pill-quiet.crit")).toMatch(/padding:\s*0/);
  });

  it("renders a calm metric value as plain text", () => {
    expect(block(".card .v.ok")).toMatch(/color:\s*var\(--text\)/);
  });

  it("keeps a disabled rule row at --text-3", () => {
    expect(block(".rule-off td,\n.rule-off th")).toMatch(/color:\s*var\(--text-3\)/);
  });
});
