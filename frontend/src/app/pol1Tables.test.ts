// POL-1: the two table fixes live in route CSS. A deleted rule fails here; the
// screenshots are what show the header and the error column actually fit.
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const customRules = readFileSync(join(__dirname, "custom-rules/custom-rules.css"), "utf8");
const searches = readFileSync(join(__dirname, "../components/searches/searches.css"), "utf8");

describe("POL-1 table layout", () => {
  it("lets the custom-rules severity header wrap inside a token floor", () => {
    expect(customRules).toMatch(/\.cr-rule-table th:nth-child\(5\)[\s\S]*white-space:\s*normal/);
    expect(customRules).toMatch(/nth-child\(5\)[\s\S]*min-width:\s*var\(--table-col-time\)/);
    expect(customRules).toMatch(/nth-child\(4\)[\s\S]*min-width:\s*var\(--table-col-magnitude\)/);
    expect(customRules).toMatch(/@media \(max-width: 1200px\)[\s\S]*nth-child\(2\)[\s\S]*min-width:\s*var\(--table-col-figures\)/);
    expect(customRules).not.toMatch(/overflow-x:\s*hidden/);
  });

  it("fits the run table, including its error column, at the 1200 breakpoint", () => {
    expect(searches).toMatch(/@media \(max-width: 1200px\)[\s\S]*\.executions-table[\s\S]*table-layout:\s*fixed/);
    expect(searches).toMatch(/nth-child\(5\)[\s\S]*width:\s*11%/);
    expect(searches).toMatch(/nth-child\(8\)[\s\S]*width:\s*12%/);
    expect(searches).toMatch(/nth-child\(9\)[\s\S]*width:\s*10%/);
    expect(searches).not.toMatch(/overflow-x:\s*hidden/);
  });
});
