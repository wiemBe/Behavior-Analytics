import { describe, expect, it } from "vitest";

import { escapeLdapName, renderBindName } from "./bindName";

describe("bind name preview", () => {
  it("escapes the same characters the directory bind escapes", () => {
    expect(escapeLdapName("a*b(c\\d,e\x00f")).toBe("a\\2ab\\28c\\5cd\\2ce\\00f");
    expect(escapeLdapName(" ada")).toBe("\\20ada");
    expect(escapeLdapName("#ada")).toBe("\\23ada");
  });

  it("substitutes only the username marker", () => {
    expect(renderBindName("{username}@corp.example", "a*b")).toBe("a\\2ab@corp.example");
    expect(renderBindName("cn={username},dc=corp", "a*b")).toBe("cn=a\\2ab,dc=corp");
    expect(renderBindName("cn={other}", "ada")).toBe("cn={other}");
  });
});
