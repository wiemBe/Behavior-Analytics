// MSG-8a: the history backfill sentences reach the reader in its language. English keeps the
// server's text; Turkish words a known code; an uncoded or unknown sentence shows the text as sent.
// A count is grouped, a fractional number keeps its decimals, and a joined limit or clause list
// keeps its structure (" ve " between limits, "; " between clauses).

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createTranslator } from "next-intl";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setTestLocale } from "../../../vitest.setup";
import codesJson from "@/i18n/backendCodes.generated.json";
import { renderBackendCode, type BackendCodeArtifact } from "@/i18n/backendMessage";
import { createTextTranslator } from "@/i18n/textTranslator";
import { createLocaleTranslator } from "@/i18n/testTranslator";
import { asTextTranslator } from "@/i18n/textTranslator";
import { StaticSession } from "@/components/auth/session";
import { ApiError, api, type CallerInfo, type HistoryConsole, type HistoryScope } from "@/lib/api";
import type { BehaviorI18n } from "@/lib/behaviorI18n";

import en from "../../../messages/en";
import tr from "../../../messages/tr";
import { HistoryBackfillPage } from "./HistoryBackfillPage";
import { HistoryTable } from "./HistoryTable";
import { consoleOf, decision, estimateFamily, family, scope } from "./fixtures";
import {
  assumptionTexts,
  conflictDetail,
  estimateText,
  familyStateText,
  windowReason,
} from "./historyFormat";
import { sentenceReader } from "./historyReasons";
import { shellSentenceReader } from "./historyShellReader";
import { shellMessages } from "@/i18n/routeNamespaces";
import { rowsOf } from "./historyRows";

const NB = "\u00a0";
const CODES = codesJson as BackendCodeArtifact;
const trSay = createLocaleTranslator("tr");

function say(locale: "en" | "tr"): BehaviorI18n {
  const translate = createTranslator({ locale, messages: (locale === "tr" ? tr : en) as never });
  return { t: asTextTranslator(translate), locale };
}
const TR = say("tr");
const EN = say("en");
const readTr = sentenceReader(TR);
const readEn = sentenceReader(EN);

const turkish = (code: string, params: Record<string, unknown>) =>
  renderBackendCode({ code, params }, "tr", trSay);

beforeEach(() => {
  setTestLocale("tr");
});

describe("every history code", () => {
  const codes = Object.keys(CODES).filter((code) => code.startsWith("history."));

  it("is in the catalog, words its own sample in Turkish and leaves no placeholder", () => {
    expect(codes.length).toBeGreaterThan(100);
    for (const code of codes) {
      const spec = CODES[code];
      const words = turkish(code, spec.sample);
      expect(words, code).toBeDefined();
      expect(words, code).not.toMatch(/[{}]/);
      expect(words, code).not.toContain("undefined");
    }
  });

  it("differs from the English except where it only composes other sentences", () => {
    const composes = new Set([
      "history.assumption.opening",
      "history.estimate.window_limited",
      "history.reason.joined",
      "history.settle.ticked",
    ]);
    for (const code of codes) {
      if (composes.has(code)) continue;
      expect(turkish(code, CODES[code].sample), code).not.toBe(CODES[code].sample_english);
    }
  });
});

describe("the sentences of the history codes in Turkish", () => {
  const setting = (name: string, days: number) => ({
    code: "history.window.limit_setting",
    params: { setting: name, days },
  });

  it("joins two limits of a reduced window with ve, and groups nothing it should not", () => {
    expect(
      turkish("history.window.reduced", {
        asked: 28,
        days: 21,
        limits: {
          kind: "list",
          items: [
            setting("BASELINE_LOOKBACK_DAYS", 21),
            {
              code: "history.window.limit_retention",
              params: {
                setting: "RETENTION_LOG_SOURCE_METRIC_DAYS",
                days: 22,
                hours_setting: "HISTORY_BACKFILL_MAX_HOURS",
                hours: 72,
              },
            },
          ],
        },
      }),
    ).toBe(
      `pencere 28${NB}günden 21${NB}güne indirildi: BASELINE_LOOKBACK_DAYS 21${NB}gün ve ` +
        `RETENTION_LOG_SOURCE_METRIC_DAYS 22${NB}gün ve HISTORY_BACKFILL_MAX_HOURS 72${NB}saat ` +
        "olduğundan okuma saklama süresini aşardı",
    );
  });

  it("joins the clauses of a settled step with a semicolon and groups a large count", () => {
    expect(
      turkish("history.settle.ticked", {
        clauses: {
          kind: "list",
          items: [
            { code: "history.settle.read", params: { count: 8640 } },
            { code: "history.settle.width", params: { count: 576 } },
            {
              code: "history.settle.gap_recorded",
              params: { gap: { code: "history.gap.search_failed", params: { failures: 3 } } },
            },
          ],
        },
      }),
    ).toBe(
      "8.640 zaman dilimi okundu; genişlik 576 zaman dilimi; boşluk kaydedildi: " +
        "arama en küçük genişlikte 3 kez başarısız oldu",
    );
  });

  it("keeps the decimal of a fractional number the way Turkish writes it", () => {
    expect(turkish("history.fit.fits", { days: 28, hours: 23.4 })).toBe(
      `28${NB}günlük pencere tahminen 23,4${NB}saate sığıyor`,
    );
    expect(turkish("history.estimate.all_fit", { sources: 4, days: 28, hours: 1260.5 })).toContain(
      `1.260,5${NB}saat`,
    );
  });

  it("words an enum parameter from the catalog, not from the English value", () => {
    expect(
      turkish("history.scan.retry", { status: "not_run", failures: 1, max_failures: 3 }),
    ).toBe(
      `bir tarama araması çalıştırılmadı; hiçbir şey kanıtlamaz, bu yüzden yeniden denenir (1${NB}/${NB}3)`,
    );
    expect(
      turkish("history.estimate.empty_sources", {
        reason: { code: "history.fit.fits", params: { days: 28, hours: 1.5 } },
        count: 2,
        noun: "tracked endpoint",
        limit: 72,
      }),
    ).toContain("henüz izlenen endpoint olmadan sığdırıldı");
  });

  it("counts the sources of a partial finish as M / N", () => {
    expect(
      turkish("history.finish.cut_sources", {
        cut: 2,
        total: { code: "history.words.source_many", params: { count: 1204 } },
      }),
    ).toBe(`QRadar, 2${NB}/${NB}1.204 kaynak için istenenden azını tutuyor`);
    expect(
      turkish("history.finish.silent_sources", {
        sources: { code: "history.words.source_one", params: {} },
      }),
    ).toBe("1 kaynak için pencerede olay yoktu");
  });
});

describe("the reader of a sentence", () => {
  const coded = { code: "history.step.wait_budget", params: {} };

  it("shows the API's text exactly in English, coded or not", () => {
    expect(readEn("waiting for search budget", coded)).toBe("waiting for search budget");
    expect(readEn("any words", null)).toBe("any words");
  });

  it("words a code in Turkish and opens the sentence with a capital", () => {
    expect(readTr("waiting for search budget", coded)).toBe("Arama bütçesi bekleniyor");
    expect(readTr("history unavailable", { code: "history.state.stopped", params: {} })).toBe(
      "Bir operatör tarafından durduruldu",
    );
    // A digit has no capital: the sentence is left as worded.
    expect(readTr("x", { code: "history.interval.hour_one", params: {} })).toBe(`1${NB}saat`);
  });

  it("shows the text as sent for an uncoded sentence, an unknown code or wrong params", () => {
    expect(readTr("stopped by ayse", null)).toBe("stopped by ayse");
    expect(readTr("stopped by ayse", { code: null, params: null })).toBe("stopped by ayse");
    expect(readTr("stopped by ayse", { code: "history.not_a_code", params: {} })).toBe(
      "stopped by ayse",
    );
    expect(
      readTr("stopped by ayse", { code: "history.lifecycle.stopped_by", params: {} }),
    ).toBe("stopped by ayse");
    expect(
      readTr("stopped by ayse", { code: "history.lifecycle.stopped_by", params: { by: "ayse" } }),
    ).toBe("Durduran: ayse");
  });
});

describe("the history views in Turkish", () => {
  const NOW = new Date("2026-10-21T12:00:00Z");

  function table(scopes: HistoryScope[], extra: Partial<HistoryConsole> = {}) {
    return consoleOf({
      decision: decision(),
      mode: "ACTIVE",
      families: [
        family({
          scopes,
          window: {
            days_requested: 28,
            days: 21,
            available: true,
            limited_by: ["baseline_lookback_days"],
            reason: "window reduced from 28 to 21 days: BASELINE_LOOKBACK_DAYS is 21 days",
            reason_code: "history.window.reduced",
            reason_params: {
              asked: 28,
              days: 21,
              limits: {
                kind: "list",
                items: [
                  {
                    code: "history.window.limit_setting",
                    params: { setting: "BASELINE_LOOKBACK_DAYS", days: 21 },
                  },
                ],
              },
            },
          },
        }),
      ],
      ...extra,
    });
  }

  it("words a scope's reason and its gaps, and leaves an old row as the API sent it", () => {
    const rows = rowsOf(
      table([
        scope({
          state: "PARTIAL",
          finished_at: "2026-10-10T01:00:00Z",
          reason: "stopped by ayse",
          reason_code: "history.lifecycle.stopped_by",
          reason_params: { by: "ayse" },
          gaps: [
            {
              start: "2026-10-01T00:00:00Z",
              end: "2026-10-01T00:30:00Z",
              reason: "search failed 3 times at the minimum width",
              reason_code: "history.gap.search_failed",
              reason_params: { failures: 3 },
            },
            {
              start: "2026-10-02T00:00:00Z",
              end: "2026-10-02T00:30:00Z",
              reason: "search failed 3 times",
            },
          ],
        }),
        scope({ scope_key: "2", reason: "stopped by ayse" }),
      ]),
      TR,
      readTr,
    );
    expect(rows[0].reason).toBe("Durduran: ayse");
    expect(rows[0].gaps.map((g) => g.reason)).toEqual([
      "Arama en küçük genişlikte 3 kez başarısız oldu",
      "search failed 3 times",
    ]);
    expect(rows[1].reason).toBe("stopped by ayse");
  });

  it("renders the table with the reduced window row and a switched-off family in Turkish", () => {
    const c = consoleOf({
      decision: decision(),
      mode: "ACTIVE",
      families: [
        family({
          scopes: [
            scope({
              state: "READING",
              reason: "the 21-day window fits in an estimated 0.1 hours; read 100 bucket(s)",
              reason_code: "history.reason.joined",
              reason_params: {
                clauses: {
                  kind: "list",
                  items: [
                    { code: "history.fit.fits", params: { days: 21, hours: 0.1 } },
                    { code: "history.settle.read", params: { count: 100 } },
                  ],
                },
              },
            }),
          ],
        }),
        family({
          family: "IPS",
          collection_enabled: false,
          collection_reason: "IPS_COLLECTION_ENABLED is off",
          collection_reason_code: "history.family.switch_off",
          collection_reason_params: { setting: "IPS_COLLECTION_ENABLED" },
          sources: [],
          later_yes: null,
        }),
      ],
    });
    render(<HistoryTable console={c} now={NOW} />);
    const text = document.body.textContent ?? "";
    expect(text).toContain(`21${NB}günlük pencere tahminen 0,1${NB}saate sığıyor; 100 zaman dilimi okundu`);
    expect(text).toContain("IPS_COLLECTION_ENABLED kapalı");
    expect(text).not.toContain("estimated");
    expect(screen.getAllByText("Neden").length).toBeGreaterThan(0);
  });

  it("keeps the English table verbatim in English", () => {
    setTestLocale("en");
    const c = table([
      scope({
        reason: "stopped by ayse",
        reason_code: "history.lifecycle.stopped_by",
        reason_params: { by: "ayse" },
      }),
    ]);
    render(<HistoryTable console={c} now={NOW} />);
    expect(document.body.textContent).toContain("stopped by ayse");
    expect(document.body.textContent).not.toContain("tarafından");
  });

  it("words the family state and the window reason through the reader", () => {
    const reduced = table([]).families[0];
    expect(windowReason(reduced, TR, readTr)).toBe(
      `Pencere 28${NB}günden 21${NB}güne indirildi: BASELINE_LOOKBACK_DAYS 21${NB}gün`,
    );
    const off = family({
      collection_enabled: false,
      collection_reason: "WAF_COLLECTION_ENABLED is off",
      collection_reason_code: "history.family.switch_off",
      collection_reason_params: { setting: "WAF_COLLECTION_ENABLED" },
    });
    expect(familyStateText(off, TR, readTr)).toBe("Kapalı. WAF_COLLECTION_ENABLED kapalı.");
    // The first-run dialog sits in the shell and passes no reader: the API's text, as sent.
    expect(familyStateText(off, TR)).toBe("Kapalı. WAF_COLLECTION_ENABLED is off.");
  });

  it("words an estimate's reason and its assumptions, each assumption once", () => {
    const estimate = estimateFamily({
      hours: 61.2,
      reason: "all 4 per-source 28-day windows fit; the console total is an estimated 61.2 hours",
      reason_code: "history.estimate.all_fit",
      reason_params: { sources: 4, days: 28, hours: 61.2 },
      reduced: true,
      assumptions: [
        "5-minute buckets, 10 history searches per tick",
        "QRadar time per search not measured on this console",
        "QRadar time per search not measured on this console",
      ],
      assumption_codes: [
        {
          code: "history.assumption.opening",
          params: {
            shape: { code: "history.assumption.shape_buckets", params: { minutes: 5 } },
            budget: { code: "history.assumption.budget_plain", params: { per_tick: 10 } },
          },
        },
        { code: "history.assumption.qradar_time", params: {} },
        { code: "history.assumption.qradar_time", params: {} },
      ],
    });
    const shown = estimateText(estimate, TR, readTr);
    expect(shown.note).toContain(
      `Azaltıldı: Kaynak başına 28${NB}günlük 4 pencerenin tümü sığıyor; console toplamı tahminen 61,2${NB}saat`,
    );
    expect(assumptionTexts(estimate, readTr)).toEqual([
      `5${NB}dakikalık zaman dilimleri, tur başına 10 geçmiş arama`,
      "Arama başına QRadar süresi bu console'da ölçülmedi",
    ]);
    // Without a reader (the shell's dialog) the API's words stay.
    expect(assumptionTexts(estimate)).toEqual([
      "5-minute buckets, 10 history searches per tick",
      "QRadar time per search not measured on this console",
    ]);
  });

  it("words the sentence of a 409 from its body and falls back to the text as sent", () => {
    const withCode = new ApiError(409, "x", "a Yes is never reversed; stop the reading instead", {
      detail: "a Yes is never reversed; stop the reading instead",
      detail_code: "history.decide.never_reversed",
      detail_params: {},
    });
    expect(conflictDetail(withCode, withCode.detail as string, readTr)).toBe(
      "Evet yanıtı hiçbir zaman geri alınmaz; bunun yerine okumayı durdurun",
    );
    const old = new ApiError(409, "x", "record Yes first", { detail: "record Yes first" });
    expect(conflictDetail(old, "record Yes first", readTr)).toBe("record Yes first");
    expect(within(document.body).queryByText("x")).toBeNull();
  });
});

describe("the list styles of the backend reader", () => {
  const artifact: BackendCodeArtifact = {
    "history.test.item": {
      template: "item",
      params: {},
      sample: {},
      sample_english: "item",
      sample_english_params: {},
    },
    // A `semicolon` list of an earlier family keeps the locale's own list form (rule health).
    "test.semicolon": {
      template: "{items}",
      params: { items: { kind: "list", style: "semicolon" } },
      sample: { items: { kind: "list", items: [] } },
      sample_english: "",
      sample_english_params: {},
    },
    ...Object.fromEntries(
      ["and_all", "semicolon", "comma"].map((style) => [
        `history.test.${style}`,
        {
          template: "{items}",
          params: { items: { kind: "list", style: style === "comma" ? undefined : style } },
          sample: { items: { kind: "list", items: [] } },
          sample_english: "",
          sample_english_params: {},
        },
      ]),
    ),
  } as BackendCodeArtifact;
  const say = createTextTranslator("tr", {
    backend: {
      history: {
        test: { item: "öğe", and_all: "{items}", semicolon: "{items}", comma: "{items}" },
      },
      test: { semicolon: "{items}" },
    },
  });
  const items = {
    kind: "list",
    items: [
      { code: "history.test.item", params: {} },
      { code: "history.test.item", params: {} },
      { code: "history.test.item", params: {} },
    ],
  };

  it("keeps a flat conjunction, a semicolon list and the locale's own list as the style says", () => {
    expect(renderBackendCode({ code: "history.test.and_all", params: { items } }, "tr", say, artifact)).toBe(
      "öğe ve öğe ve öğe",
    );
    expect(renderBackendCode({ code: "history.test.semicolon", params: { items } }, "tr", say, artifact)).toBe(
      "öğe; öğe; öğe",
    );
    expect(renderBackendCode({ code: "history.test.comma", params: { items } }, "tr", say, artifact)).toBe(
      "öğe, öğe ve öğe",
    );
  });

  it("keeps the locale's own list for a semicolon list outside the history codes", () => {
    const outside = {
      kind: "list",
      items: [
        { code: "history.test.item", params: {} },
        { code: "history.test.item", params: {} },
        { code: "history.test.item", params: {} },
      ],
    };
    expect(
      renderBackendCode({ code: "test.semicolon", params: { items: outside } }, "tr", say, artifact),
    ).toBe("öğe, öğe ve öğe");
  });

  it("refuses a list that is not a tagged list, so the API's text is shown", () => {
    expect(
      renderBackendCode(
        { code: "history.test.semicolon", params: { items: [{ code: "history.test.item", params: {} }] } },
        "tr",
        say,
        artifact,
      ),
    ).toBeUndefined();
  });
});

describe("the decision block's sentences from the API", () => {
  const admin: CallerInfo = {
    subject: "ada",
    email: null,
    permissions: ["admin:*"],
    effective_permissions: ["admin:*"],
    username: "ada",
    auth_source: "LOCAL",
    must_change_password: false,
    roles: ["admin"],
  };

  function mountPage() {
    return render(
      <StaticSession
        value={{ status: "ready", me: admin, error: null, reload: vi.fn(), signOut: vi.fn() }}
      >
        <HistoryBackfillPage />
      </StaticSession>,
    );
  }

  const reading = () =>
    consoleOf({
      decision: decision(),
      mode: "ACTIVE",
      families: [family({ scopes: [scope()] })],
    });

  it("words the message of a recorded request in Turkish", async () => {
    vi.spyOn(api, "historyBackfill").mockResolvedValue([reading()]);
    vi.spyOn(api, "stopHistoryBackfill").mockResolvedValue({
      ...reading(),
      mode: "STOPPED",
      message:
        "Stop reading is recorded. Each family ends its reading scopes at its next collection; no scope has changed yet.",
      message_code: "history.request.stop",
      message_params: {},
    });
    mountPage();
    await screen.findByRole("table");
    await userEvent.click(screen.getByRole("button", { name: "Okumayı durdur" }));
    await userEvent.click(screen.getByRole("button", { name: /Onayla/ }));
    expect(
      await screen.findByText(
        "“Okumayı durdur” kaydedildi. Her aile okuyan scope'larını sonraki toplamasında sonlandırır; henüz hiçbir scope değişmedi.",
      ),
    ).toBeInTheDocument();
  });

  it("words the detail of a conflict in Turkish and keeps an uncoded one as sent", async () => {
    vi.spyOn(api, "historyBackfill").mockResolvedValue([consoleOf()]);
    const recorded = reading();
    const detail = "a Yes is never reversed; stop the reading instead";
    vi.spyOn(api, "decideHistoryBackfill").mockRejectedValue(
      new ApiError(409, "x", detail, {
        detail,
        detail_code: "history.decide.never_reversed",
        detail_params: {},
        console: recorded,
      }),
    );
    mountPage();
    await screen.findByRole("table");
    await userEvent.click(screen.getByRole("button", { name: "Hayır olarak kaydet" }));
    await userEvent.click(screen.getByRole("button", { name: /Onayla/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Değiştirilmedi. Evet yanıtı hiçbir zaman geri alınmaz; bunun yerine okumayı durdurun. Şu anda kayıtlı olan:",
    );
  });
});

describe("the later-Yes words of a row", () => {
  it("are worded from their code in Turkish and stay the API's text in English", () => {
    const console = consoleOf();
    const turkish = rowsOf(console, TR, readTr).map((row) => row.laterYes);
    expect(turkish).toContain("Mümkün");
    expect(turkish).not.toContain("possible");
    expect(rowsOf(console, EN, readEn).map((row) => row.laterYes)).toContain("possible");
  });

  it("are worded for a scope row as well as for a family without scopes", () => {
    const console = consoleOf({
      families: [family({ family: "PHASE_A", scopes: [scope({ scope_key: "" })] })],
    });
    expect(rowsOf(console, TR, readTr).map((row) => row.laterYes)).toEqual(["Mümkün"]);
    expect(rowsOf(console, EN, readEn).map((row) => row.laterYes)).toEqual(["possible"]);
  });

  it("show the text as sent when the row has no code", () => {
    const console = consoleOf();
    for (const family of console.families) {
      family.later_yes_code = null;
      family.later_yes_params = null;
    }
    expect(rowsOf(console, TR, readTr).map((row) => row.laterYes)).toContain("possible");
  });
});

describe("the first-run dialog's reader (the shell's lean catalog)", () => {
  // Only what the shell carries: the dialog is mounted outside every route's provider.
  const shellTr = createTranslator({
    locale: "tr",
    messages: shellMessages(tr as never) as never,
  });
  const shell: BehaviorI18n = { t: asTextTranslator(shellTr), locale: "tr" };
  const read = shellSentenceReader(shell);

  it("words an estimate sentence with a fractional hours value", () => {
    expect(
      read("the 28-day window fits in an estimated 0.1 hours", {
        code: "history.fit.fits",
        params: { days: 28, hours: 0.1 },
      }),
    ).toBe(`28${NB}günlük pencere tahminen 0,1${NB}saate sığıyor`);
  });

  it("keeps one decimal on a whole number of hours", () => {
    expect(
      read("x", { code: "history.fit.fits", params: { days: 28, hours: 72 } }),
    ).toContain("72,0");
  });

  it("joins the limits of a reduced window with ve", () => {
    const words = read("english", {
      code: "history.window.reduced",
      params: {
        asked: 28,
        days: 21,
        limits: {
          kind: "list",
          items: [
            { code: "history.window.limit_setting", params: { setting: "A", days: 21 } },
            { code: "history.window.limit_setting", params: { setting: "B", days: 22 } },
          ],
        },
      },
    });
    expect(words).toContain(" ve ");
    expect(words).not.toBe("english");
  });

  it("shows the API's text for what the shell cannot word", () => {
    // `noun` is an enum of a catalog the shell does not carry: the whole sentence stays English.
    expect(
      read("english text", {
        code: "history.estimate.empty_sources",
        params: { reason: { code: "history.estimate.no_ips", params: {} }, count: 1, noun: "tracked endpoint", limit: 72 },
      }),
    ).toBe("english text");
    expect(read("uncoded text", null)).toBe("uncoded text");
    expect(read("unknown", { code: "history.nope", params: {} })).toBe("unknown");
  });

  it("never mixes an unworded enum value into a Turkish sentence", () => {
    // With every key present (the whole catalog), the sentence is still left in the API's English:
    // its `noun` is an enum the lean reader does not word.
    const whole: BehaviorI18n = {
      t: asTextTranslator(createTranslator({ locale: "tr", messages: tr as never })),
      locale: "tr",
    };
    expect(
      shellSentenceReader(whole)("english text", {
        code: "history.estimate.empty_sources",
        params: {
          reason: { code: "history.estimate.no_ips", params: {} },
          count: 1,
          noun: "tracked endpoint",
          limit: 72,
        },
      }),
    ).toBe("english text");
  });

  it("shows the text exactly as sent in English", () => {
    const english = shellSentenceReader({ t: asTextTranslator(createTranslator({ locale: "en", messages: shellMessages(en as never) as never })), locale: "en" });
    expect(english("the 28-day window fits", { code: "history.fit.fits", params: { days: 28, hours: 0.1 } })).toBe(
      "the 28-day window fits",
    );
  });
});
