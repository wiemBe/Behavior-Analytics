import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setTestLocale } from "../../../vitest.setup";
import { ApiError, api, type ScheduledSearch, type SearchExecution, type SearchVersion } from "@/lib/api";

import tr from "../../../messages/tr";
import SearchesError from "./error";
import Loading from "./loading";
import SearchCatalogPage, { generateMetadata as listMetadata } from "./page";
import SearchDetailError from "./[id]/error";
import SearchDetailLoading from "./[id]/loading";
import SearchDetailPage, { generateMetadata as detailMetadata } from "./[id]/page";
import NewSearchError from "./new/error";
import NewSearchLoading from "./new/loading";
import NewSearchPage, { generateMetadata as newMetadata } from "./new/page";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }) }));
vi.mock("echarts/core", () => ({ init: () => ({ setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn(), on: vi.fn(), off: vi.fn(), getZr: () => ({ on: vi.fn() }) }), use: vi.fn() }));
vi.mock("echarts/charts", () => ({ LineChart: {}, BarChart: {} }));
vi.mock("echarts/components", () => ({
  GridComponent: {},
  MarkLineComponent: {},
  MarkPointComponent: {},
  TooltipComponent: {},
}));
vi.mock("echarts/renderers", () => ({ CanvasRenderer: {} }));

const NB = " ";

function search(over: Partial<ScheduledSearch> = {}): ScheduledSearch {
  return {
    id: "s-1",
    name: "Repeated authentication failures",
    description: "Counts failures per source",
    owner: "SOC",
    category: "Credential Access",
    mitre_techniques: ["T1110"],
    aql_query: "SELECT count(*) FROM events WHERE username IS NOT NULL\nGROUP BY username",
    query_version: 2,
    schedule_cron: "0 */4 * * *",
    schedule_timezone: "UTC",
    severity: "HIGH",
    threshold_value: 10,
    threshold_operator: "GT",
    timeout_seconds: 300,
    max_time_range_hours: 24,
    max_result_rows: 500,
    visualization_type: "table",
    enabled: true,
    last_run_at: "2026-10-15T04:00:00Z",
    next_run_at: "2026-10-15T08:00:00Z",
    consecutive_failures: 0,
    ...over,
  };
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
    retry_count: 0,
    threshold_breached: false,
    ...over,
  };
}

const versions: SearchVersion[] = [
  { version: 2, aql_query: "SELECT count(*) FROM events", changed_by: "ada", change_note: "Added a filter", created_at: "2026-10-15T04:00:00Z" },
  { version: 1, aql_query: "SELECT count(*) FROM events WHERE username IS NOT NULL", changed_by: null, change_note: null, created_at: "2026-10-14T04:00:00Z" },
];

const reader = {
  subject: "analyst",
  email: null,
  permissions: ["read:*", "search:execute"],
  effective_permissions: ["read:searches", "search:execute"],
};
const writer = {
  subject: "ada",
  email: null,
  permissions: ["search:write"],
  effective_permissions: ["read:searches", "search:write", "search:execute"],
};

beforeEach(() => {
  vi.restoreAllMocks();
  setTestLocale("tr");
  vi.spyOn(api, "caller").mockResolvedValue(reader);
  vi.spyOn(api, "searchResults").mockResolvedValue({
    search_id: "s-1", metric_key: "total", threshold_value: null, threshold_operator: "GT", count: 0, points: [],
  });
  vi.spyOn(api, "searchMetricKeys").mockResolvedValue({ search_id: "s-1", total: 0, count: 0, keys: [] });
  vi.spyOn(api, "searchLatestGroups").mockResolvedValue({
    search_id: "s-1", execution_id: null, started_at: null, status: null, truncated: false,
    values_are_lower_bounds: false, empty: "no_execution", total: 0, rows: [],
  });
});

describe("the search catalog in Turkish", () => {
  it("titles the page and words the table, the schedule, the severity and the enabled flag", async () => {
    vi.spyOn(api, "searches").mockResolvedValue([
      search(),
      search({ id: "s-2", name: "Custom", schedule_cron: "17 3 * * 1", enabled: false, category: null, mitre_techniques: [], severity: "CRITICAL" }),
    ]);
    vi.spyOn(api, "caller").mockResolvedValue(writer);
    render(await SearchCatalogPage());
    expect(await listMetadata()).toEqual({ title: "Arama kataloğu" });
    expect(screen.getByRole("heading", { level: 1, name: "Arama kataloğu" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Yeni arama" })).toHaveAttribute("href", "/searches/new");
    const table = screen.getByRole("table", { name: "Zamanlanmış aramalar" });
    for (const name of ["Ad", "Kategori", "MITRE", "Zamanlama", "Önem derecesi", "Sürüm", "Etkin"]) {
      expect(within(table).getByRole("columnheader", { name })).toBeInTheDocument();
    }
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows[0].textContent).toContain(`Her 4${NB}sa`);
    expect(within(rows[0]).getByText("YÜKSEK")).toHaveClass("pill", "crit");
    expect(within(rows[0]).getByText("evet")).toBeInTheDocument();
    expect(within(rows[0]).getByText("v2")).toBeInTheDocument();
    expect(within(rows[1]).getByText("hayır")).toHaveClass("pill", "warn");
    expect(within(rows[1]).getByText("KRİTİK")).toBeInTheDocument();
    expect(within(rows[1]).getByText("17 3 * * 1").tagName).toBe("CODE");
    expect(within(rows[1]).getAllByText("—")).toHaveLength(2);
    expect(table).not.toHaveTextContent(/Name|Severity|yes|Every/);
  });

  it("says what is empty and what to do, for a writer and for a reader", async () => {
    vi.spyOn(api, "searches").mockResolvedValue([]);
    vi.spyOn(api, "caller").mockResolvedValue(writer);
    const first = render(await SearchCatalogPage());
    expect(screen.getByText("Henüz tanımlanmış zamanlanmış arama yok.")).toBeInTheDocument();
    expect(screen.getByText(/“Yeni arama” seçeneğini kullanın/)).toBeInTheDocument();
    first.unmount();
    vi.spyOn(api, "caller").mockResolvedValue(reader);
    render(await SearchCatalogPage());
    expect(screen.queryByRole("link", { name: "Yeni arama" })).toBeNull();
    expect(screen.getByText("search:write").tagName).toBe("CODE");
    expect(screen.getByText("detection_engineer").tagName).toBe("CODE");
    expect(document.body).toHaveTextContent("Arama oluşturmak için search:write izni gerekir.");
  });

  it("words a refused and a failed read", async () => {
    vi.spyOn(api, "searches").mockRejectedValue(new ApiError(403, "no"));
    const first = render(await SearchCatalogPage());
    expect(screen.getByRole("alert")).toHaveTextContent(
      "İzin yok: aramalar görüntülenemez. read:searches izni için bir yöneticiye başvurun.",
    );
    first.unmount();
    vi.spyOn(api, "searches").mockRejectedValue(new Error("down"));
    render(await SearchCatalogPage());
    expect(screen.getByRole("alert")).toHaveTextContent("Yüklenemedi: Aramalar.");
  });

  it("keeps the route error and loading boundaries in Turkish", async () => {
    const reset = vi.fn();
    const first = render(<SearchesError error={Object.assign(new Error("secret"), { digest: "d-7" })} reset={reset} />);
    expect(screen.getByRole("heading", { level: 1, name: "Arama kataloğu" })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Zamanlanmış aramalar gösterilemedi.");
    expect(screen.getByRole("alert")).not.toHaveTextContent("secret");
    await userEvent.click(screen.getByRole("button", { name: "Tekrar dene" }));
    expect(reset).toHaveBeenCalledTimes(1);
    first.unmount();
    render(<Loading />);
    expect(screen.getByRole("status")).toHaveTextContent("Zamanlanmış aramalar yükleniyor…");
  });
});

describe("the search detail in Turkish", () => {
  async function renderDetail() {
    return render(await SearchDetailPage({ params: Promise.resolve({ id: "s-1" }) }));
  }

  it("words the header, the sections, the query panels, the executions and the versions", async () => {
    vi.spyOn(api, "search").mockResolvedValue(search({ category: null }));
    vi.spyOn(api, "searchVersions").mockResolvedValue(versions);
    vi.spyOn(api, "searchExecutions").mockResolvedValue([
      execution(),
      execution({ id: "e-2", status: "FAILED", error_type: "NETWORK", result_count: null, trigger: "MANUAL" }),
    ]);
    await renderDetail();
    expect(await detailMetadata()).toEqual({ title: "Arama" });
    const header = screen.getByRole("heading", { level: 1, name: "Repeated authentication failures" }).closest("header") ?? document.body;
    expect(header).toHaveTextContent("Etkin");
    expect(document.body).toHaveTextContent("kategorisiz · önem derecesi YÜKSEK · v2 · sonraki çalıştırma");
    expect(document.body).toHaveTextContent("2026-10-15 08:00:00 UTC");
    expect(screen.getByRole("button", { name: "Şimdi çalıştır" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "Zamanlama" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "Geçerli AQL (v2)" })).toBeInTheDocument();
    expect(screen.getByText("salt okunur")).toBeInTheDocument();
    expect(screen.getByText("Sorgu değişikliği: v1 → v2")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "Çalıştırma geçmişi" })).toBeInTheDocument();
    expect(screen.getByText("TAMAMLANDI")).toBeInTheDocument();
    expect(screen.getByText("BAŞARISIZ")).toBeInTheDocument();
    expect(screen.getByText("1.204")).toBeInTheDocument();
    const versionTable = screen.getByRole("table", { name: "Sorgu sürüm geçmişi" });
    expect(within(versionTable).getByRole("columnheader", { name: "Değiştiren" })).toBeInTheDocument();
    expect(within(versionTable).getByRole("columnheader", { name: "Zaman (UTC)" })).toBeInTheDocument();
    expect(within(versionTable).getByText("Added a filter")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "Sonuç eğilimi" })).toBeInTheDocument();
    // The trend takes its words from the interface language through the client wrapper.
    await waitFor(() => expect(document.body).toHaveTextContent("QRadar bağlantısı başarısız oldu."));
    expect(document.body).not.toHaveTextContent(/Execution history|Query version|Result trend|No measured points/);
  });

  it("says there are no executions yet", async () => {
    vi.spyOn(api, "search").mockResolvedValue(search());
    vi.spyOn(api, "searchVersions").mockResolvedValue(versions);
    vi.spyOn(api, "searchExecutions").mockResolvedValue([]);
    await renderDetail();
    expect(
      screen.getByText("Henüz çalıştırma yok. İlki, bu arama zamanlamasına göre çalıştığında veya şimdi çalıştırıldığında görünür."),
    ).toBeInTheDocument();
  });

  it("shows the editor to a writer and the notice to a reader, both in Turkish", async () => {
    vi.spyOn(api, "search").mockResolvedValue(search({ enabled: false }));
    vi.spyOn(api, "searchVersions").mockResolvedValue(versions);
    vi.spyOn(api, "searchExecutions").mockResolvedValue([]);
    vi.spyOn(api, "caller").mockResolvedValue(writer);
    const first = await renderDetail();
    expect(screen.getByText("Devre dışı", { selector: ".pill" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "Tanım" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Aramayı etkinleştir" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Değişiklikleri kaydet" })).toBeInTheDocument();
    first.unmount();
    vi.spyOn(api, "caller").mockResolvedValue(reader);
    await renderDetail();
    expect(document.body).toHaveTextContent("Düzenleyici gizli.");
  });

  it("says the search could not be loaded, with the id, and what to do", async () => {
    vi.spyOn(api, "search").mockRejectedValue(new ApiError(500, "secret"));
    vi.spyOn(api, "searchVersions").mockResolvedValue([]);
    vi.spyOn(api, "searchExecutions").mockResolvedValue([]);
    render(await SearchDetailPage({ params: Promise.resolve({ id: "s-9" }) }));
    expect(screen.getByRole("heading", { level: 1, name: "Arama ayrıntısı" })).toBeInTheDocument();
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("s-9 kimlikli arama yüklenemedi.");
    expect(alert).toHaveTextContent("Yeniden denemek için sayfayı yenileyin.");
    expect(alert).not.toHaveTextContent("secret");
  });

  it("keeps the boundaries in Turkish", () => {
    const first = render(<SearchDetailError error={new Error("x")} reset={() => {}} />);
    expect(screen.getByRole("heading", { level: 1, name: "Arama ayrıntısı" })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Bu arama gösterilemedi.");
    first.unmount();
    render(<SearchDetailLoading />);
    expect(screen.getByRole("status")).toHaveTextContent("Bu arama yükleniyor…");
  });
});

describe("the new search page in Turkish", () => {
  it("shows the editor to a writer", async () => {
    vi.spyOn(api, "caller").mockResolvedValue(writer);
    render(await NewSearchPage());
    expect(await newMetadata()).toEqual({ title: "Yeni arama" });
    expect(screen.getByRole("heading", { level: 1, name: "Yeni arama" })).toBeInTheDocument();
    expect(screen.getByText(tr.searches.new.description)).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "AQL" })).toBeInTheDocument();
  });

  it("hides the editor from a reader and from an unreadable caller", async () => {
    const first = render(await NewSearchPage());
    expect(document.querySelector("textarea")).toBeNull();
    expect(screen.getByRole("link", { name: "Aramalara dön" })).toHaveAttribute("href", "/searches");
    expect(document.body).toHaveTextContent("Düzenleyici gizli.");
    first.unmount();
    vi.spyOn(api, "caller").mockRejectedValue(new Error("down"));
    render(await NewSearchPage());
    expect(document.body).toHaveTextContent("İzinleriniz okunamadı");
  });

  it("keeps the boundaries in Turkish", () => {
    const first = render(<NewSearchError error={new Error("x")} reset={() => {}} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Arama düzenleyicisi gösterilemedi.");
    first.unmount();
    render(<NewSearchLoading />);
    expect(screen.getByRole("status")).toHaveTextContent("Arama düzenleyicisi yükleniyor…");
  });
});
