// The lean refusal reader: English keeps the server's text, Turkish words a known code, and
// everything else shows the server's text. It must agree with the full backend reader on every
// registered code whose parameters it can read.

import { describe, expect, it } from "vitest";

import { createLocaleTranslator } from "./testTranslator";
import { renderBackendMessage, type BackendCodeArtifact } from "./backendMessage";
import codesJson from "./backendCodes.generated.json";
import { renderRefusal } from "./refusalMessage";

const tr = createLocaleTranslator("tr");
const en = createLocaleTranslator("en");
const CODES = codesJson as BackendCodeArtifact;

const REFUSAL_FAMILIES = ["auth.", "dashboard.", "propertywatch.", "customrule."];
const LEAN_KINDS = new Set(["text", "count", "ref"]);

describe("renderRefusal", () => {
  it("shows the server's text in English even when a code is present", () => {
    expect(
      renderRefusal(
        { text: "dashboard not found", code: "dashboard.not_found", params: {} },
        "en",
        en,
      ),
    ).toBe("dashboard not found");
  });

  it("words a known code in Turkish, text and count parameters included", () => {
    expect(
      renderRefusal({ text: "x", code: "dashboard.name_taken", params: { name: "Main" } }, "tr", tr),
    ).toBe("'Main' adlı bir gösterge paneliniz zaten var");
    expect(
      renderRefusal({ text: "x", code: "dashboard.owner_limit", params: { limit: 20 } }, "tr", tr),
    ).toBe("Sahip başına en fazla 20 gösterge paneli olabilir");
  });

  it("words a nested sentence through its own code", () => {
    expect(
      renderRefusal(
        {
          text: "english",
          code: "propertywatch.dependency.refused",
          params: {
            action: { code: "propertywatch.action.delete", params: {} },
            names: "'Rule A', 'Rule B'",
          },
        },
        "tr",
        tr,
      ),
    ).toBe(
      "izlemeyi silme işlemi yapılamıyor: 'Rule A', 'Rule B' özel kuralları bu izlemenin bir " +
        "değerini sabitliyor; önce kuralları ayırın veya silin",
    );
  });

  it.each([
    ["no code", { text: "server words" }],
    ["a null code", { text: "server words", code: null, params: {} }],
    ["no params", { text: "server words", code: "dashboard.not_found", params: null }],
    ["an unknown code", { text: "server words", code: "dashboard.unheard_of", params: {} }],
    ["a malformed code", { text: "server words", code: "Not A Code", params: {} }],
    ["an inherited name", { text: "server words", code: "constructor.prototype", params: {} }],
    ["a code with no Turkish entry", { text: "server words", code: "auth.nothing.here", params: {} }],
    [
      "a count that is not whole",
      { text: "server words", code: "dashboard.owner_limit", params: { limit: 2.5 } },
    ],
    [
      "a tagged wrapper it cannot read",
      {
        text: "server words",
        code: "dashboard.owner_limit",
        params: { limit: { kind: "duration", seconds: 3 } },
      },
    ],
    [
      "a nested sentence it cannot word",
      {
        text: "server words",
        code: "propertywatch.dependency.raced",
        params: { action: { code: "propertywatch.action.nope", params: {} } },
      },
    ],
  ])("shows the server's text for %s", (_name, sentence) => {
    expect(renderRefusal(sentence, "tr", tr)).toBe("server words");
  });

  it("stops at the depth bound and shows the server's text", () => {
    let params: Record<string, unknown> = { name: "x" };
    for (let level = 0; level < 8; level += 1) {
      params = { name: { code: "dashboard.name_taken", params } };
    }
    expect(renderRefusal({ text: "server words", code: "dashboard.name_taken", params }, "tr", tr)).toBe(
      "server words",
    );
  });

  it("falls back when the translator throws", () => {
    const broken = Object.assign(
      () => {
        throw new Error("format");
      },
      { has: () => true },
    );
    expect(
      renderRefusal({ text: "server words", code: "dashboard.not_found", params: {} }, "tr", broken),
    ).toBe("server words");
  });
});

describe("agreement with the full reader", () => {
  const readable = Object.entries(CODES).filter(
    ([code, spec]) =>
      REFUSAL_FAMILIES.some((family) => code.startsWith(family)) &&
      Object.values(spec.params).every((param) => LEAN_KINDS.has(param.kind)),
  );

  it("covers every refusal code", () => {
    const refusal = Object.keys(CODES).filter((code) =>
      REFUSAL_FAMILIES.some((family) => code.startsWith(family)),
    );
    expect(readable.map(([code]) => code).sort()).toEqual(refusal.sort());
  });

  it.each(readable)("%s reads the same in Turkish", (code, spec) => {
    // `sample` is the wire form: a ref is already `{code, params}`, a count a plain number.
    const sentence = { text: spec.sample_english, code, params: spec.sample };
    const lean = renderRefusal(sentence, "tr", tr);
    expect(lean).not.toBe(spec.sample_english);
    expect(lean).toBe(renderBackendMessage(sentence, "tr", tr));
  });
});
