import { describe, expect, it } from "vitest";

import en from "../../messages/en";
import tr from "../../messages/tr";
import { GUIDE_GROUPS, GUIDE_SECTIONS, guideSection } from "./guideSections";
import { parseBlocks, parseGuideSection, readGuideSection } from "./guideText";

const CATALOGS = { en, tr } as const;

describe("the guide catalog and the section table agree", () => {
  for (const locale of ["en", "tr"] as const) {
    const guide = CATALOGS[locale].guide;
    const sections = guide.sections as Record<string, unknown>;

    it(`${locale}: has exactly the table's sections, each well formed`, () => {
      expect(Object.keys(sections).sort()).toEqual(GUIDE_SECTIONS.map((section) => section.id).sort());
      for (const section of GUIDE_SECTIONS) {
        expect(() => readGuideSection(sections, section.id), section.id).not.toThrow();
      }
    });

    it(`${locale}: steps and states exist only where both languages have them`, () => {
      const other = (locale === "en" ? tr : en).guide.sections as Record<string, Record<string, unknown>>;
      for (const section of GUIDE_SECTIONS) {
        const mine = sections[section.id] as Record<string, unknown>;
        for (const key of ["steps", "afterSteps", "states", "permission"]) {
          expect(Object.hasOwn(mine, key), `${section.id}.${key}`).toBe(Object.hasOwn(other[section.id], key));
        }
      }
    });

    it(`${locale}: names every group, and the link titles equal the section titles`, () => {
      for (const group of GUIDE_GROUPS) expect(guide.groups[group], group).toBeTruthy();
      const titles = CATALOGS[locale].guideLinks.titles as Record<string, string>;
      expect(Object.keys(titles).sort()).toEqual(Object.keys(sections).sort());
      for (const id of Object.keys(sections)) {
        expect(titles[id], id).toBe((sections[id] as { title: string }).title);
      }
    });
  }

  it("points only at sections that exist, and a section never at itself", () => {
    for (const section of GUIDE_SECTIONS) {
      for (const related of section.related) {
        expect(guideSection(related), `${section.id} -> ${related}`).toBeDefined();
        expect(related).not.toBe(section.id);
      }
    }
  });

  it("gives a concept no route, and a page or task at least one", () => {
    for (const section of GUIDE_SECTIONS) {
      expect(section.routes.length === 0, section.id).toBe(section.group === "concepts");
    }
  });

  it("does not find a section by an inherited name", () => {
    for (const name of ["constructor", "toString", "__proto__", "hasOwnProperty"]) {
      expect(guideSection(name)).toBeUndefined();
      expect(() => readGuideSection(en.guide.sections, name)).toThrow();
    }
  });
});

describe("the section reader fails closed", () => {
  const good = { title: "T", answers: "A", body: [{ p: "text" }] };

  it("accepts the three block kinds", () => {
    expect(parseBlocks([{ p: "a" }, { h: "b" }, { ul: ["c", "d"] }], "x")).toHaveLength(3);
    expect(parseGuideSection(good, "x").title).toBe("T");
  });

  it("rejects an empty body, an unknown block, a mixed block, an empty text and an empty list", () => {
    expect(() => parseGuideSection({ ...good, body: [] }, "x")).toThrow();
    expect(() => parseGuideSection({ ...good, body: [{ div: "a" }] }, "x")).toThrow();
    expect(() => parseGuideSection({ ...good, body: [{ p: "a", h: "b" }] }, "x")).toThrow();
    expect(() => parseGuideSection({ ...good, body: [{ p: "" }] }, "x")).toThrow();
    expect(() => parseGuideSection({ ...good, body: [{ ul: [] }] }, "x")).toThrow();
    expect(() => parseGuideSection({ ...good, steps: [] }, "x")).toThrow();
    expect(() => parseGuideSection(null, "x")).toThrow();
  });
});
