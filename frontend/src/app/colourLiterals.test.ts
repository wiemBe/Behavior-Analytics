// UI-6.4. The palette contract from tokens.css (rules 1–2 and D-11): every
// colour is declared in `src/app/tokens.css`; no other stylesheet carries a
// colour literal, so a palette change is a token change and nothing else.
// The three values that name a colour but stay allowed are `transparent`,
// `currentColor` and `inherit`.
//
// `contrast.test.ts` reads globals.css, fonts.css and the component
// stylesheets for the WCAG arithmetic; this contract sweeps every stylesheet
// under `src`, so a new route stylesheet is covered the moment it lands.
import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const appDir = path.resolve(__dirname);

/** Every CSS named colour except the three allowed keywords. */
const NAMED_COLOURS = new Set(
  `aliceblue antiquewhite aqua aquamarine beige bisque black blanchedalmond blue blueviolet
   brown burlywood cadetblue chartreuse chocolate coral cornflowerblue cornsilk crimson cyan
   darkblue darkcyan darkgoldenrod darkgray darkgreen darkgrey darkkhaki darkmagenta
   darkolivegreen darkorange darkorchid darkred darksalmon darkseagreen darkslateblue
   darkslategray darkgrey darkturquoise darkviolet deeppink deepskyblue dimgray dimgrey
   dodgerblue firebrick floralwhite forestgreen fuchsia gainsboro ghostwhite gold goldenrod
   gray green greenyellow grey honeydew hotpink indianred indigo ivory khaki lavender
   lavenderblush lawngreen lemonchiffon lightblue lightcoral lightcyan lightgoldenrodyellow
   lightgray lightgreen lightgrey lightpink lightsalmon lightseagreen lightskyblue
   lightslategray lightsteelblue lightyellow lime limegreen linen magenta maroon
   mediumaquamarine mediumblue mediumorchid mediumpurple mediumseagreen mediumslateblue
   mediumspringgreen mediumturquoise mediumvioletred midnightblue mintcream mistyrose moccasin
   navajowhite navy oldlace olive olivedrab orange orangered orchid palegoldenrod palegreen
   paleturquoise palevioletred papayawhip peachpuff peru pink plum powderblue purple
   rebeccapurple rosybrown royalblue saddlebrown salmon sandybrown seagreen seashell sienna
   silver skyblue slateblue slategray snow springgreen steelblue tan thistle tomato turquoise
   violet wheat white whitesmoke yellow yellowgreen`
    .split(/\s+/)
    .map((word) => word.trim())
    .filter(Boolean),
);

function cssFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const p = path.join(dir, name);
    if (statSync(p).isDirectory()) return cssFiles(p);
    return p.endsWith(".css") ? [p] : [];
  });
}

describe("no colour literal outside tokens.css", () => {
  const files = cssFiles(path.resolve(__dirname, "..")).filter(
    (file) => path.basename(file) !== "tokens.css",
  );

  it("covers every stylesheet under src (regression guard on the sweep itself)", () => {
    expect(files.length).toBeGreaterThan(20);
  });

  for (const file of files) {
    it(`${path.relative(appDir, file)} has none`, () => {
      // Comments are prose and may name a colour; strip them first.
      const css = readFileSync(file, "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
      expect(css, "hex literal").not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
      expect(css, "rgb/rgba/hsl/hsla function").not.toMatch(/\b(?:rgba?|hsla?)\(/);
      // A named colour is only a colour inside a declaration's value:
      // `white-space` is a property name, never a value word.
      const words = [...css.matchAll(/:\s*([^;{}]+)[;{]/g)].flatMap((m) =>
        m[1].split(/[\s,()]+/),
      );
      const coloured = words.filter(
        (word) => NAMED_COLOURS.has(word.toLowerCase()) && !/^(transparent|currentcolor|inherit)$/.test(word.toLowerCase()),
      );
      expect(coloured, `named colours: ${coloured.join(", ")}`).toEqual([]);
    });
  }
});
