// The fleet routes in Turkish: the classes page, the review list, their route states, and the
// components they are built from. Every state has a Turkish assertion; the Turkish number form is
// checked in the class table, the queue counts and the window line.

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setTestLocale } from "../../../vitest.setup";
import { FleetClassTable } from "@/components/fleet/FleetClassTable";
import { FleetSourceFilters } from "@/components/fleet/FleetSourceFilters";
import { FleetSourcesTable } from "@/components/fleet/FleetSourcesTable";
import { WindowSwitch } from "@/components/fleet/WindowSwitch";
import {
  ApiError,
  fleetApi,
  type FleetClassSummary,
  type FleetSource,
  type FleetSummary,
} from "@/lib/api";

import FleetError from "./error";
import Loading from "./loading";
import FleetPage from "./page";
import FleetSourcesPage from "./sources/page";

const NB = "\u00a0";

function klass(over: Partial<FleetClassSummary> = {}): FleetClassSummary {
  return {
    source_class: "FIREWALL",
    sources: { enabled: 1204, disabled: 3 },
    buckets: { expected: 288, complete: 270, complete_from_history: 12, partial: 10, missing: 8 },
    events: 1_204_000,
    mean_eps: 1234.5,
    expected_eps: 1500,
    baseline_sources: 2,
    baseline_share: 0.5,
    monitored_sources: 4,
    open_incidents: { NO_EVENTS: 2 },
    top_sources: [{ id: "t1", name: "fw-edge-01", events: 52_300 }],
    ...over,
  };
}

function summary(over: Partial<FleetSummary> = {}): FleetSummary {
  return {
    window: "24h",
    window_start: "2026-10-12T08:00:00Z",
    window_end: "2026-10-13T08:00:00Z",
    bucket_seconds: 300,
    classes: [klass()],
    internal: { sources: { enabled: 4, disabled: 1 } },
    new_sources: 1204,
    conflict_sources: 1,
    unclassified_sources: 0,
    ...over,
  };
}

function source(over: Partial<FleetSource> = {}): FleetSource {
  return {
    id: "s1",
    instance_id: "i1",
    qradar_id: 7,
    name: "dmz-ips-05",
    type_name: "Linux OS",
    enabled: false,
    auto_discovered: true,
    first_seen_at: "2026-10-13T02:40:00Z",
    source_class: "IPS",
    basis: "NAME_REGEX",
    rule_id: null,
    override_class: "IPS",
    override_by: "s.okafor",
    override_at: null,
    conflicts: [{ basis: "TYPE_NAME_REGEX", rule_id: "r2", class: "LINUX" }],
    classified_at: null,
    review_status: "NEW",
    reviewed_at: null,
    reviewed_by: null,
    ...over,
  };
}

async function renderFleet(params: Record<string, string> = {}) {
  return render(await FleetPage({ searchParams: Promise.resolve(params) }));
}
async function renderSources(params: Record<string, string> = {}) {
  return render(await FleetSourcesPage({ searchParams: Promise.resolve(params) }));
}

beforeEach(() => {
  vi.restoreAllMocks();
  setTestLocale("tr");
  vi.spyOn(fleetApi, "rules").mockResolvedValue([]);
});

describe("/fleet in Turkish", () => {
  it("titles the page, names the window and the queue, and writes numbers the Turkish way", async () => {
    vi.spyOn(fleetApi, "summary").mockResolvedValue(summary());
    await renderFleet({ window: "7d" });
    expect(screen.getByRole("heading", { level: 1, name: "Log source filosu" })).toBeInTheDocument();
    expect(screen.getByText(/Son 7.gün:/).textContent).toBe(
      `Son 7${NB}gün: 2026-10-12 08:00:00 UTC ile 2026-10-13 08:00:00 UTC arası, 5${NB}dakikalık zaman dilimlerinde.`,
    );
    expect(screen.getByRole("navigation", { name: "Filo penceresi" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /^7.gün$/ })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "1.204 yeni kaynak" })).toHaveAttribute(
      "href",
      "/fleet/sources?review_status=NEW",
    );
    expect(screen.getByRole("link", { name: "Sınıfı çakışan 1 kaynak" })).toBeInTheDocument();
    // A zero is text, not a link to an empty list.
    expect(screen.getByText("0 sınıflanmamış kaynak").closest("a")).toBeNull();
    expect(screen.getByText("İnceleme kuyruğu:")).toBeInTheDocument();
    expect(screen.getByText("4 etkin, 1 devre dışı. Yukarıdaki sınıflardan ayrı sayılırlar ve burada hacim değeri taşımazlar.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "QRadar dahili kaynakları" })).toBeInTheDocument();
    expect(screen.getByText(/Beklenen EPS, baseline'ı olan kaynaklar/)).toBeInTheDocument();
  });

  it("says what is empty and why when no class is returned", async () => {
    vi.spyOn(fleetApi, "summary").mockResolvedValue(summary({ classes: [] }));
    await renderFleet();
    expect(screen.getByText("Hiçbir sınıf döndürülmedi.")).toBeInTheDocument();
    expect(screen.getByText(/Log source'lar sayfasından bir envanter eşitlemesi/)).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("names the permission on 403, asks for a new sign-in on 401 and says what to do when the API is down", async () => {
    vi.spyOn(fleetApi, "summary").mockRejectedValueOnce(new ApiError(403, "no"));
    await renderFleet();
    expect(screen.getByRole("alert")).toHaveTextContent("Filo özeti gösterilemedi.");
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Rolünüz filo özetini okuyamıyor (izin: read:fleet). Hiçbir şey değiştirilmedi.",
    );
  });

  it("asks for a new sign-in on 401", async () => {
    vi.spyOn(fleetApi, "summary").mockRejectedValueOnce(new ApiError(401, "no"));
    await renderFleet();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Oturumunuzun süresi doldu. Devam etmek için yeniden giriş yapın.",
    );
  });

  it("says what failed and what to do when the API is down", async () => {
    vi.spyOn(fleetApi, "summary").mockRejectedValueOnce(new Error("down"));
    await renderFleet();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Filo özeti yüklenemedi. Backend'e erişilemiyor olabilir; çalıştığını denetleyin, sonra sayfayı yeniden yükleyin.",
    );
  });

  it("announces loading and the route error without the exception", () => {
    const { unmount } = render(<Loading />);
    expect(screen.getByRole("status")).toHaveTextContent("Log source filosu yükleniyor…");
    expect(screen.getByRole("link", { name: /^24.sa$/ })).toHaveAttribute("aria-current", "page");
    unmount();
    render(<FleetError error={Object.assign(new Error("secret stack"), { digest: "abc" })} reset={() => {}} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Log source filosu gösterilemedi.");
    expect(screen.getByRole("alert")).toHaveTextContent("Referans: abc.");
    expect(screen.getByRole("button", { name: "Tekrar dene" })).toBeInTheDocument();
    expect(screen.queryByText(/secret stack/)).toBeNull();
  });
});

describe("the class table in Turkish", () => {
  it("writes figures with their unit in the Turkish form and names every column", () => {
    render(<FleetClassTable classes={[klass()]} windowName={`24${NB}saat`} />);
    const table = screen.getByRole("table");
    expect(within(table).getByText(/son 24.saat içindeki hacim/, { selector: "caption" })).toBeInTheDocument();
    for (const name of ["Sınıf", "Olaylar", "Ortalama EPS", "Beklenen EPS", "Pencere bütünlüğü", "Açık vakalar"]) {
      expect(within(table).getByRole("columnheader", { name })).toBeInTheDocument();
    }
    const row = screen.getAllByRole("row")[1];
    expect(within(row).getByRole("link", { name: "Güvenlik duvarı" })).toHaveAttribute(
      "href",
      "/fleet/sources?class=FIREWALL",
    );
    expect(row).toHaveTextContent("1.204 etkin, 3 devre dışı");
    expect(row).toHaveTextContent("1.204.000");
    expect(row.textContent).toContain(`1.234,50${NB}EPS`);
    expect(row.textContent).toContain(`1.500,00${NB}EPS`);
    expect(row.textContent).toContain(`Baseline'ı olan kaynaklar: 2${NB}/${NB}4.`);
    expect(row).toHaveTextContent("Sınıfın yalnızca bir bölümünü kapsar.");
    expect(row).toHaveTextContent("yalnızca tamamlanmış zaman dilimleri");
    expect(row).toHaveTextContent("Eksik");
    expect(row.textContent).toContain(`270${NB}/${NB}288 zaman dilimi tamamlandı`);
    expect(row).toHaveTextContent("10 kısmi, 8 eksik");
    expect(row.textContent).toContain(
      `Tamamlanmış 12${NB}/${NB}270 zaman dilimi QRadar geçmişinden okundu, değerlendirilmedi.`,
    );
    expect(row).toHaveTextContent("Olay yok 2");
    expect(screen.getByText("Olay sayısına göre en yoğun kaynaklar")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "fw-edge-01" })).toBeInTheDocument();
    expect(screen.getByText("52.300")).toBeInTheDocument();
  });

  it("says 'not measured' with its reason, never zero, for a class with no complete bucket", () => {
    const empty = klass({
      source_class: "VPN",
      sources: { enabled: 0, disabled: 0 },
      buckets: { expected: 288, complete: 0, complete_from_history: 0, partial: 0, missing: 288 },
      events: null,
      mean_eps: null,
      expected_eps: null,
      open_incidents: {},
      top_sources: [],
    });
    render(<FleetClassTable classes={[empty]} windowName="1 saat" />);
    const row = screen.getAllByRole("row")[1];
    expect(row).toHaveTextContent("Kaynak yok");
    expect(row).toHaveTextContent("Ölçülmedi");
    expect(row).toHaveTextContent("Pencerede tamamlanmış zaman dilimi yok.");
    expect(row).toHaveTextContent("Henüz hiçbir kaynağın baseline'ı yok.");
    expect(within(row).getByLabelText("Beklenti yok")).toBeInTheDocument();
    expect(row).toHaveTextContent("Açık yok");
    expect(row).not.toHaveTextContent(/\b0,00/);
  });

  it("says a class with no monitored source has nothing to judge", () => {
    const none = klass({ buckets: { expected: 0, complete: 0, complete_from_history: 0, partial: 0, missing: 0 } });
    render(<FleetClassTable classes={[none]} windowName="1 saat" />);
    expect(screen.getAllByRole("row")[1]).toHaveTextContent("İzlenen kaynak yok");
  });
});

describe("the window switch and the filters in Turkish", () => {
  it("labels the switch and each window with its unit", () => {
    render(<WindowSwitch active="1h" />);
    const nav = screen.getByRole("navigation", { name: "Filo penceresi" });
    expect(within(nav).getByText("Pencere")).toBeInTheDocument();
    expect(within(nav).getAllByRole("link").map((a) => a.textContent)).toEqual([
      `1${NB}sa`,
      `24${NB}sa`,
      `7${NB}gün`,
    ]);
  });

  it("labels every control and option, with the class and basis words of the tables", () => {
    render(
      <FleetSourceFilters
        query={{ class: "", review_status: "", has_conflict: "", basis: "", offset: 0 }}
      />,
    );
    for (const name of ["Sınıf", "İnceleme durumu", "Çakışmalar", "Sınıflandırma dayanağı"]) {
      expect(screen.getByRole("combobox", { name })).toBeInTheDocument();
    }
    expect(screen.getByRole("option", { name: "Tüm sınıflar" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Güvenlik duvarı" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Tüm inceleme durumları" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "İncelendi" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Çakışmalı veya çakışmasız" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Yalnızca sınıfı çakışanlar" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Tüm dayanaklar" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Grup kuralı" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Filtrele" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Temizle" })).toBeNull();
  });

  it("offers Clear once a filter is set", () => {
    render(
      <FleetSourceFilters
        query={{ class: "WAF", review_status: "", has_conflict: "", basis: "", offset: 0 }}
      />,
    );
    expect(screen.getByRole("link", { name: "Temizle" })).toHaveAttribute("href", "/fleet/sources");
  });
});

describe("/fleet/sources in Turkish", () => {
  it("titles the page, shows the active filters as chips and the Turkish table", async () => {
    vi.spyOn(fleetApi, "sources").mockResolvedValue({ items: [source()], total: 1, limit: 50, offset: 0 });
    await renderSources({ class: "IPS", review_status: "NEW", has_conflict: "true", basis: "NAME_REGEX" });
    expect(screen.getByRole("heading", { level: 1, name: "Filo kaynakları" })).toBeInTheDocument();
    const chips = screen.getByRole("list", { name: "Etkin filtreler" });
    expect(within(chips).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "Sınıf: IPS",
      "İnceleme: Yeni",
      "Yalnızca çakışanlar",
      "Dayanak: Ad kuralı",
    ]);
    const row = screen.getAllByRole("row")[1];
    expect(row).toHaveTextContent("Linux OS · devre dışı");
    expect(row).toHaveTextContent("Ad kuralı · atayan: s.okafor");
    expect(row).toHaveTextContent("Çakışma: ad: IPS, tür: Linux");
    expect(row).toHaveTextContent("Otomatik keşfedildi");
    expect(row).toHaveTextContent("10-13 02:40 UTC");
  });

  it("says what is empty and why, for a filter and for an empty inventory", async () => {
    vi.spyOn(fleetApi, "sources").mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 });
    const { unmount } = await renderSources({ class: "WAF" });
    expect(screen.getByText("Bu filtrelere uyan kaynak yok.")).toBeInTheDocument();
    expect(screen.getByText("Daha fazla kaynak görmek için filtreleri genişletin veya temizleyin.")).toBeInTheDocument();
    unmount();
    await renderSources();
    expect(screen.getByText("Envanterde log source yok.")).toBeInTheDocument();
    expect(screen.getByText(/Log source'lar sayfasından bir envanter eşitlemesi çalıştırın/)).toBeInTheDocument();
  });

  it("names the permission on 403 and says what to do when the list cannot be loaded", async () => {
    vi.spyOn(fleetApi, "sources").mockRejectedValueOnce(new ApiError(403, "no"));
    const { unmount } = await renderSources();
    expect(screen.getByRole("alert")).toHaveTextContent("Kaynak listesi gösterilemedi.");
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Rolünüz filoyu okuyamıyor (izin: read:fleet). Hiçbir şey değiştirilmedi.",
    );
    unmount();
    vi.spyOn(fleetApi, "sources").mockRejectedValueOnce(new Error("down"));
    await renderSources();
    expect(screen.getByRole("alert")).toHaveTextContent("Kaynak listesi yüklenemedi. Backend'e erişilemiyor olabilir");
  });
});

describe("the review table in Turkish", () => {
  it("labels every column and action, and each action carries the source name", () => {
    render(<FleetSourcesTable items={[source()]} />);
    for (const name of ["Kaynak", "Sınıf ve dayanak", "İlk görülme", "İnceleme", "İşlemler"]) {
      expect(screen.getByRole("columnheader", { name })).toBeInTheDocument();
    }
    expect(screen.getByRole("button", { name: "dmz-ips-05 için sınıf ata" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "dmz-ips-05 için atamayı kaldır" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "dmz-ips-05 kaynağını incelendi olarak işaretle" })).toBeInTheDocument();
  });

  it("words the outcome of an override, a cleared override and a review", async () => {
    vi.spyOn(fleetApi, "setOverride").mockResolvedValueOnce(source({ source_class: "LINUX", override_class: "LINUX" }));
    vi.spyOn(fleetApi, "setOverride").mockResolvedValueOnce(source({ source_class: "IPS", override_class: null, basis: "NAME_REGEX" }));
    vi.spyOn(fleetApi, "markReviewed").mockResolvedValue(source({ review_status: "REVIEWED" }));
    render(<FleetSourcesTable items={[source()]} />);
    await userEvent.click(screen.getByRole("button", { name: "dmz-ips-05 için sınıf ata" }));
    expect(screen.getByText("dmz-ips-05 için sınıf")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Uygula" }));
    await waitFor(() => expect(screen.getByText("Kaydedildi. Sınıf artık Linux.")).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: "dmz-ips-05 için atamayı kaldır" }));
    await waitFor(() =>
      expect(screen.getByText("Atama kaldırıldı. Sınıf artık IPS (ad kuralı).")).toBeInTheDocument(),
    );
    await userEvent.click(screen.getByRole("button", { name: "dmz-ips-05 kaynağını incelendi olarak işaretle" }));
    await waitFor(() => expect(screen.getByText("İncelendi olarak işaretlendi.")).toBeInTheDocument());
  });

  it("names the permission when a role cannot change a source, and says nothing changed", async () => {
    vi.spyOn(fleetApi, "markReviewed").mockRejectedValue(new ApiError(403, "no"));
    render(<FleetSourcesTable items={[source({ override_class: null })]} />);
    await userEvent.click(screen.getByRole("button", { name: "dmz-ips-05 kaynağını incelendi olarak işaretle" }));
    expect(await screen.findByText(/izin: log_source:write/)).toHaveTextContent("Hiçbir şey değiştirilmedi.");
  });

  it("cancels an edit without a request", async () => {
    const spy = vi.spyOn(fleetApi, "setOverride");
    render(<FleetSourcesTable items={[source({ override_class: null })]} />);
    await userEvent.click(screen.getByRole("button", { name: "dmz-ips-05 için sınıf ata" }));
    await userEvent.click(screen.getByRole("button", { name: "İptal" }));
    expect(spy).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "dmz-ips-05 için sınıf ata" })).toBeInTheDocument();
  });
});
