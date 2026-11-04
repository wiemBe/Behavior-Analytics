// UX-5: two layout rules that live in route CSS. A deleted rule fails here; the screenshots are
// what show the table and the frame actually fit.
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const siemload = readFileSync(join(__dirname, "../components/siemload/siemload.css"), "utf8");
const geo = readFileSync(join(__dirname, "../components/geo/geo.css"), "utf8");

describe("UX-5 Load by caller", () => {
  it("lets the caller name wrap so a long label never widens the table past its frame", () => {
    expect(siemload).toMatch(
      /\.sl-callers-table th\[scope="row"\]\s*\{[^}]*white-space:\s*normal[^}]*overflow-wrap:\s*anywhere/,
    );
  });
});

describe("UX-5 Geography without a map", () => {
  it("sizes a no-map frame to its content in the fixed grid, not to the layout's box", () => {
    expect(geo).toMatch(
      /\.dg\[data-cols="12"\] \.dg-item > \.tw:has\(\.geo-hollow\)\s*\{[^}]*align-self:\s*flex-start/,
    );
  });
  it("leaves the map state and the drag-and-resize grid alone", () => {
    expect(geo).not.toMatch(/^[^/*\n]*\.dg-edit[^\n]*:has\(/m);
    expect(geo.match(/align-self:\s*flex-start/g)).toHaveLength(1);
  });
});
