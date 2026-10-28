// MSG-1: what the readers of a refused request show once the server codes its sentences.
// English keeps the server's text (and, for a 403 that names the permission, the new sentence);
// Turkish words a known code; an uncoded refusal, an old server's, shows the text as sent.

import { afterEach, describe, expect, it, vi } from "vitest";

import { errorTexts } from "@/components/auth/reasons";
import { classifySaveError } from "@/components/customrules/authoring";
import { classifyWatchError } from "@/components/propertywatch/authoring";
import type { PwI18n } from "@/components/propertywatch/pwText";
import { createLocaleTranslator } from "@/i18n/testTranslator";
import { ApiError, actionErrorMessage } from "@/lib/api";
import { describeRefusal, replaceDashboard } from "@/lib/dashboardApi";

const tr = createLocaleTranslator("tr");
const en = createLocaleTranslator("en");

afterEach(() => vi.unstubAllGlobals());

const refusal = (status: number, body: Record<string, unknown>) =>
  new ApiError(status, typeof body.detail === "string" ? body.detail : "failed", body.detail, body);

const noPermission = (permission = "admin:users") =>
  refusal(403, {
    detail: `missing required permission: ${permission}`,
    detail_code: "auth.permission.missing",
    detail_params: { permission },
  });

describe("a 403 that names the permission", () => {
  it("is worded with the permission by actionErrorMessage, in both languages", () => {
    expect(actionErrorMessage(noPermission(), en)).toBe(
      "You need the admin:users permission for this.",
    );
    expect(actionErrorMessage(noPermission(), tr)).toBe(
      "Bunun için admin:users iznine sahip olmanız gerekir.",
    );
    expect(actionErrorMessage(noPermission("dashboard:write"))).toBe(
      "You need the dashboard:write permission for this.",
    );
  });

  it("keeps the general sentence for a 403 without the code", () => {
    expect(actionErrorMessage(new ApiError(403, "Forbidden", "nope"), en)).toBe(
      "You do not have permission to perform this action.",
    );
    expect(actionErrorMessage(new ApiError(403, "Forbidden", "nope"), tr)).toBe(
      "Bu işlemi yapma izniniz yok.",
    );
    expect(
      actionErrorMessage(
        refusal(403, { detail: "only the owner", detail_code: "dashboard.owner_only", detail_params: {} }),
        tr,
      ),
    ).toBe("Bu işlemi yapma izniniz yok.");
  });

  it("is worded with the permission by describeRefusal, in both languages", () => {
    expect(describeRefusal(noPermission("dashboard:write"), en, "en")).toBe(
      "You need the dashboard:write permission for this.",
    );
    expect(describeRefusal(noPermission("dashboard:write"), tr, "tr")).toBe(
      "Bunun için dashboard:write iznine sahip olmanız gerekir.",
    );
    expect(describeRefusal(noPermission("dashboard:write"))).toBe(
      "You need the dashboard:write permission for this.",
    );
  });
});

describe("describeRefusal", () => {
  const taken = refusal(409, {
    detail: "you already have a dashboard named 'Main'",
    detail_code: "dashboard.name_taken",
    detail_params: { name: "Main" },
  });

  it("shows the server's text in English and a coded sentence in Turkish", () => {
    expect(describeRefusal(taken, en, "en")).toBe("You already have a dashboard named 'Main'.");
    expect(describeRefusal(taken, tr, "tr")).toBe("'Main' adlı bir gösterge paneliniz zaten var.");
    expect(describeRefusal(taken)).toBe("You already have a dashboard named 'Main'.");
  });

  it("shows the server's text in Turkish when the refusal carries no code", () => {
    const old = new ApiError(409, "x", "you already have a dashboard named 'Main'");
    expect(describeRefusal(old, tr, "tr")).toBe("You already have a dashboard named 'Main'.");
  });

  it("words an object detail's message by the code inside it", () => {
    const stale = refusal(409, {
      detail: { message: "this dashboard changed since you opened it", code: "dashboard.changed_since_opened", params: {} },
    });
    expect(describeRefusal(stale, tr, "tr")).toBe("Bu gösterge paneli siz açtıktan sonra değişti.");
    expect(describeRefusal(stale, en, "en")).toBe("This dashboard changed since you opened it.");
  });
});

describe("replaceDashboard", () => {
  it("carries the code of a stale write's message", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            detail: {
              message: "this dashboard changed since you opened it",
              current_revision: 3,
              code: "dashboard.changed_since_opened",
              params: {},
            },
          }),
          { status: 409, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );
    const result = await replaceDashboard("d1", {
      name: "n",
      description: null,
      layout: { v: 1, range: "24h", widgets: [] },
      expected_revision: 2,
    });
    expect(result).toEqual({
      kind: "conflict",
      message: "this dashboard changed since you opened it",
      current_revision: 3,
      code: "dashboard.changed_since_opened",
      params: {},
    });
  });

  it("carries no code for an older server", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({ detail: { message: "stale", current_revision: 3 } }),
          { status: 409, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );
    const result = await replaceDashboard("d1", {
      name: "n",
      description: null,
      layout: { v: 1, range: "24h", widgets: [] },
      expected_revision: 2,
    });
    expect(result).toEqual({ kind: "conflict", message: "stale", current_revision: 3 });
  });
});

describe("errorTexts", () => {
  const policy = refusal(422, {
    detail: ["password must be at least 12 characters", "password must not equal the username"],
    detail_codes: [
      { code: "auth.password.too_short", params: {} },
      { code: "auth.password.equals_username", params: {} },
    ],
  });

  it("lists the server's sentences in English", () => {
    expect(errorTexts(policy, en, "en")).toEqual([
      "password must be at least 12 characters",
      "password must not equal the username",
    ]);
    expect(errorTexts(policy)).toEqual([
      "password must be at least 12 characters",
      "password must not equal the username",
    ]);
  });

  it("lists each coded sentence in Turkish", () => {
    expect(errorTexts(policy, tr, "tr")).toEqual([
      "Parola en az 12 karakter olmalıdır",
      "Parola kullanıcı adıyla aynı olmamalıdır",
    ]);
  });

  it("words one sentence of a list and leaves an uncoded neighbour as sent", () => {
    const mixed = refusal(422, {
      detail: ["password must be at most 72 bytes", "something added later"],
      detail_codes: [{ code: "auth.password.too_long", params: {} }],
    });
    expect(errorTexts(mixed, tr, "tr")).toEqual([
      "Parola en fazla 72 bayt olmalıdır",
      "something added later",
    ]);
  });

  it("words a single coded sentence, ", () => {
    const wrong = refusal(401, {
      detail: "current password is wrong",
      detail_code: "auth.password.current_wrong",
      detail_params: {},
    });
    expect(errorTexts(wrong, tr, "tr")).toEqual(["Mevcut parola yanlış"]);
    expect(errorTexts(wrong, tr, "en")).toEqual(["current password is wrong"]);
  });

  it("keeps a list from an older server, and a validation list of objects, as before", () => {
    expect(errorTexts(new ApiError(422, "x", ["too short", { msg: "bad" }]), tr, "tr")).toEqual([
      "too short",
      "bad",
    ]);
  });

  it("words each directory reason, with its parameters", () => {
    const directory = refusal(422, {
      detail: ["at most 8 directory URLs are allowed", "http://dc must use ldap:// or ldaps://"],
      detail_codes: [
        { code: "auth.directory.urls_max", params: { max: 8 } },
        { code: "auth.directory.url_scheme", params: { url: "http://dc" } },
      ],
    });
    expect(errorTexts(directory, tr, "tr")).toEqual([
      "En fazla 8 dizin URL adresine izin verilir",
      "http://dc ldap:// veya ldaps:// kullanmalıdır",
    ]);
  });
});

describe("the property-watch and custom-rule editors", () => {
  const i18nTr: PwI18n = { t: tr, locale: "tr" };
  const i18nEn: PwI18n = { t: en, locale: "en" };

  it("words a watch name conflict in Turkish and keeps English as sent", () => {
    const dup = refusal(409, {
      detail: "a property watch named 'W' already exists",
      detail_code: "propertywatch.name_exists",
      detail_params: { name: "W" },
    });
    expect(classifyWatchError(dup, i18nTr)).toEqual({
      kind: "conflict",
      detail: "'W' adlı bir özellik izleme zaten var",
      codes: [],
    });
    expect(classifyWatchError(dup, i18nEn)).toEqual({
      kind: "conflict",
      detail: "a property watch named 'W' already exists",
      codes: [],
    });
  });

  it("words the dependency refusal's message and keeps the rules", () => {
    const refused = refusal(409, {
      detail: {
        message: "cannot delete the watch: custom rule(s) 'R' pin a value of this watch; detach or delete the rule(s) first",
        rules: [{ id: "r1", name: "R" }],
        code: "propertywatch.dependency.refused",
        params: {
          action: { code: "propertywatch.action.delete", params: {} },
          names: "'R'",
        },
      },
    });
    expect(classifyWatchError(refused, i18nTr)).toEqual({
      kind: "dependencies",
      message:
        "izlemeyi silme işlemi yapılamıyor: 'R' özel kuralları bu izlemenin bir değerini sabitliyor; önce kuralları ayırın veya silin",
      rules: [{ id: "r1", name: "R" }],
    });
    const english = classifyWatchError(refused, i18nEn);
    expect(english.kind === "dependencies" && english.message.startsWith("cannot delete the watch")).toBe(true);
  });

  it("words a custom-rule name conflict in Turkish and keeps English as sent", () => {
    const dup = refusal(409, {
      detail: "a custom rule named 'V' already exists",
      detail_code: "customrule.name_exists",
      detail_params: { name: "V" },
    });
    expect(classifySaveError(dup, tr, "tr")).toEqual({ kind: "error", detail: "'V' adlı bir özel kural zaten var" });
    expect(classifySaveError(dup, en, "en")).toEqual({
      kind: "error",
      detail: "a custom rule named 'V' already exists",
    });
    expect(classifySaveError(dup)).toEqual({
      kind: "error",
      detail: "a custom rule named 'V' already exists",
    });
  });
});
