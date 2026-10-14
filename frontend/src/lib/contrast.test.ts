import { readFileSync, readdirSync, statSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const appDir = path.resolve(__dirname, "../app");
const tokensCss = readFileSync(path.join(appDir, "tokens.css"), "utf8");

/** Resolve a token to its #rrggbb value, following `var(--x)` references. */
function token(name: string): string {
  const m = new RegExp(`${name}:\\s*([^;]+);`).exec(tokensCss);
  if (!m) throw new Error(`token ${name} missing`);
  const v = m[1].trim();
  const ref = /^var\((--[\w-]+)\)$/.exec(v);
  return ref ? token(ref[1]) : v;
}

type Rgb = [number, number, number];

function parse(value: string): Rgb {
  const hex = /^#([0-9a-f]{6})$/i.exec(value);
  if (hex) {
    const n = parseInt(hex[1], 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  throw new Error(`not a hex colour: ${value}`);
}

/** An rgba(r, g, b, a) token composited over an opaque background. */
function over(rgbaToken: string, bg: Rgb): Rgb {
  const m = /^rgba\((\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\)$/.exec(token(rgbaToken));
  if (!m) throw new Error(`not rgba: ${rgbaToken}`);
  const a = Number(m[4]);
  return [0, 1, 2].map((i) => Math.round(Number(m[i + 1]) * a + bg[i] * (1 - a))) as Rgb;
}

function lum([r, g, b]: Rgb): number {
  const c = [r, g, b].map((v) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
}

function ratio(a: Rgb, b: Rgb): number {
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

const col = (name: string): Rgb => parse(token(name));

const SURFACES = ["--canvas", "--sidebar-bg", "--surface", "--surface-2", "--surface-3"];

describe("text contrast (WCAG 2.x, UR.2.8)", () => {
  const AA = 4.5;
  for (const fg of ["--text", "--text-2", "--neutral-text"]) {
    for (const bg of SURFACES) {
      it(`${fg} on ${bg} is at least ${AA}:1`, () => {
        expect(ratio(col(fg), col(bg))).toBeGreaterThanOrEqual(AA);
      });
    }
  }

  it("pins --text-3 to the palette's muted text", () => {
    // D-11, the user's decision of 2026-10-14: keep the palette value #73757B
    // even though it is below AA. Deliberately NOT asserted against 4.5:1.
    expect(token("--text-3").toUpperCase()).toBe("#73757B");
  });

  it("pins --border-strong to the palette's border (D-11)", () => {
    expect(token("--border-strong").toLowerCase()).toBe("#35363a");
  });

  it("has no --text-disabled token (D-11)", () => {
    expect(tokensCss.replace(/\/\*[\s\S]*?\*\//g, "")).not.toMatch(/--text-disabled/);
  });
});

describe("status text pairs", () => {
  const tones = ["crit", "warn", "ok", "info", "neutral"];
  for (const tone of tones) {
    const text = col(`--${tone}-text`);
    it(`--${tone}-text on surface and elevated is at least 4.5:1`, () => {
      expect(ratio(text, col("--surface"))).toBeGreaterThanOrEqual(4.5);
      expect(ratio(text, col("--surface-2"))).toBeGreaterThanOrEqual(4.5);
    });
    it(`--${tone}-text on its tint over surface and hover is at least 4.5:1`, () => {
      expect(ratio(text, over(`--${tone}-bg`, col("--surface")))).toBeGreaterThanOrEqual(4.5);
      expect(ratio(text, over(`--${tone}-bg`, col("--surface-3")))).toBeGreaterThanOrEqual(4.5);
    });
  }
});

describe("controls", () => {
  it("--on-accent on --primary is at least 4.5:1", () => {
    expect(ratio(col("--on-accent"), col("--primary"))).toBeGreaterThanOrEqual(4.5);
    expect(ratio(col("--on-accent"), col("--primary-hover"))).toBeGreaterThanOrEqual(4.5);
  });
  it("--focus on canvas, surface and elevated is at least 3:1", () => {
    for (const bg of ["--canvas", "--surface", "--surface-2"]) {
      expect(ratio(col("--focus"), col(bg))).toBeGreaterThanOrEqual(3);
    }
  });
});

describe("chart palette", () => {
  it("never uses a status hue as a categorical series", () => {
    const status = ["--ok", "--warn", "--crit"].map((n) => token(n).toLowerCase());
    for (let i = 1; i <= 5; i++) {
      expect(status).not.toContain(token(`--chart-cat-${i}`).toLowerCase());
    }
  });
  it("categorical marks are at least 3:1 on surface", () => {
    for (let i = 1; i <= 5; i++) {
      expect(ratio(col(`--chart-cat-${i}`), col("--surface"))).toBeGreaterThanOrEqual(3);
    }
  });
});

describe("no colour literal outside tokens.css", () => {
  function cssFiles(dir: string): string[] {
    return readdirSync(dir).flatMap((name) => {
      const p = path.join(dir, name);
      if (statSync(p).isDirectory()) return cssFiles(p);
      return p.endsWith(".css") ? [p] : [];
    });
  }
  const files = [
    path.join(appDir, "globals.css"),
    path.join(appDir, "fonts.css"),
    ...cssFiles(path.resolve(__dirname, "../components")),
  ];
  for (const file of files) {
    it(`${path.relative(appDir, file)} has none`, () => {
      // Strip comments, then look for #hex, rgb( and rgba(.
      const css = readFileSync(file, "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
      expect(css).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
      expect(css).not.toMatch(/\brgba?\(/);
    });
  }
});
