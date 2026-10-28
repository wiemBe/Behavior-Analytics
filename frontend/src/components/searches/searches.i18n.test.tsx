import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createTranslator } from "next-intl";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setTestLocale } from "../../../vitest.setup";
import { asTextTranslator } from "@/i18n/textTranslator";
import { ApiError, api, type ScheduledSearch, type SearchExecution } from "@/lib/api";
import type { BehaviorI18n } from "@/lib/behaviorI18n";

import en from "../../../messages/en";
import tr from "../../../messages/tr";
import {
  SEARCH_LATENCY_STATEMENT,
  capMessage,
  classifySaveError,
  emptyDraft,
  parseDraft,
  runRequestFailure,
  scheduleOptions,
  visualizationLabel,
} from "./authoring";
import { CONFIRM_TEXT } from "../history/historyFormat";
import { ExecutionsTable } from "./ExecutionsTable";
import { LatestRunStatus } from "./LatestRunStatus";
import { ScheduleControl } from "./ScheduleControl";
import { ScheduleLabel } from "./ScheduleLabel";
import { SearchEditor } from "./SearchEditor";
import { SearchEnableControl } from "./SearchEnableControl";
import { SearchRunControl } from "./SearchRunControl";
import { SearchWriteNotice } from "./SearchWriteNotice";

const refresh = vi.fn();
const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh, push }) }));

const NB = " ";

function say(locale: "en" | "tr"): BehaviorI18n {
  const translate = createTranslator({ locale, messages: (locale === "tr" ? tr : en) as never });
  return { t: asTextTranslator(translate), locale };
}

function execution(over: Partial<SearchExecution> = {}): SearchExecution {
  return {
    id: "e-1",
    search_id: "s-1",
    query_version: 2,
    status: "COMPLETED",
    trigger: "SCHEDULED",
    triggered_by: null,
    ariel_search_id: null,
    ariel_status: null,
    started_at: "2026-10-15T04:00:00Z",
    completed_at: "2026-10-15T04:00:41Z",
    duration_ms: 41200,
    result_count: 1204,
    truncated: false,
    error_type: null,
    error_message: null,
    retry_count: 1,
    threshold_breached: false,
    ...over,
  };
}

function search(over: Partial<ScheduledSearch> = {}): ScheduledSearch {
  return {
    id: "s-1",
    name: "Repeated authentication failures",
    description: "Counts failures per source",
    owner: "SOC",
    category: "Credential Access",
    mitre_techniques: ["T1110"],
    aql_query: "SELECT count(*) FROM events",
    query_version: 2,
    schedule_cron: "17 3 * * 1",
    schedule_timezone: "UTC",
    severity: "HIGH",
    threshold_value: 10,
    threshold_operator: "GT",
    timeout_seconds: 300,
    max_time_range_hours: 24,
    max_result_rows: 10000,
    visualization_type: "PIE",
    enabled: false,
    last_run_at: null,
    next_run_at: null,
    consecutive_failures: 0,
    ...over,
  };
}

beforeEach(() => {
  vi.restoreAllMocks();
  refresh.mockClear();
  push.mockClear();
  setTestLocale("tr");
});

describe("authoring helpers in Turkish", () => {
  const TR = say("tr");

  it("words the validation messages in the Turkish number form and keeps English by default", () => {
    const draft = { ...emptyDraft(), name: "x", aql: "SELECT 1" };
    expect(parseDraft({ ...draft, name: "" }, "create", TR)).toEqual({
      field: "name",
      text: "Ad zorunludur.",
    });
    expect(parseDraft({ ...draft, aql: " " }, "edit", TR)?.text).toBe("AQL zorunludur.");
    expect(parseDraft({ ...draft, timeout: "9000" }, "edit", TR)?.text).toBe(
      "1 ile 900 arasında tam sayı olarak saniye girin.",
    );
    expect(parseDraft({ ...draft, rows: "0" }, "edit", TR)?.text).toBe(
      "1 ile 10.000 arasında tam sayı olarak satır girin.",
    );
    expect(parseDraft({ ...draft, windowHours: "x" }, "edit", TR)?.text).toContain("saat girin.");
    expect(parseDraft({ ...draft, threshold: "abc" }, "edit", TR)?.text).toBe(
      "Eşik bir sayı olmalıdır; aramanın eşiği yoksa boş bırakın.",
    );
    expect(parseDraft({ ...draft, description: "x".repeat(2001) }, "edit", TR)?.text).toBe(
      "Açıklama en fazla 2.000 karakter olabilir.",
    );
    expect(parseDraft({ ...draft, rows: "0" }, "edit")?.text).toBe(
      "Enter a whole number of rows, from 1 to 10000.",
    );
  });

  it("words each refusal and keeps the server's own text as sent", () => {
    expect(classifySaveError(new Error("x"), TR)).toEqual({
      kind: "error",
      detail: "Arama kaydedilemedi. Bağlantıyı denetleyip tekrar deneyin.",
    });
    const forbidden = classifySaveError(new ApiError(403, "x"), TR);
    expect(forbidden.kind).toBe("forbidden");
    expect(forbidden).toMatchObject({ detail: expect.stringContaining("search:write") });
    expect(classifySaveError(new ApiError(404, "x"), TR)).toEqual({
      kind: "error",
      detail: "Bu arama artık yok. Sayfayı yenileyin.",
    });
    expect(classifySaveError(new ApiError(503, "x"), TR)).toMatchObject({
      detail: "Sunucu aramayı kaydedemedi. Kısa süre sonra tekrar deneyin.",
    });
    expect(classifySaveError(new ApiError(418, "x", ""), TR)).toMatchObject({
      detail: "Arama kaydedilmedi.",
    });
    // The server's reasons and detail are English by decision D-37: shown as sent.
    expect(
      classifySaveError(new ApiError(422, "x", { reasons: ["Unknown field."] }), TR),
    ).toEqual({ kind: "reasons", reasons: ["Unknown field."] });
    expect(capMessage("At most 20 searches may be enabled.", TR)).toBe(
      "Etkinleştirilmedi: At most 20 searches may be enabled. Başka bir aramayı devre dışı bırakın, sonra bunu etkinleştirin.",
    );
    expect(capMessage("At most 20 searches may be enabled.")).toBe(
      "Not enabled: At most 20 searches may be enabled. Disable another search, then enable this one.",
    );
  });

  it("words the run refusals", () => {
    expect(runRequestFailure(new ApiError(403, "x"), TR)).toBe(
      "Rolünüz saklanan bir aramayı çalıştıramıyor (izin: search:execute).",
    );
    expect(runRequestFailure(new ApiError(404, "x"), TR)).toBe("Bu arama artık yok. Sayfayı yenileyin.");
    expect(runRequestFailure(new Error("x"), TR)).toBe("Çalıştırma başlamadı. Sayfayı yenileyip tekrar deneyin.");
  });

  it("words the visualizations and the schedule options, with an unknown value as recorded", () => {
    expect(visualizationLabel("TABLE", TR)).toBe("Tablo");
    expect(visualizationLabel("SINGLE_VALUE", TR)).toBe("Tek değer");
    expect(visualizationLabel("HEATMAP", TR)).toBe("Isı haritası, saklanan");
    expect(visualizationLabel("RADAR", TR)).toBe("RADAR");
    expect(visualizationLabel("constructor", TR)).toBe("constructor");
    expect(visualizationLabel("")).toBe("");
    expect(scheduleOptions("17 3 * * 1", null, TR).map((o) => o.label)).toEqual([
      "Özel",
      `Her 5${NB}dk`,
      `Her 15${NB}dk`,
      `Her 30${NB}dk`,
      `Her 1${NB}sa`,
      `Her 2${NB}sa`,
      `Her 4${NB}sa`,
      `Her 8${NB}sa`,
    ]);
  });

  it("keeps the English constants equal to the catalog (one source of English)", () => {
    expect(en.searches.editor.latency).toBe(SEARCH_LATENCY_STATEMENT);
    expect(en.history.confirm.no).toBe(CONFIRM_TEXT.no);
    expect(en.history.confirm.start).toBe(CONFIRM_TEXT.start);
    expect(en.history.confirm.stop).toBe(CONFIRM_TEXT.stop);
    expect(en.history.confirm.yes.replace("{days}", "")).toBe(CONFIRM_TEXT.yes(null));
  });
});

describe("the executions table in Turkish", () => {
  it("words the states, the triggers and the numbers, and keeps the times in UTC", () => {
    render(
      <ExecutionsTable
        executions={[
          execution(),
          execution({
            id: "e-2",
            status: "FAILED",
            trigger: "MANUAL",
            error_type: "TIMEOUT",
            result_count: null,
            duration_ms: 300000,
            retry_count: 0,
          }),
          execution({ id: "e-3", status: "TIMEOUT", truncated: true, result_count: 10000 }),
          execution({ id: "e-4", status: "RUNNING", result_count: null, duration_ms: null }),
          execution({ id: "e-5", status: "STRANGE", truncated: true, result_count: null }),
          execution({ id: "e-6", threshold_breached: true, result_count: 12 }),
        ]}
      />,
    );
    const table = screen.getByRole("table");
    expect(within(table).getByRole("columnheader", { name: "Başlangıç (UTC)" })).toBeInTheDocument();
    for (const name of ["Durum", "Tetikleyici", "Sürüm", "Süre", "Sonuç", "Yeniden deneme", "Eşik", "Hata"]) {
      expect(within(table).getByRole("columnheader", { name })).toBeInTheDocument();
    }
    const rows = within(table).getAllByRole("row").slice(1);
    expect(within(rows[0]).getByText("TAMAMLANDI")).toHaveClass("pill", "ok");
    expect(within(rows[0]).getByText("Zamanlanmış")).toBeInTheDocument();
    expect(within(rows[0]).getByText("1.204")).toBeInTheDocument();
    // getByText folds a no-break space into a plain one: read the cell's own text to pin it.
    const cell = (row: HTMLElement, index: number) => (row as HTMLTableRowElement).cells[index].textContent;
    expect(cell(rows[0], 4)).toBe(`41,2${NB}sn`);
    expect(within(rows[0]).getByText("10-15 04:00")).toBeInTheDocument();
    expect(within(rows[1]).getByText("BAŞARISIZ")).toHaveClass("pill", "crit");
    expect(within(rows[1]).getByText("Elle")).toBeInTheDocument();
    expect(cell(rows[1], 4)).toBe(`5${NB}dk 0${NB}sn`);
    // The error type is the backend's own code, as sent.
    expect(within(rows[1]).getByText("TIMEOUT")).toBeInTheDocument();
    expect(within(rows[2]).getByText("ZAMAN AŞIMI")).toBeInTheDocument();
    expect(within(rows[2]).getByText("10.000")).toBeInTheDocument();
    expect(within(rows[2]).getByText("en az")).toHaveClass("sr-only");
    expect(within(rows[2]).getByText("karar yok")).toHaveAttribute(
      "title",
      "Karar yok: sayı bir alt sınırdır; bu yüzden eşik değerlendirilemez",
    );
    expect(within(rows[3]).getByText("ÇALIŞIYOR")).toBeInTheDocument();
    expect(within(rows[3]).getAllByText("—").length).toBeGreaterThanOrEqual(2);
    // An unknown state is shown as recorded and the missing count is not drawn as zero.
    expect(within(rows[4]).getByText("STRANGE")).toBeInTheDocument();
    expect(within(rows[4]).getByText("kırpıldı, sayı kaydedilmedi")).toBeInTheDocument();
    expect(within(rows[5]).getByText("aşıldı")).toHaveClass("pill", "crit");
    const notes = screen.getByRole("list", { name: "Kırpılmış çalıştırmalar" });
    expect(notes).toHaveTextContent("“≥” kırpılmış bir çalıştırmayı gösterir");
    expect(notes).toHaveTextContent("“Karar yok”");
    expect(table).not.toHaveTextContent(/Started|Threshold|truncated|no verdict/);
  });

  it("shows no note list when no run was truncated, and keeps English in English", () => {
    const { unmount } = render(<ExecutionsTable executions={[execution()]} />);
    expect(screen.queryByRole("list")).toBeNull();
    unmount();
    setTestLocale("en");
    render(<ExecutionsTable executions={[execution()]} />);
    expect(screen.getByText("COMPLETED")).toBeInTheDocument();
    expect(screen.getByText("SCHEDULED")).toBeInTheDocument();
    expect(screen.getByText("1204")).toBeInTheDocument();
    expect(screen.getByText("41.2 s")).toBeInTheDocument();
  });
});

describe("the run readout and the run controls in Turkish", () => {
  it("words the latest run in a status line", () => {
    render(<LatestRunStatus execution={execution({ truncated: true, result_count: 500 })} consecutiveFailures={null} />);
    expect(screen.getByRole("status")).toHaveTextContent("Son çalıştırma kırpıldı");
  });

  it("groups the counts the Turkish way and prints them plain in English", () => {
    const { unmount } = render(
      <LatestRunStatus execution={execution({ result_count: 1204 })} consecutiveFailures={null} />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Son çalıştırma tamamlandı ve 1.204 satır döndürdü.");
    unmount();
    const bound = render(
      <LatestRunStatus execution={execution({ truncated: true, result_count: 10000 })} consecutiveFailures={null} />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("En az 10.000 satır saydı.");
    bound.unmount();
    setTestLocale("en");
    render(<LatestRunStatus execution={execution({ result_count: 1204 })} consecutiveFailures={null} />);
    expect(screen.getByRole("status")).toHaveTextContent("The latest run completed and returned 1204 rows.");
  });

  it("names the permission when a run is refused", async () => {
    vi.spyOn(api, "runSearch").mockRejectedValue(new ApiError(403, "no"));
    render(<SearchRunControl searchId="s-1" />);
    await userEvent.click(screen.getByRole("button", { name: "Şimdi çalıştır" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Rolünüz saklanan bir aramayı çalıştıramıyor (izin: search:execute).",
    );
    expect(screen.queryByText(/Run now|Running/)).toBeNull();
  });

  it("shows the running label while the run is pending", async () => {
    let finish: (value: SearchExecution) => void = () => {};
    vi.spyOn(api, "runSearch").mockReturnValue(new Promise((resolve) => (finish = resolve)));
    render(<SearchRunControl searchId="s-1" />);
    await userEvent.click(screen.getByRole("button", { name: "Şimdi çalıştır" }));
    expect(screen.getByRole("button", { name: "Çalışıyor…" })).toBeDisabled();
    finish(execution());
    await waitFor(() => expect(screen.getByRole("button", { name: "Şimdi çalıştır" })).toBeEnabled());
    expect(screen.getByRole("status")).toHaveTextContent("Son çalıştırma tamamlandı");
  });

  it("says why an enable is refused: the cap text is the server's, the advice is Turkish", async () => {
    vi.spyOn(api, "setSearchEnabled").mockRejectedValue(
      new ApiError(409, "x", "At most 20 searches may be enabled."),
    );
    render(<SearchEnableControl id="s-1" enabled={false} />);
    await userEvent.click(screen.getByRole("button", { name: "Aramayı etkinleştir" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Etkinleştirilmedi: At most 20 searches may be enabled. Başka bir aramayı devre dışı bırakın",
    );
  });

  it("offers Disable for an enabled search", () => {
    render(<SearchEnableControl id="s-1" enabled />);
    expect(screen.getByRole("button", { name: "Aramayı devre dışı bırak" })).toBeInTheDocument();
  });

  it("names who can grant search:write to a reader, and says when permissions could not be read", () => {
    const { unmount } = render(<SearchWriteNotice unknown={false} />);
    expect(screen.getByRole("status")).toHaveTextContent("Düzenleyici gizli. Saklanan bir aramayı değiştirmek için search:write izni gerekir.");
    expect(screen.getByText("search:write")).toHaveClass("perm");
    expect(screen.getByText("detection_engineer").tagName).toBe("CODE");
    unmount();
    render(<SearchWriteNotice unknown />);
    expect(screen.getByRole("status")).toHaveTextContent("İzinleriniz okunamadı");
  });
});

describe("the schedule in Turkish", () => {
  it("labels a preset and a custom cron", () => {
    const { unmount } = render(<ScheduleLabel cron="0 */4 * * *" />);
    expect(screen.getByText("Her 4 sa")).toBeInTheDocument();
    expect(document.body.textContent).toBe(`Her 4${NB}sa`);
    unmount();
    render(<ScheduleLabel cron="17 3 * * 1" />);
    expect(screen.getByText("17 3 * * 1").tagName).toBe("CODE");
    expect(document.body).toHaveTextContent("Özel 17 3 * * 1");
  });

  it("reverts and names the permission when the change is refused", async () => {
    vi.spyOn(api, "setSearchSchedule").mockRejectedValue(new ApiError(403, "no"));
    render(<ScheduleControl searchId="s-1" cron="0 */4 * * *" />);
    expect(screen.getByRole("combobox", { name: "Şuna değiştir" })).toHaveValue("14400");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Şuna değiştir" }), "3600");
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Rolünüz bu zamanlamayı değiştiremiyor (izin: search:write). Hiçbir şey değiştirilmedi.",
    );
    expect(screen.getByRole("combobox", { name: "Şuna değiştir" })).toHaveValue("14400");
  });

  it("confirms a saved change in Turkish", async () => {
    vi.spyOn(api, "setSearchSchedule").mockResolvedValue({ schedule_cron: "0 * * * *" } as ScheduledSearch);
    render(<ScheduleControl searchId="s-1" cron="0 */4 * * *" />);
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Şuna değiştir" }), "3600");
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Kaydedildi. Sonraki çalıştırma yeni zamanlamayı izler.",
    );
    expect(refresh).toHaveBeenCalled();
  });

  it("offers Custom (keep current) for an unknown cron", () => {
    render(<ScheduleControl searchId="s-1" cron="17 3 * * 1" />);
    expect(screen.getByRole("option", { name: "Özel (mevcut kalsın)" })).toBeInTheDocument();
  });
});

describe("the search editor in Turkish", () => {
  it("words the create form, the latency statement and the number hints", () => {
    render(<SearchEditor mode="create" />);
    expect(screen.getByText(tr.searches.editor.latency)).toBeInTheDocument();
    expect(tr.searches.editor.latency).not.toMatch(/gerçek zamanlı|canlı|anlık/);
    expect(screen.getByRole("textbox", { name: "Ad" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "AQL" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Zamanlama" })).toBeInTheDocument();
    expect(screen.getByText(/Varsayılan:/)).toHaveTextContent("Varsayılan: Her 5 dk");
    expect(screen.getByText(/Varsayılan:/).textContent).toContain(`Her 5${NB}dk`);
    expect(screen.getByText(`1 ile 900 saniye arası`)).toBeInTheDocument();
    expect(screen.getByText("1 ile 168 saat arası")).toBeInTheDocument();
    expect(screen.getByText("1 ile 10.000 satır arası")).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "KRİTİK" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Tek değer" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Devre dışı olarak kaydet" })).toBeInTheDocument();
    expect(screen.queryByText(/Save as disabled|Default:/)).toBeNull();
  });

  it("shows a validation error in Turkish and sends nothing", async () => {
    const create = vi.spyOn(api, "createSearch");
    render(<SearchEditor mode="create" />);
    await userEvent.type(screen.getByRole("textbox", { name: "Ad" }), "Nightly");
    await userEvent.click(screen.getByRole("button", { name: "Devre dışı olarak kaydet" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("AQL zorunludur.");
    expect(create).not.toHaveBeenCalled();
  });

  it("lists the server's reasons as sent under a Turkish label", async () => {
    vi.spyOn(api, "createSearch").mockRejectedValue(
      new ApiError(422, "x", { reasons: ["Only SELECT is allowed."] }),
    );
    render(<SearchEditor mode="create" />);
    await userEvent.type(screen.getByRole("textbox", { name: "Ad" }), "Nightly");
    await userEvent.type(screen.getByRole("textbox", { name: "AQL" }), "DROP");
    await userEvent.click(screen.getByRole("button", { name: "Devre dışı olarak kaydet" }));
    const list = await screen.findByRole("list", { name: "Doğrulama nedenleri" });
    expect(list).toHaveTextContent("Only SELECT is allowed.");
  });

  it("words the edit form, the stored-chart hint and the saved version", async () => {
    vi.spyOn(api, "updateSearch").mockResolvedValue(search({ query_version: 3 }));
    render(<SearchEditor mode="edit" search={search()} />);
    expect(screen.getByText(/Geçerli:/)).toHaveTextContent("Özel");
    expect(screen.getByText(/olarak saklanıyor/)).toHaveTextContent("PIE olarak saklanıyor.");
    expect(screen.getByRole("textbox", { name: "Değişiklik notu" })).toBeInTheDocument();
    expect(screen.getByText(/Özel cron/)).toHaveTextContent("Özel cron 17 3 * * 1 olduğu gibi korunur.");
    await userEvent.click(screen.getByRole("button", { name: "Değişiklikleri kaydet" }));
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Sürüm v3 olarak kaydedildi. Şimdi çalıştır, bu saklanan sürümü çalıştırır.",
    );
  });

  it("says the version stayed when the query text did not change", async () => {
    vi.spyOn(api, "updateSearch").mockResolvedValue(search({ query_version: 2 }));
    render(<SearchEditor mode="edit" search={search()} />);
    await userEvent.click(screen.getByRole("button", { name: "Değişiklikleri kaydet" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Sorgu metni değişmedi");
  });

  it("names the permission when saving is refused", async () => {
    vi.spyOn(api, "updateSearch").mockRejectedValue(new ApiError(403, "x"));
    render(<SearchEditor mode="edit" search={search()} />);
    await userEvent.click(screen.getByRole("button", { name: "Değişiklikleri kaydet" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("(izin: search:write)");
    expect(screen.getByRole("alert")).toHaveTextContent("Hiçbir şey kaydedilmedi.");
  });
});
