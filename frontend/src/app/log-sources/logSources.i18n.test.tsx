// The log source inventory and its detail page in Turkish: every state, the QRadar status words
// (a status the catalog does not know stays as QRadar sent it) and the Turkish number form.

import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setTestLocale } from "../../../vitest.setup";
import { createTextTranslator } from "@/i18n/textTranslator";
import { ApiError, api, type LogSourceDetail, type LogSourceSummary } from "@/lib/api";

import tr from "../../../messages/tr";
import DetailError from "./[id]/error";
import DetailLoading from "./[id]/loading";
import LogSourceDetailPage from "./[id]/page";
import LogSourcesError from "./error";
import LogSourcesLoading from "./loading";
import LogSourcesPage from "./page";
import { qradarStatusLabel, qradarStatusTone } from "./qradarStatus";

const turkish = createTextTranslator("tr", tr);
const NB = " ";

function source(over: Partial<LogSourceSummary> = {}): LogSourceSummary {
  return {
    id: "s-1",
    qradar_id: 227,
    name: "Core Firewall",
    type_name: "Check Point Firewall",
    criticality: "HIGH",
    owner: "SOC",
    enabled: true,
    monitoring_enabled: true,
    maintenance_mode: false,
    qradar_status: "SUCCESS",
    last_event_time: "2026-10-15T08:59:00Z",
    open_anomaly_count: 0,
    ...over,
  };
}

function detail(over: Partial<LogSourceDetail> = {}): LogSourceDetail {
  return {
    id: "s-1",
    qradar_id: 227,
    enabled: true,
    monitoring_enabled: true,
    maintenance_mode: false,
    timezone_name: "UTC",
    name: "Core Firewall",
    type_name: "Check Point Firewall",
    description: "Perimeter firewall",
    criticality: "HIGH",
    owner: "SOC",
    owner_email: "soc@example.com",
    qradar_status: "SUCCESS",
    business_hours_only: false,
    expected_interval_seconds: 3600,
    last_event_time: "2026-10-15T08:59:00Z",
    ...over,
  };
}

beforeEach(() => {
  vi.restoreAllMocks();
  setTestLocale("tr");
});

describe("/log-sources in Turkish", () => {
  it("counts the sources, labels the columns and words every status", async () => {
    const many = Array.from({ length: 1204 }, (_, i) => source({ id: `s-${i}`, name: `src-${i}` }));
    many[0] = source({ id: "w", name: "warn-src", qradar_status: "WARNING", maintenance_mode: true, open_anomaly_count: 1204 });
    many[1] = source({ id: "e", name: "err-src", qradar_status: "ERROR", last_event_time: null });
    many[2] = source({ id: "d", name: "off-src", qradar_status: "DISABLED", type_name: null });
    many[3] = source({ id: "n", name: "none-src", qradar_status: null });
    many[4] = source({ id: "x", name: "odd-src", qradar_status: "QUARANTINED" });
    vi.spyOn(api, "logSources").mockResolvedValue(many);
    render(await LogSourcesPage());
    expect(screen.getByRole("heading", { level: 1, name: "Log source'lar" })).toBeInTheDocument();
    expect(screen.getByText(/^1\.204 kaynak\. QRadar'a ait alanlar eşitlemede yenilenir/)).toBeInTheDocument();
    const table = screen.getByRole("table", { name: "Log source'lar" });
    for (const name of ["Ad", "Tür", "Kritiklik", "Durum", "Anomaliler", "Son olay"]) {
      expect(within(table).getByRole("columnheader", { name })).toBeInTheDocument();
    }
    const row = (name: string) => within(table).getByText(name).closest("tr")!;
    expect(row("warn-src")).toHaveTextContent("UYARI");
    expect(row("warn-src")).toHaveTextContent("bakım");
    expect(row("warn-src")).toHaveTextContent("1.204");
    expect(within(row("warn-src")).getByText("UYARI")).toHaveClass("warn");
    expect(within(row("err-src")).getByText("HATA")).toHaveClass("crit");
    expect(row("err-src")).toHaveTextContent("hiç");
    expect(within(row("off-src")).getByText("DEVRE DIŞI")).not.toHaveClass("ok", "warn", "crit");
    expect(row("off-src")).toHaveTextContent("—");
    expect(row("none-src")).toHaveTextContent("bilinmiyor");
    expect(row("odd-src")).toHaveTextContent("QUARANTINED");
    expect(within(table).getAllByText("BAŞARILI")[0]).toHaveClass("ok");
  });

  it("says what is empty, and gives no count when the read failed", async () => {
    vi.spyOn(api, "logSources").mockResolvedValueOnce([]);
    const a = render(await LogSourcesPage());
    expect(screen.getByText("Henüz log source yok. Yapılandırılmış sağlayıcıdan almak için bir envanter eşitlemesi başlatın.")).toBeInTheDocument();
    a.unmount();
    vi.spyOn(api, "logSources").mockRejectedValueOnce(new ApiError(500, "x"));
    render(await LogSourcesPage());
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.getByText(/^QRadar'a ait alanlar eşitlemede yenilenir/)).toBeInTheDocument();
    expect(screen.queryByText(/kaynak\. QRadar/)).toBeNull();
  });

  it("announces loading and the route error without the exception", () => {
    const a = render(<LogSourcesLoading />);
    expect(screen.getByRole("status")).toHaveTextContent("Log source'lar yükleniyor…");
    a.unmount();
    render(<LogSourcesError error={Object.assign(new Error("secret stack"), { digest: "ref-58" })} reset={() => {}} />);
    expect(screen.getByRole("heading", { level: 1, name: "Log source'lar" })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Log source'lar gösterilemedi.");
    expect(screen.getByRole("alert")).toHaveTextContent("Referans: ref-58.");
    expect(screen.getByRole("alert")).not.toHaveTextContent("secret stack");
  });
});

describe("a log source's detail in Turkish", () => {
  async function renderDetail(id = "s-1") {
    return render(await LogSourceDetailPage({ params: Promise.resolve({ id }) }));
  }

  it("words the header line and the configuration card", async () => {
    vi.spyOn(api, "logSource").mockResolvedValue(detail());
    await renderDetail();
    expect(screen.getByRole("heading", { level: 1, name: "Core Firewall" })).toBeInTheDocument();
    expect(screen.getByText("Check Point Firewall · kritiklik HIGH · sahip SOC")).toBeInTheDocument();
    expect(screen.getByText("Yapılandırma")).toBeInTheDocument();
    expect(screen.getByText("Açıklama: Perimeter firewall")).toBeInTheDocument();
    expect(screen.getByText("Yalnızca mesai saatleri: hayır")).toBeInTheDocument();
    expect(screen.getByText(/^Beklenen aralık: 3\.600.sn$/).textContent).toBe("Beklenen aralık: 3.600" + NB + "sn");
    expect(document.body).toHaveTextContent("Son olay: 2026-10-15 08:59:00 UTC");
    expect(document.body).toHaveTextContent("QRadar durumu: BAŞARILI");
    expect(screen.getByRole("link", { name: "kaynak davranışı" })).toHaveAttribute("href", "/behavior/sources/s-1");
    expect(document.body).toHaveTextContent("EPS eğilimi, anomali zaman çizelgesi ve baseline katmanı: kaynak davranışı.");
  });

  it("writes a missing figure as a dash or a word, never a made-up value", async () => {
    vi.spyOn(api, "logSource").mockResolvedValue(
      detail({ type_name: null, owner: null, description: null, expected_interval_seconds: null, last_event_time: null, qradar_status: null, business_hours_only: true }),
    );
    await renderDetail();
    expect(screen.getByText("bilinmeyen tür · kritiklik HIGH · sahip —")).toBeInTheDocument();
    expect(screen.getByText("Açıklama: —")).toBeInTheDocument();
    expect(screen.getByText("Yalnızca mesai saatleri: evet")).toBeInTheDocument();
    expect(screen.getByText("Beklenen aralık: —")).toBeInTheDocument();
    expect(screen.getByText("Son olay: hiç")).toBeInTheDocument();
    expect(screen.getByText("bilinmiyor")).toBeInTheDocument();
  });

  it.each([
    [401, "Oturumunuzun süresi doldu. Devam etmek için yeniden giriş yapın."],
    [403, "Bu log source'u görüntüleme izniniz yok. Bir yöneticiden read:log_sources iznini isteyin."],
    [404, "Envanterde s-9 kimlikli log source yok."],
    [500, "Backend'e erişilemedi. Tekrar denemek için sayfayı yeniden yükleyin."],
  ])("says what failed for a %s read", async (status, sentence) => {
    vi.spyOn(api, "logSource").mockRejectedValue(new ApiError(status, "x"));
    await renderDetail("s-9");
    expect(screen.getByRole("heading", { level: 1, name: "Log source ayrıntısı" })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(sentence);
  });

  it("announces loading and the route error", () => {
    const a = render(<DetailLoading />);
    expect(screen.getByRole("status")).toHaveTextContent("Bu log source yükleniyor…");
    a.unmount();
    render(<DetailError error={Object.assign(new Error("x"), { digest: "d2" })} reset={() => {}} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Bu log source gösterilemedi.");
  });
});

describe("the QRadar status words", () => {
  it("keeps the English as QRadar sent it, and has a Turkish word for each known status", () => {
    for (const s of ["SUCCESS", "WARNING", "ERROR", "DISABLED", "NA", "OTHER", null]) {
      expect(qradarStatusLabel(s)).toBe(s ?? "unknown");
    }
    for (const s of ["SUCCESS", "WARNING", "ERROR", "DISABLED", "NA"]) {
      expect(qradarStatusLabel(s, turkish)).not.toBe(s);
    }
    expect(qradarStatusLabel(null, turkish)).toBe("bilinmiyor");
  });

  it("shows a status the catalog does not know as sent, never empty, and does not read it as a path", () => {
    expect(qradarStatusLabel("QUARANTINED", turkish)).toBe("QUARANTINED");
    expect(qradarStatusLabel("a.b", turkish)).toBe("a.b");
    expect(qradarStatusLabel("unknown", turkish)).toBe("unknown");
    expect(qradarStatusLabel("", turkish)).toBe("");
  });

  it("keeps the tone out of the language", () => {
    expect([qradarStatusTone("SUCCESS"), qradarStatusTone("WARNING"), qradarStatusTone("ERROR"), qradarStatusTone("NA")]).toEqual([
      "ok",
      "warn",
      "crit",
      "",
    ]);
  });
});
