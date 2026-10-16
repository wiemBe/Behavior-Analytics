// UI-5.9 point 7: a placeholder must fit the field it sits in at 1024 px, so none of these is
// the long form that ended in an ellipsis.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const read = (rel: string) => readFileSync(resolve(__dirname, "../..", rel), "utf8");

describe("placeholders that clipped at 1024 px", () => {
  it("the top search names its scope in a short form", () => {
    const src = read("components/shell/TopSearch.tsx");
    expect(src).toMatch(/placeholder="Search sources, rules, anomalies"/);
    expect(src).toMatch(/aria-label="Search sources, rules, offenses and anomalies"/);
  });
  it("the anomaly instance filter is short enough for its field", () => {
    expect(read("components/behavior/AnomalyFilters.tsx")).toMatch(/placeholder="Instance…"/);
  });
});
