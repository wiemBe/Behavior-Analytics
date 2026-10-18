import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

// The editor and the actions style themselves from propertywatch.css. A route that renders only
// them (/behavior/watches/new) gets that sheet only if they import it: a missing import renders
// the form unstyled, which no jsdom test can see (found in the PW-8 round-1 screenshots).
describe("property-watch authoring styles", () => {
  it.each(["WatchEditor.tsx", "WatchActions.tsx"])("%s imports its own stylesheet", (file) => {
    const source = readFileSync(join(__dirname, file), "utf8");
    expect(source).toMatch(/^import "\.\/propertywatch\.css";$/m);
  });
});
