// The shape of one guide section in the `guide` catalog, checked when it is read. The catalog is
// product text edited by hand and by scripts; a malformed block must stop the page and the
// contract test, never render as a blank or as an empty list.

export type GuideBlock = { p: string } | { ul: string[] } | { h: string };

export interface GuideText {
  title: string;
  answers: string;
  body: GuideBlock[];
  steps?: string[];
  afterSteps?: GuideBlock[];
  states?: GuideBlock[];
  /** Replaces the permission codes where the text says what each one is for. */
  permission?: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function text(value: unknown, where: string): string {
  if (typeof value !== "string" || value === "") throw new Error(`guide: ${where} is not text`);
  return value;
}

function textList(value: unknown, where: string): string[] {
  if (!Array.isArray(value) || value.length === 0) throw new Error(`guide: ${where} is not a list`);
  return value.map((item, index) => text(item, `${where}.${index}`));
}

export function parseBlocks(value: unknown, where: string): GuideBlock[] {
  if (!Array.isArray(value) || value.length === 0) throw new Error(`guide: ${where} has no blocks`);
  return value.map((block, index): GuideBlock => {
    const at = `${where}.${index}`;
    if (!isRecord(block) || Object.keys(block).length !== 1) throw new Error(`guide: ${at} is not a block`);
    if (Object.hasOwn(block, "p")) return { p: text(block.p, at) };
    if (Object.hasOwn(block, "h")) return { h: text(block.h, at) };
    if (Object.hasOwn(block, "ul")) return { ul: textList(block.ul, at) };
    throw new Error(`guide: ${at} is none of p, h, ul`);
  });
}

export function parseGuideSection(value: unknown, id: string): GuideText {
  const where = `sections.${id}`;
  if (!isRecord(value)) throw new Error(`guide: ${where} is missing`);
  const section: GuideText = {
    title: text(value.title, `${where}.title`),
    answers: text(value.answers, `${where}.answers`),
    body: parseBlocks(value.body, `${where}.body`),
  };
  if (Object.hasOwn(value, "steps")) section.steps = textList(value.steps, `${where}.steps`);
  if (Object.hasOwn(value, "afterSteps")) section.afterSteps = parseBlocks(value.afterSteps, `${where}.afterSteps`);
  if (Object.hasOwn(value, "states")) section.states = parseBlocks(value.states, `${where}.states`);
  if (Object.hasOwn(value, "permission")) section.permission = text(value.permission, `${where}.permission`);
  return section;
}

/** One section out of the namespace's raw `sections` object; an inherited name is not a section. */
export function readGuideSection(sections: unknown, id: string): GuideText {
  if (!isRecord(sections) || !Object.hasOwn(sections, id)) throw new Error(`guide: no section ${id}`);
  return parseGuideSection(sections[id], id);
}

/**
 * A raw (unformatted) message by dotted key. next-intl types `raw` over string leaves only, so a
 * list or an object needs this one cast, here and nowhere else.
 */
export function rawMessage(t: { raw: unknown }, key: string): unknown {
  return (t.raw as (key: string) => unknown)(key);
}
