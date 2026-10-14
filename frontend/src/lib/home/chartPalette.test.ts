import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it, vi } from "vitest";

import { HOME_TOKENS, readHomePalette, sliceColors, SEVERITY_TOKEN } from "./chartPalette";

const tokensCss = readFileSync(resolve(__dirname, "../../app/tokens.css"), "utf8");

describe("home chart palette", () => {
  it("only names tokens that tokens.css declares", () => {
    for (const name of Object.values(HOME_TOKENS)) {
      expect(tokensCss, `${name} is not declared in tokens.css`).toMatch(
        new RegExp(`^\\s*${name}:`, "m"),
      );
    }
  });

  it("maps every severity to a --sev token and never to a status hue", () => {
    for (const key of Object.values(SEVERITY_TOKEN)) {
      expect(HOME_TOKENS[key]).toMatch(/^--sev-/);
    }
  });

  it("reads the home tokens on top of the shared ones", () => {
    vi.spyOn(window, "getComputedStyle").mockReturnValue({
      getPropertyValue: (n: string) => ` v(${n}) `,
    } as unknown as CSSStyleDeclaration);
    const p = readHomePalette();
    expect(p.observed).toBe("v(--chart-observed)");
    expect(p.critical).toBe("v(--sev-critical)");
    expect(sliceColors(p)).toEqual([
      "v(--chart-cat-1)",
      "v(--chart-cat-2)",
      "v(--chart-cat-3)",
      "v(--chart-cat-4)",
      "v(--chart-other)",
    ]);
  });
});
