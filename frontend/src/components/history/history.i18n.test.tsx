import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createTranslator } from "next-intl";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setTestLocale } from "../../../vitest.setup";
import HistoryBackfillError from "@/app/admin/history-backfill/error";
import Loading from "@/app/admin/history-backfill/loading";
import Page, { generateMetadata } from "@/app/admin/history-backfill/page";
import { StaticSession, type SessionValue } from "@/components/auth/session";
import { asTextTranslator } from "@/i18n/textTranslator";
import { ApiError, api, type CallerInfo, type HistoryConsole } from "@/lib/api";
import type { BehaviorI18n } from "@/lib/behaviorI18n";

import en from "../../../messages/en";
import tr from "../../../messages/tr";
import { HistoryBackfillPage } from "./HistoryBackfillPage";
import { HistoryFirstRun } from "./HistoryFirstRun";
import { HistoryTable } from "./HistoryTable";
import { UndecidedNotice } from "./UndecidedNotice";
import { LAB_ID, consoleOf, decision, estimateFamily, estimateOf, family, scope } from "./fixtures";
import {
  confirmText,
  decidedBy,
  estimateText,
  familyName,
  familyStateText,
  formatDays,
  modeText,
  questionText,
  stateInfo,
} from "./historyFormat";
import { rowsOf } from "./historyRows";

const NB = " ";

function say(locale: "en" | "tr"): BehaviorI18n {
  const translate = createTranslator({ locale, messages: (locale === "tr" ? tr : en) as never });
  return { t: asTextTranslator(translate), locale };
}
const TR = say("tr");

function caller(permissions: string[]): CallerInfo {
  return {
    subject: "ada",
    email: null,
    permissions,
    effective_permissions: permissions,
    username: "ada",
    auth_source: "LOCAL",
    must_change_password: false,
    roles: ["admin"],
  };
}

function session(permissions: string[]): SessionValue {
  return { status: "ready", me: caller(permissions), error: null, reload: vi.fn(), signOut: vi.fn() };
}

function mountPage(permissions: string[] = ["admin:*"]) {
  return render(
    <StaticSession value={session(permissions)}>
      <HistoryBackfillPage />
    </StaticSession>,
  );
}

function reading(): HistoryConsole {
  return consoleOf({
    decision: decision(),
    mode: "ACTIVE",
    families: [
      family({
        scopes: [
          scope({
            gaps: [{ start: "2026-10-01T00:00:00Z", end: "2026-10-01T00:30:00Z", reason: "search failed 3 times" }],
            days_read: 9,
            days_target: 28,
            buckets_read: 1204,
            searches: 1204,
          }),
        ],
      }),
      family({
        family: "WAF",
        collection_reason: "WAF_COLLECTION_ENABLED is on",
        scopes: [
          scope({
            scope_key: "7",
            name: "web-7",
            state: "PARTIAL",
            floor_proved: true,
            floor: "2026-09-21T10:00:00Z",
            through: "2026-10-10T00:00:00Z",
            finished_at: "2026-10-10T01:00:00Z",
            reason: "the density gate stopped the reading",
            days_read: 19.5,
            buckets_read: 1,
            searches: 640,
            days_requested: 28,
            days_target: 20,
            last_error_class: "TIMEOUT",
            last_error_at: "2026-10-10T00:45:00Z",
            sources_cut: [{ key: "1", name: "cut-1" }, { key: "2", name: null }],
            sources_cut_count: 8,
            sources_silent: [{ key: "3", name: "quiet-3" }],
            sources_silent_count: 1,
          }),
          scope({ scope_key: "8", state: "UNREADABLE-NEW", progress_unreadable: true }),
        ],
        sources: [
          { scope_key: "9", name: "web-9", profile_enabled: true, later_yes: "not possible: this source already collected" },
          { scope_key: "10", name: "web-10", profile_enabled: false, later_yes: "possible" },
        ],
        later_yes: null,
      }),
      family({
        family: "CATCHALL",
        scopes: [scope({ state: "COMPLETE", finished_at: "2026-10-19T18:00:00Z", through: "2026-10-19T00:00:00Z", days_read: 28, buckets_read: 8064, searches: 90 })],
      }),
      family({ family: "RULE_MATCH", collection_enabled: false, collection_reason: "RULE_MATCH_COLLECTION_ENABLED is off" }),
      family({ family: "IPS", sources: [], later_yes: null }),
    ],
  });
}

beforeEach(() => {
  vi.restoreAllMocks();
  setTestLocale("tr");
  window.sessionStorage.clear();
  HTMLDialogElement.prototype.showModal = function showModal() {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function close() {
    this.removeAttribute("open");
  };
});

afterEach(() => {
  window.sessionStorage.clear();
});

describe("history backfill words in Turkish", () => {
  it("names the families, the states and the modes, and shows an unknown value as recorded", () => {
    expect(familyName("PHASE_A", TR)).toBe("Kaynak hacmi");
    expect(familyName("CATCHALL", TR)).toBe("Catch-all gönderenleri");
    expect(familyName("GEO", TR)).toBe("GEO");
    expect(familyName("constructor", TR)).toBe("constructor");
    expect(familyName("PHASE_A")).toBe("Source volume");
    const labels = ["NOT_REQUESTED", "WAITING", "READING", "COMPLETE", "PARTIAL", "UNAVAILABLE", "NOT_ELIGIBLE", "SWITCHED_OFF", "NOT_STARTED", "NOT_READ", "PROFILE_DISABLED"].map((s) => stateInfo(s, TR).label);
    expect(labels).toEqual(["İstenmedi", "Bekliyor", "Okunuyor", "Tamamlandı", "Kısmi", "Kullanılamıyor", "Uygun değil", "Kapalı", "Başlamadı", "Okunmadı", "Profil devre dışı"]);
    expect(stateInfo("PARTIAL", TR).tone).toBe("warn");
    expect(stateInfo("NEW_STATE", TR)).toEqual({ label: "NEW_STATE", tone: "neutral" });
    expect(stateInfo("constructor", TR)).toEqual({ label: "constructor", tone: "neutral" });
    expect(modeText("NO", TR)).toBe("Hayır: geçmiş okunmuyor");
    expect(modeText("STOPPED", TR)).toBe("Evet: okuma durduruldu");
    expect(modeText("SOMETHING_NEW", TR)).toBe("SOMETHING_NEW");
    expect(decidedBy({ source: "INSTALLER", decided_by: "installer" }, TR)).toBe("kurulum programı");
    expect(decidedBy({ source: "UI", decided_by: "bea" }, TR)).toBe("bea");
  });

  it("uses the Turkish number form for days, the question, the families and the estimates", () => {
    expect(formatDays(9, "tr")).toBe("9");
    expect(formatDays(0, "tr")).toBe("0,0");
    expect(formatDays(12.5, "tr")).toBe("12,5");
    expect(formatDays(12.5)).toBe("12.5");
    expect(questionText(28, TR)).toBe(`QRadar'dan son 28${NB}gün okunsun mu?`);
    expect(questionText(null, TR)).toBe("QRadar'ın geçmişi okunsun mu?");
    expect(
      familyStateText(family({ collection_enabled: false, collection_reason: "X is off" }), TR),
    ).toBe("Kapalı. X is off.");
    expect(
      familyStateText(family({ window: { days_requested: 28, days: null, available: false, limited_by: [], reason: null } }), TR),
    ).toBe("Kullanılamıyor. Pencere sığdırılamadı.");
    const sources = [
      { scope_key: "1", name: "a", profile_enabled: true, later_yes: "possible" },
      { scope_key: "2", name: "b", profile_enabled: true, later_yes: "not possible: x" },
    ];
    expect(familyStateText(family({ sources }), TR)).toContain("2 kaynak etkin; zaten toplanmış kaynak sayısı 1");
    expect(familyStateText(family({ sources: [] }), TR)).toBe("0 kaynak etkin; sonradan etkinleştireceğiniz kaynaklar, etkinleştirdiğinizde okunur.");
    expect(familyStateText(family(), TR)).toBe("Okunacak.");
    expect(estimateText(estimateFamily({ searches: 2100, hours: 72.5, reduced: true, reason: "20 of 28 days" }), TR)).toEqual({
      figures: `2.100 arama, yaklaşık 72,5${NB}saat`,
      note: "Azaltıldı: 20 of 28 days",
    });
    expect(estimateText(estimateFamily({ hours: null }), TR).figures).toBe("5 arama, süre tahmin edilmedi");
    expect(estimateText(estimateFamily({ refused: true, reason: "too long" }), TR)).toEqual({ figures: "Reddedildi", note: "too long" });
    expect(estimateText(estimateFamily({ searches: null, hours: null, reason: "switched off" }), TR)).toEqual({ figures: "—", note: "switched off" });
    expect(estimateText(undefined, TR)).toEqual({ figures: "—", note: "Bu aile için tahmin döndürülmedi." });
    expect(estimateText(estimateFamily({ searches: 2100, hours: 72 }))).toEqual({ figures: "2,100 searches, about 72 hours", note: null });
  });

  it("says what each action does, in Turkish, with the days when there are some", () => {
    expect(confirmText("yes", 28, TR)).toContain(`Bu, Evet yanıtını 28${NB}gün için kaydeder.`);
    expect(confirmText("yes", null, TR)).toContain("Bu, Evet yanıtını kaydeder.");
    expect(confirmText("no", null, TR)).toContain("Hayır yanıtını kaydeder");
    expect(confirmText("start", null, TR)).toContain("yeniden okumasını ister");
    expect(confirmText("stop", null, TR)).toContain("Hiçbir şey silinmez.");
    expect(confirmText("yes", 28)).toContain("This records Yes for 28 days.");
  });

  it("words a row's own reasons and keeps the API's reasons as sent", () => {
    const undecided = rowsOf(consoleOf(), TR);
    const waf = undecided.find((r) => r.family === "WAF")!;
    expect(waf.state.label).toBe("Okunmadı");
    expect(waf.stateCode).toBe("NOT_READ");
    expect(waf.reason).toBe("Bu console için kayıtlı bir karar yok.");
    expect(waf.scope).toBe("web-1");
    expect(waf.scopeSub).toBe("Log source 1");
    const ips = undecided.find((r) => r.family === "IPS")!;
    expect(ips.scope).toBe("Yapılandırılmış kaynak yok");
    // The API's own reason for a switched-off family is shown as sent.
    expect(ips.reason).toBe("IPS_COLLECTION_ENABLED is off");
    expect(ips.state.label).toBe("Kapalı");
    expect(rowsOf(consoleOf({ mode: "NO" }), TR)[0].reason).toBe("Yanıt Hayır.");
    expect(rowsOf(consoleOf({ mode: "STOPPED" }), TR)[0].reason).toBe("Okuma, bu başlamadan önce durduruldu.");
    const active = rowsOf(consoleOf({ mode: "ACTIVE" }), TR);
    expect(active[0].state.label).toBe("Başlamadı");
    expect(active[0].reason).toBe("Ailenin sonraki toplamasında oluşturulur.");
    expect(active[0].scope).toBe("Tüm console");
    expect(rowsOf(consoleOf({ mode: "ACTIVE" })).find((r) => r.family === "PHASE_A")!.reason).toBe(
      "It is created at the family's next collection.",
    );
  });
});

describe("the history table in Turkish", () => {
  const NOW = new Date("2026-10-21T12:00:00Z");

  it("words the headers, the states, the figures and the details of every row kind", () => {
    render(<HistoryTable console={reading()} now={NOW} />);
    const table = screen.getByRole("table", { name: undefined });
    for (const name of ["Aile", "Scope", "Durum", /İstenen pencere/, /Kanıtlanan pencere/, "Okunan", "Arama", /Okunan son zaman/]) {
      expect(within(table).getByRole("columnheader", { name })).toBeInTheDocument();
    }
    expect(screen.getByLabelText("Aile ve scope'a göre geçmiş veri okuma, lab-console")).toBeInTheDocument();
    const rowOf = (name: string, text?: string) =>
      screen
        .getAllByRole("rowheader", { name })
        .map((h) => h.closest("tbody") as HTMLElement)
        .find((r) => !text || r.textContent?.includes(text))!;
    const phase = rowOf("Kaynak hacmi");
    expect(within(phase).getByText("Okunuyor")).toHaveClass("pill");
    // A reading scope reads "9 / 28 gün" with no-break spaces around the slash.
    expect((within(phase).getAllByRole("cell")[4] as HTMLElement).textContent).toContain(`9${NB}/${NB}28${NB}gün`);
    expect(phase.textContent).toContain("1.204 zaman dilimi");
    expect(within(phase).getByText("1.204", { selector: "td.num:not(.hb-read):not(.hb-when)" })).toBeInTheDocument();
    expect(within(phase).getByLabelText("Okunanlardaki boşluklar")).toHaveTextContent("10-01 00:00 UTC – 10-01 00:30 UTC");
    expect(within(phase).getByText("search failed 3 times")).toBeInTheDocument();
    expect(within(phase).getByText("Neden")).toBeInTheDocument();
    expect(within(phase).getByText("Sonradan Evet denirse")).toBeInTheDocument();
    const waf = rowOf("WAF", "web-7");
    expect(within(waf).getByText("Kısmi")).toBeInTheDocument();
    expect(within(waf).getByText("28 istendi")).toBeInTheDocument();
    expect(within(waf).getByText("Pencere kanıtlandı")).toBeInTheDocument();
    expect(waf.textContent).toContain("19,5");
    expect(waf.textContent).toContain("1 zaman dilimi");
    expect(waf.textContent).toContain("tamamlandı");
    expect(within(waf).getByText("the density gate stopped the reading")).toBeInTheDocument();
    expect(waf.textContent).toContain("Son adım 10-10 00:45 UTC zamanında başarısız oldu (TIMEOUT). Sonraki toplama yeniden dener.");
    expect(waf.textContent).toContain("QRadar istenenden azını tutuyor: cut-1, kaynak 2 ve 6 daha.");
    expect(waf.textContent).toContain("Pencerede olay yok: quiet-3.");
    const unread = rowOf("WAF", "Log source 8");
    expect(within(unread).getByText("ilerleme okunamadı")).toBeInTheDocument();
    expect(within(unread).getByText("kaynak başına")).toHaveAttribute(
      "title",
      "Alt sınır kaynak başına kanıtlanır ve bu yanıtta yer almaz",
    );
    const disabled = rowOf("WAF", "web-10");
    expect(within(disabled).getByText("Profil devre dışı")).toBeInTheDocument();
    expect(disabled.textContent).toContain("Kaynağın profili devre dışı; bu yüzden toplanmıyor.");
    expect(rowOf("WAF", "web-9").textContent).toContain("not possible: this source already collected");
    const off = rowOf("Kural eşleşmeleri");
    expect(within(off).getByText("Kapalı")).toBeInTheDocument();
    expect(off.textContent).toContain("RULE_MATCH_COLLECTION_ENABLED is off");
    expect(rowOf("IPS").textContent).toContain("Yapılandırılmış kaynak yok");
    expect(rowOf("IPS").textContent).toContain("Yapılandırılmış kaynak yok; bir kaynak, profilini etkinleştirdiğinizde okunur.");
    expect(table.textContent).not.toMatch(/Family|Reason|Later Yes|searches|sources/);
  });

  it("keeps English in English, with the English number form", () => {
    setTestLocale("en");
    render(<HistoryTable console={reading()} now={NOW} />);
    expect(screen.getAllByText("Later Yes").length).toBeGreaterThan(0);
    expect(document.body.textContent).toContain("1,204 buckets");
    expect(document.body.textContent).toContain("9 of 28 days");
    expect(document.body.textContent).toContain("and 6 more");
  });

  it("draws a missing measurement as a dash and never as a zero", () => {
    const c = consoleOf({
      decision: decision(),
      mode: "ACTIVE",
      families: [family({ scopes: [scope({ days_read: null, buckets_read: null, searches: null, through: null, finished_at: null })] })],
    });
    render(<HistoryTable console={c} now={NOW} />);
    const row = screen.getByRole("rowheader", { name: "Kaynak hacmi" }).closest("tbody") as HTMLElement;
    const cells = within(row).getAllByRole("cell");
    expect(cells[4].textContent).toBe("—");
    expect(cells[5].textContent).toBe("—");
    expect(row.textContent).not.toMatch(/\b0\b/);
  });
});

describe("the history backfill page states in Turkish", () => {
  it("says what it is loading", () => {
    vi.spyOn(api, "historyBackfill").mockReturnValue(new Promise(() => {}));
    mountPage();
    expect(screen.getByRole("status")).toHaveTextContent("Geçmiş veri okuma durumu yükleniyor…");
  });

  it("names the permission on a 403 and shows no table", async () => {
    vi.spyOn(api, "historyBackfill").mockRejectedValue(new ApiError(403, "forbidden"));
    mountPage(["read:offenses"]);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Geçmiş veri okuma gösterilemedi.");
    expect(alert).toHaveTextContent("Rolünüz bunu okuyamıyor (izin: read:history_backfill). Hiçbir şey değiştirilmedi.");
    expect(alert.querySelector("code.perm")).toHaveTextContent("read:history_backfill");
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("says the session expired", async () => {
    vi.spyOn(api, "historyBackfill").mockRejectedValue(new ApiError(401, "x"));
    mountPage();
    expect(await screen.findByRole("alert")).toHaveTextContent("Oturumunuzun süresi doldu. Devam etmek için yeniden giriş yapın.");
  });

  it("says what failed and offers to try again", async () => {
    const read = vi.spyOn(api, "historyBackfill").mockRejectedValueOnce(new Error("down")).mockResolvedValue([reading()]);
    mountPage();
    expect(await screen.findByRole("alert")).toHaveTextContent("Geçmiş veri okuma yüklenemedi.");
    await userEvent.click(screen.getByRole("button", { name: "Tekrar dene" }));
    expect(await screen.findByRole("table")).toBeInTheDocument();
    expect(read).toHaveBeenCalledTimes(2);
  });

  it("says no console is enabled, and why there is nothing to show", async () => {
    vi.spyOn(api, "historyBackfill").mockResolvedValue([]);
    mountPage();
    const note = (await screen.findByText("Etkin bir QRadar console'u yok.")).closest(".notice") as HTMLElement;
    expect(note).toHaveAttribute("role", "status");
    expect(note).toHaveTextContent("bu yüzden gösterilecek bir şey yok");
  });

  it("states the answer, who, when and how, and the mode", async () => {
    vi.spyOn(api, "historyBackfill").mockResolvedValue([reading()]);
    mountPage();
    expect(await screen.findByText(/Geçmiş veri okuma: Evet/)).toHaveTextContent(
      `Geçmiş veri okuma: Evet, 28 gün okunacak. Kaydeden: kurulum programı, 2026-10-19 12:00:00 UTC.`,
    );
    expect(screen.getByText(/Mod:/)).toHaveTextContent("Mod: Evet: geçmiş okunuyor.");
    expect(screen.getByRole("region", { name: "Console lab-console" })).toBeInTheDocument();
  });

  it("states an undecided console and a No", async () => {
    vi.spyOn(api, "historyBackfill").mockResolvedValue([consoleOf()]);
    const first = mountPage();
    expect(await screen.findByText(/karar verilmedi\. QRadar'ın geçmişinden hiçbir şey okunmuyor\./)).toBeInTheDocument();
    first.unmount();
    vi.spyOn(api, "historyBackfill").mockResolvedValue([
      consoleOf({ decision: decision({ answer: "NO", days: null, source: "UI", decided_by: "bea" }), mode: "NO" }),
    ]);
    mountPage();
    expect(await screen.findByText(/Hayır, geçmiş okunmuyor/)).toHaveTextContent("Kaydeden: bea");
  });

  it("names the permission to a caller who cannot change the answer", async () => {
    vi.spyOn(api, "historyBackfill").mockResolvedValue([reading()]);
    mountPage(["read:history_backfill"]);
    await screen.findByRole("table");
    expect(document.body).toHaveTextContent("Yanıtı değiştirmek için admin:history_backfill izni gerekir.");
    expect(screen.queryByRole("button", { name: "Okumayı durdur" })).toBeNull();
  });
});

describe("the decision block in Turkish", () => {
  it("asks in plain words before it records Yes, then shows the recorded line", async () => {
    const no = consoleOf({ decision: decision({ answer: "NO", days: null, source: "UI", decided_by: "bea" }), mode: "NO" });
    vi.spyOn(api, "historyBackfill").mockResolvedValue([no]);
    const decide = vi.spyOn(api, "decideHistoryBackfill").mockResolvedValue(reading());
    mountPage();
    await screen.findByRole("table");
    expect(screen.getByRole("group", { name: "lab-console için eylemler" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Geçmişi şimdi oku" }));
    const confirm = screen.getByRole("group", { name: "“Geçmişi şimdi oku” eylemi onaylansın mı?" });
    expect(confirm).toHaveTextContent("Bu, Evet yanıtını 28 gün için kaydeder.");
    expect(decide).not.toHaveBeenCalled();
    await userEvent.click(within(confirm).getByRole("button", { name: "Onayla: Geçmişi şimdi oku" }));
    expect(decide).toHaveBeenCalledWith(LAB_ID, "YES");
    expect(await screen.findByRole("status")).toHaveTextContent("Kaydedildi. Geçmiş veri okuma: Evet");
    expect(screen.getByRole("button", { name: "Okumayı durdur" })).toBeInTheDocument();
  });

  it("cancels a confirmation without sending anything", async () => {
    vi.spyOn(api, "historyBackfill").mockResolvedValue([reading()]);
    const stop = vi.spyOn(api, "stopHistoryBackfill");
    mountPage();
    await screen.findByRole("table");
    await userEvent.click(screen.getByRole("button", { name: "Okumayı durdur" }));
    const confirm = screen.getByRole("group", { name: "“Okumayı durdur” eylemi onaylansın mı?" });
    expect(confirm).toHaveTextContent("Hiçbir şey silinmez.");
    await userEvent.click(within(confirm).getByRole("button", { name: "İptal" }));
    expect(stop).not.toHaveBeenCalled();
  });

  it("shows the API's own message as sent after a stop, and names who asked", async () => {
    vi.spyOn(api, "historyBackfill").mockResolvedValue([reading()]);
    const message = "Stop reading is recorded. Each family ends its reading scopes at its next collection.";
    vi.spyOn(api, "stopHistoryBackfill").mockResolvedValue({
      ...reading(),
      decision: decision({ stop_requested_at: "2026-10-20T08:00:00Z", stop_requested_by: "ada", start_requested_at: "2026-10-19T13:00:00Z", start_requested_by: null }),
      mode: "STOPPED",
      message,
    });
    mountPage();
    await screen.findByRole("table");
    await userEvent.click(screen.getByRole("button", { name: "Okumayı durdur" }));
    await userEvent.click(screen.getByRole("button", { name: /Onayla/ }));
    expect(await screen.findByText(message)).toBeInTheDocument();
    expect(screen.getByText(/Mod:/)).toHaveTextContent("Mod: Evet: okuma durduruldu.");
    expect(screen.getByText(/Mod:/)).toHaveTextContent("“Geçmişi şimdi oku” eylemini bir operatör 2026-10-19 13:00:00 UTC tarihinde istedi.");
    expect(screen.getByText(/Mod:/)).toHaveTextContent("“Okumayı durdur” eylemini ada 2026-10-20 08:00:00 UTC tarihinde istedi.");
  });

  it("names the permission on a 403, and says nothing changed", async () => {
    vi.spyOn(api, "historyBackfill").mockResolvedValue([reading()]);
    vi.spyOn(api, "stopHistoryBackfill").mockRejectedValue(new ApiError(403, "no"));
    mountPage();
    await screen.findByRole("table");
    await userEvent.click(screen.getByRole("button", { name: "Okumayı durdur" }));
    await userEvent.click(screen.getByRole("button", { name: /Onayla/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Değiştirilmedi. Rolünüz geçmiş veri okumayı değiştiremiyor (izin: admin:history_backfill).",
    );
  });

  it("shows what is recorded now after a conflict, with the server's detail as sent", async () => {
    vi.spyOn(api, "historyBackfill").mockResolvedValue([consoleOf()]);
    const recorded = consoleOf({ decision: decision({ source: "UI", decided_by: "bea" }), mode: "ACTIVE" });
    vi.spyOn(api, "decideHistoryBackfill").mockRejectedValue(
      new ApiError(409, "x", "A decision already exists", { detail: "A decision already exists", console: recorded }),
    );
    mountPage();
    await screen.findByRole("table");
    await userEvent.click(screen.getByRole("button", { name: "Hayır olarak kaydet" }));
    await userEvent.click(screen.getByRole("button", { name: /Onayla/ }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Değiştirilmedi. A decision already exists. Şu anda kayıtlı olan: Geçmiş veri okuma: Evet, 28 gün okunacak.");
    expect(alert).toHaveTextContent("Kaydeden: bea");
  });

  it("says a refusal without a console body, and a request that did not complete", async () => {
    vi.spyOn(api, "historyBackfill").mockResolvedValue([reading()]);
    const stop = vi.spyOn(api, "stopHistoryBackfill").mockRejectedValueOnce(new ApiError(409, "x", "Already stopped"));
    mountPage();
    await screen.findByRole("table");
    await userEvent.click(screen.getByRole("button", { name: "Okumayı durdur" }));
    await userEvent.click(screen.getByRole("button", { name: /Onayla/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Değiştirilmedi. Already stopped.");
    stop.mockRejectedValueOnce(new ApiError(500, "psycopg2 exploded"));
    await userEvent.click(screen.getByRole("button", { name: "Okumayı durdur" }));
    await userEvent.click(screen.getByRole("button", { name: /Onayla/ }));
    const alert = await screen.findByText(/İstek tamamlanamadı/);
    expect(alert).toHaveTextContent("Backend'e erişilebildiğini denetleyip tekrar deneyin.");
    expect(alert).not.toHaveTextContent("psycopg2");
  });
});

describe("the first-run dialog in Turkish", () => {
  function mount() {
    return render(
      <StaticSession value={session(["admin:history_backfill"])}>
        <HistoryFirstRun />
      </StaticSession>,
    );
  }

  it("asks the question with the days, lists the families, the estimates and the reasons", async () => {
    vi.spyOn(api, "historyBackfill").mockResolvedValue([consoleOf()]);
    vi.spyOn(api, "historyBackfillEstimate").mockResolvedValue([estimateOf()]);
    mount();
    const dialog = await screen.findByRole("dialog", { name: /^QRadar'dan son 28.gün okunsun mu\?$/ });
    await within(dialog).findByRole("button", { name: "Evet" });
    expect(within(dialog).getByRole("button", { name: "Sonra karar ver" })).toBeInTheDocument();
    expect(within(dialog).getByRole("region", { name: "Neyin okunacağı ve maliyeti" })).toBeInTheDocument();
    const table = within(dialog).getByRole("table");
    for (const name of ["Aile", "Durum", "Tahmin"]) {
      expect(within(table).getByRole("columnheader", { name })).toBeInTheDocument();
    }
    const row = (name: string) => within(table).getByRole("rowheader", { name }).closest("tr") as HTMLElement;
    expect(row("Kaynak hacmi")).toHaveTextContent("5 arama, yaklaşık 0,1 saat");
    expect(row("WAF")).toHaveTextContent("1 kaynak etkin; sonradan etkinleştireceğiniz kaynaklar, etkinleştirdiğinizde okunur.");
    expect(row("WAF")).toHaveTextContent("2.100 arama, yaklaşık 72 saat");
    expect(row("WAF")).toHaveTextContent("Azaltıldı: 20 of 28 days fit in 72 hours");
    expect(row("IPS")).toHaveTextContent("Kapalı. IPS_COLLECTION_ENABLED is off.");
    expect(row("IPS")).toHaveTextContent("no estimate: collection is switched off");
    expect(row("IPS")).not.toHaveTextContent(/\b0 arama\b/);
    expect(row("Coğrafya")).toHaveTextContent("Dışarıda bırakıldı. Doldurulacak baseline yok.");
    expect(within(dialog).getByRole("list", { name: "WAF için varsayımlar" })).toBeInTheDocument();
    expect(dialog).toHaveTextContent("Bunlar ölçüm değil, bir modelin tahminleridir.");
    expect(dialog).toHaveTextContent("Eski hiçbir veri uyarı oluşturmaz.");
    expect(dialog).toHaveTextContent("yaklaşık 72 saat boyunca salt okunur aramalar gönderir");
    expect(dialog).toHaveTextContent("Özellik izlemeleri, izlemeyi oluşturduğunuzda geçmiş ister.");
    expect(within(dialog).getByRole("group", { name: "Yanıtınız" })).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Hayır" })).toBeInTheDocument();
    expect(dialog.textContent).not.toMatch(/Decide later|estimates from a model|Your answer/);
  });

  it("says it is loading, and what failed when the estimate cannot be read", async () => {
    vi.spyOn(api, "historyBackfill").mockResolvedValue([consoleOf()]);
    let fail: (e: unknown) => void = () => {};
    vi.spyOn(api, "historyBackfillEstimate").mockReturnValue(new Promise((_, reject) => (fail = reject)));
    mount();
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("status")).toHaveTextContent("Geçmiş veri okuma durumu yükleniyor…");
    await act(async () => fail(new Error("down")));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Tahmin yüklenemedi.");
    expect(dialog).toHaveTextContent("daha sonra Yönetim altındaki Geçmiş veri okuma sayfasından karar verin.");
    expect(within(dialog).getByRole("button", { name: "Evet" })).toBeDisabled();
  });

  it("says the status could not be loaded, in its own dialog", async () => {
    vi.spyOn(api, "historyBackfill").mockRejectedValue(new Error("down"));
    mount();
    const dialog = await screen.findByRole("dialog", { name: "Geçmiş veri okuma durumu yüklenemedi" });
    expect(dialog).toHaveTextContent("QRadar'ın geçmişinin okunup okunmayacağı henüz bilinmiyor.");
    expect(within(dialog).getByRole("button", { name: "Tekrar dene" })).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Kapat" })).toBeInTheDocument();
  });

  it("records an answer, and names the permission or the conflict when it is refused", async () => {
    vi.spyOn(api, "historyBackfill").mockResolvedValue([consoleOf(), consoleOf({ instance_id: "x2", name: "second" })]);
    vi.spyOn(api, "historyBackfillEstimate").mockResolvedValue([estimateOf(), estimateOf({ instance_id: "x2", name: "second" })]);
    const decide = vi
      .spyOn(api, "decideHistoryBackfill")
      .mockRejectedValueOnce(new ApiError(403, "no"))
      .mockRejectedValueOnce(new Error("down"))
      .mockResolvedValueOnce(consoleOf({ decision: decision({ source: "UI", decided_by: "ada" }), mode: "ACTIVE" }));
    mount();
    const dialog = await screen.findByRole("dialog");
    await within(dialog).findAllByRole("button", { name: "Evet" });
    const answers = within(dialog).getByRole("group", { name: "Yanıtınız" });
    const yes = () => within(answers).getAllByRole("button", { name: "Evet" });
    await userEvent.click(yes()[0]);
    expect(await within(answers).findByText(/Rolünüz yanıtı kaydedemiyor \(izin: admin:history_backfill\)\./)).toHaveTextContent("Hiçbir şey değiştirilmedi.");
    await userEvent.click(yes()[0]);
    expect(await within(answers).findByText(/Yanıt kaydedilemedi\./)).toHaveTextContent("Backend'e erişilebildiğini denetleyip tekrar deneyin.");
    await userEvent.click(yes()[0]);
    expect(await within(answers).findByText(/Kaydedildi\. Geçmiş veri okuma: Evet/)).toBeInTheDocument();
    expect(decide).toHaveBeenCalledTimes(3);
    expect(within(dialog).getByRole("heading", { level: 3, name: "lab-console" })).toBeInTheDocument();
  });

  it("says a decision was recorded in the meantime", async () => {
    vi.spyOn(api, "historyBackfill").mockResolvedValue([consoleOf()]);
    vi.spyOn(api, "historyBackfillEstimate").mockResolvedValue([estimateOf()]);
    vi.spyOn(api, "decideHistoryBackfill").mockRejectedValue(
      new ApiError(409, "x", "exists", { detail: "exists", console: consoleOf({ decision: decision({ source: "UI", decided_by: "bea" }), mode: "ACTIVE" }) }),
    );
    mount();
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(await within(dialog).findByRole("button", { name: "Hayır" }));
    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent("Yanıtınız kaydedilmedi. Bu arada bir karar kaydedildi. Geçmiş veri okuma: Evet");
    expect(within(dialog).getByRole("button", { name: "Kapat" })).toBeInTheDocument();
  });

  it("says the cost is not estimated when the hours are missing", async () => {
    vi.spyOn(api, "historyBackfill").mockResolvedValue([consoleOf()]);
    vi.spyOn(api, "historyBackfillEstimate").mockResolvedValue([estimateOf({ families: [estimateFamily({ hours: null })] })]);
    mount();
    const dialog = await screen.findByRole("dialog");
    await within(dialog).findByRole("button", { name: "Evet" });
    expect(dialog).toHaveTextContent("ne kadar süreceği tahmin edilmedi");
  });
});

describe("the undecided notice and the route in Turkish", () => {
  it("shows the notice with its link", async () => {
    vi.spyOn(api, "historyBackfill").mockResolvedValue([consoleOf()]);
    render(
      <StaticSession value={session(["read:history_backfill"])}>
        <UndecidedNotice />
      </StaticSession>,
    );
    const notice = await screen.findByRole("status");
    expect(notice).toHaveTextContent("Geçmiş veri okuma kararı verilmedi. Karar verilene kadar QRadar'ın geçmişinden hiçbir şey okunmaz.");
    expect(within(notice).getByRole("link", { name: "Geçmiş veri okumayı aç" })).toHaveAttribute("href", "/admin/history-backfill");
    expect(within(notice).getByText("Geçmiş veri okuma kararı verilmedi.").tagName).toBe("STRONG");
  });

  it("shows nothing when every console has decided", async () => {
    vi.spyOn(api, "historyBackfill").mockResolvedValue([reading()]);
    render(
      <StaticSession value={session(["read:history_backfill"])}>
        <UndecidedNotice />
      </StaticSession>,
    );
    await act(async () => {});
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("titles the route and keeps its boundaries in Turkish", async () => {
    vi.spyOn(api, "historyBackfill").mockReturnValue(new Promise(() => {}));
    const first = render(<Page />);
    expect(await generateMetadata()).toEqual({ title: "Geçmiş veri okuma" });
    expect(screen.getByRole("heading", { level: 1, name: "Geçmiş veri okuma" })).toBeInTheDocument();
    expect(screen.getByText(/Eski veri yalnızca öğrenme için kullanılır/)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/canlı|gerçek zamanlı|anlık|!/);
    first.unmount();
    const reset = vi.fn();
    const second = render(<HistoryBackfillError error={Object.assign(new Error("secret"), { digest: "d-9" })} reset={reset} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Geçmiş veri okuma gösterilemedi.");
    expect(screen.getByRole("alert")).toHaveTextContent("Referans: d-9.");
    expect(screen.getByRole("alert")).not.toHaveTextContent("secret");
    await userEvent.click(screen.getByRole("button", { name: "Tekrar dene" }));
    expect(reset).toHaveBeenCalledTimes(1);
    second.unmount();
    render(<Loading />);
    expect(screen.getByRole("status")).toHaveTextContent("Geçmiş veri okuma durumu yükleniyor…");
    await waitFor(() => undefined);
  });
});
