import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api";
import { createLocaleTranslator } from "@/i18n/testTranslator";

import {
  TEST_OUTCOMES,
  bodyOf,
  draftFrom,
  insertAt,
  outcomeName,
  outcomeSentence,
  placeholderToken,
  recipientErrors,
  sameDraft,
  sourceOf,
} from "./mail";
import { customisedList, plainList, template } from "./fixtures";

const say = createLocaleTranslator("en");

describe("draftFrom", () => {
  it("starts a template with no stored row enabled: the API's false for it means nothing", () => {
    expect(draftFrom(template("waf_endpoint")).enabled).toBe(true);
  });

  it("keeps a stored switch as it is", () => {
    expect(draftFrom(template("waf_endpoint", { customised: true, enabled: false })).enabled).toBe(false);
    expect(draftFrom(template("waf_endpoint", { customised: true, enabled: true })).enabled).toBe(true);
  });
});

describe("sameDraft and bodyOf", () => {
  it("compares every field", () => {
    const a = draftFrom(template("default"));
    expect(sameDraft(a, { ...a })).toBe(true);
    for (const change of [{ subject: "x" }, { html: "x" }, { text: "x" }, { enabled: !a.enabled }]) {
      expect(sameDraft(a, { ...a, ...change })).toBe(false);
    }
  });

  it("sends an empty or blank text body as null, so the text part is derived", () => {
    const a = draftFrom(template("default"));
    expect(bodyOf({ ...a, text: "" }).text_body).toBeNull();
    expect(bodyOf({ ...a, text: "  \n" }).text_body).toBeNull();
    expect(bodyOf({ ...a, text: "Plain" }).text_body).toBe("Plain");
  });
});

describe("insertAt", () => {
  it("puts the text at the caret and moves the caret behind it", () => {
    expect(insertAt("ab", 1, 1, "X")).toEqual({ value: "aXb", caret: 2 });
  });
  it("replaces a selection", () => {
    expect(insertAt("abcd", 1, 3, "X")).toEqual({ value: "aXd", caret: 2 });
  });
  it("clamps a caret outside the text", () => {
    expect(insertAt("ab", 9, 12, "X")).toEqual({ value: "abX", caret: 3 });
    expect(insertAt("ab", -3, -1, "X")).toEqual({ value: "Xab", caret: 1 });
  });
  it("writes a placeholder in the API's syntax", () => {
    expect(placeholderToken("alert.title")).toBe("{{ alert.title }}");
  });
});

describe("sourceOf", () => {
  it("says shipped for every row when nothing is stored", () => {
    const list = plainList();
    expect(list.templates.map((row) => sourceOf(row, list))).toEqual([
      "shipped",
      "shipped",
      "shipped",
      "shipped",
      "shipped",
    ]);
  });

  it("says own for an enabled stored row, default for a type under a stored default, shipped for a stored but disabled one without a default", () => {
    const list = customisedList();
    const bySource = Object.fromEntries(list.templates.map((row) => [row.key, sourceOf(row, list)]));
    expect(bySource).toEqual({
      default: "own",
      log_source: "default",
      waf_endpoint: "own",
      ips_entity: "default",
      custom_rule: "default",
    });
    const noDefault = {
      ...list,
      templates: list.templates.map((row) =>
        row.key === "default" ? { ...row, customised: false, enabled: false } : row,
      ),
    };
    expect(sourceOf(noDefault.templates[4], noDefault)).toBe("shipped");
  });

  it("does not use a stored default that is switched off", () => {
    const list = customisedList();
    const off = {
      ...list,
      templates: list.templates.map((row) => (row.key === "default" ? { ...row, enabled: false } : row)),
    };
    expect(sourceOf(off.templates[0], off)).toBe("shipped");
    expect(sourceOf(off.templates[1], off)).toBe("shipped");
  });
});

describe("test-send outcomes", () => {
  it("maps every code the API documents to a sentence from the catalog, never blank", () => {
    expect(TEST_OUTCOMES).toHaveLength(11);
    for (const name of TEST_OUTCOMES) {
      const sentence = outcomeSentence({ code: `email.test.${name}`, message: "" }, say);
      expect(sentence.trim(), name).not.toBe("");
      expect(sentence).toBe(say(`backend.email.test.${name}`));
    }
  });

  it("shows the API's own sentence for a code it does not know", () => {
    expect(
      outcomeSentence({ code: "email.test.greylisted", message: "The relay asked to retry later." }, say),
    ).toBe("The relay asked to retry later.");
    expect(outcomeName("email.test.greylisted")).toBeNull();
    expect(outcomeName("something.else")).toBeNull();
  });

  it("never renders blank when the API sends an unknown code with no sentence", () => {
    expect(outcomeSentence({ code: "email.test.greylisted", message: "  " }, say).trim()).not.toBe("");
  });
});

describe("recipientErrors", () => {
  const fallback = ["fallback"];
  const coded = (status: number, detail: unknown, body: unknown) =>
    new ApiError(status, typeof detail === "string" ? detail : "x", detail, body);

  it("puts the duplicate sentence under the address", () => {
    const err = coded(409, "soc@example.org is already a recipient. Edit the existing entry instead.", {
      detail: "soc@example.org is already a recipient. Edit the existing entry instead.",
      detail_code: "email.recipient.duplicate",
      detail_params: { address: "soc@example.org" },
    });
    const out = recipientErrors(err, "en", say, fallback);
    expect(out.address).toEqual(["soc@example.org is already a recipient. Edit the existing entry instead."]);
    expect(out.types).toEqual([]);
    expect(out.general).toEqual([]);
  });

  it("splits a list of coded reasons between the address and the types", () => {
    const err = coded(422, ["x is not an alert type a recipient can be limited to.", "y is not an alert type a recipient can be limited to."], {
      detail_codes: [
        { code: "email.recipient.alert_type_unknown", params: { name: "x" } },
        { code: "email.recipient.alert_type_unknown", params: { name: "y" } },
      ],
    });
    const out = recipientErrors(err, "en", say, fallback);
    expect(out.types).toHaveLength(2);
    expect(out.address).toEqual([]);
  });

  it("keeps a reason with no known field in the general list, and uses the fallback when there is none", () => {
    expect(
      recipientErrors(coded(422, "something else", { detail: "something else" }), "en", say, fallback)
        .general,
    ).toEqual(["something else"]);
    expect(
      recipientErrors(coded(422, [{ msg: "field required" }], {}), "en", say, fallback).general,
    ).toEqual(fallback);
  });
});
