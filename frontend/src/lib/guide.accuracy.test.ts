import { describe, expect, it } from "vitest";

import en from "../../messages/en";
import { ENABLED_LOCALES } from "@/i18n/locale";

// GUIDE-2: sentences of the English guide that must stay true of the 0.1.10 product. The label
// contract (`guideLabels.contract.test.ts`) pins every bold label; these pin the claims around
// them that a label check cannot see.

type Block = { p?: string; h?: string; ul?: string[] };
type Section = { answers: string; body: Block[]; steps?: string[]; states?: Block[] };

const sections = en.guide.sections as unknown as Record<string, Section>;

/** Every string of a section, in one text. */
function textOf(section: Section): string {
  return JSON.stringify(section);
}

const LANGUAGE_WORDS = /language|english|türkçe|turkish|locale|cookie/i;

describe("the account section follows the languages that are switched on (D-41)", () => {
  const several = ENABLED_LOCALES.length > 1;

  it("names no language control while only English is enabled, and names it once more are", () => {
    expect(LANGUAGE_WORDS.test(textOf(sections.account)), "account").toBe(several);
    expect(/language/i.test(sections.account.answers)).toBe(several);
  });

  it("does not promise a language option from the roles section either", () => {
    expect(/language/i.test(textOf(sections.roles))).toBe(several);
  });

  it("does not mention Turkish anywhere in the guide while it is switched off", () => {
    const found = Object.entries(sections)
      .filter(([, section]) => /türkçe|turkish/i.test(textOf(section)))
      .map(([id]) => id);
    expect(found.length > 0).toBe(several);
  });

  it("describes the password, which is what the page shows", () => {
    const text = textOf(sections.account);
    expect(sections.account.answers).toMatch(/password/i);
    expect(text).toContain("**Password**");
    expect(text).toContain("LDAP account changes its password in the directory");
    expect(text).toContain("The password was not changed.");
  });
});

describe("the users section says how access is removed", () => {
  const body = sections["users-and-directory"].body.map((block) => block.p ?? "").join("\n");

  it("says there is no user delete and that disabling removes access", () => {
    expect(body).toContain("There is no user delete.");
    expect(body).toContain("Disabling an account is how access is removed");
  });

  it("says the sessions end and the account row and its audit trail stay", () => {
    expect(body).toMatch(/ends the account's sessions/);
    expect(body).toContain("The account row and its audit trail stay.");
  });

  it("names the control as the page shows it, under Manage", () => {
    expect(body).toContain("**Disable {name}** under **Manage**");
    const steps = sections["users-and-directory"].steps!.join("\n");
    expect(steps).toContain("Choose **Manage** on a row");
    expect(steps).toContain("**Disable {name}**");
  });

  it("promises no delete control anywhere in the section", () => {
    const text = textOf(sections["users-and-directory"]);
    expect(text).not.toMatch(/delete (the |an )?(user|account)|remove (the |an )?(user|account)/i);
  });
});

describe("the geography section describes both ways the country is found", () => {
  it("says what Unresolved means for a property and for the GeoIP lookup", () => {
    const text = sections.geography.body.map((block) => block.p ?? "").join("\n");
    expect(text).toContain("that property was empty");
    expect(text).toContain("private, internal or unknown to GeoIP");
  });
});
