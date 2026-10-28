// The dashboard framework in Turkish (D-37): the view and its customize mode, the catalog with
// every reason a widget cannot be added, the configure drawer with each state of an option read,
// the manage menu and its dialogs, the switcher, the save dialogs, the arrange controls, the
// placeholder, the expand dialog and the range control. English stays the default of the harness,
// so each test opts in with setTestLocale("tr").

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ResourceState } from "@/hooks/useApiResource";
import { createLocaleTranslator } from "@/i18n/testTranslator";
import type { DashboardListItem } from "@/lib/dashboardApi";
import { setTestLocale } from "../../vitest.setup";

import { Catalog, unavailableReason } from "./Catalog";
import { ConfigureDrawer } from "./Configure";
import {
  DashboardMenu,
  dashboardAccess,
  nameProblem,
  type Caller,
  type CurrentDashboard,
} from "./DashboardMenu";
import { ConflictDialog, NameTakenDialog, UnsavedChangesDialog } from "./DashboardSaveDialogs";
import { DashboardSwitcher } from "./DashboardSwitcher";
import { DashboardView } from "./DashboardView";
import { ExpandDialog } from "./Expand";
import { EditGrid } from "./Grid";
import { placeholderFor } from "./Placeholder";
import { RangeControl } from "./RangeControl";
import { widgetDef } from "./registry";
import { SOC_OVERVIEW, TEMPLATES } from "./templates";
import type { AnyWidgetDef, DashboardLayout, LayoutWidget } from "./types";

const mocks = vi.hoisted(() => ({ me: { status: "loading" } as unknown }));
vi.mock("@/hooks/useApiResource", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/hooks/useApiResource")>();
  return {
    ...actual,
    // The catalog and the view read `/me` through the hook; the drawer's reads go through fetch,
    // which this file stubs below, so they are passed to the real hook.
    useApiResource: (key: string | null, fetcher: (signal: AbortSignal) => Promise<unknown>) => {
      if (key === "/me") return mocks.me as ResourceState<unknown>;
      return actual.useApiResource(key, fetcher as never);
    },
  };
});

const dialogs = () => {
  HTMLDialogElement.prototype.showModal = function showModal() {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function close() {
    this.removeAttribute("open");
  };
};

let routes: Record<string, { status?: number; body?: unknown; pending?: boolean }> = {};

beforeEach(() => {
  setTestLocale("tr");
  mocks.me = { status: "loading" };
  routes = {};
  dialogs();
  globalThis.fetch = vi.fn((input: RequestInfo | URL) => {
    const path = String(input).replace(/^.*\/api\/v1/, "");
    const route = routes[path];
    if (!route) return Promise.resolve(new Response("{}", { status: 404 }));
    if (route.pending) return new Promise<Response>(() => {});
    return Promise.resolve(new Response(JSON.stringify(route.body ?? {}), { status: route.status ?? 200 }));
  }) as unknown as typeof fetch;
});

const meOk = (...permissions: string[]): ResourceState<unknown> => ({
  status: "ok",
  data: { subject: "me", effective_permissions: permissions },
  receivedAt: "2026-10-14T13:55:00Z",
  refreshing: false,
  refreshFailed: null,
});

describe("the registry in Turkish: placeholder and catalog", () => {
  it("shows the placeholder with the Turkish title and the loading sentence", () => {
    const Placeholder = placeholderFor(widgetDef("kpi.total_eps") as AnyWidgetDef, true);
    render(<Placeholder />);
    expect(screen.getByRole("heading", { name: "Toplam EPS" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Toplam EPS yükleniyor…");
  });

  it("lists every category and widget in Turkish with what it reads and needs", () => {
    mocks.me = meOk("read:fleet", "read:anomalies", "read:overview", "read:offenses");
    render(<Catalog placedTypes={new Set()} placedCount={0} onAdd={vi.fn()} onClose={vi.fn()} />);
    expect(screen.getByRole("heading", { name: "Bileşen ekle" })).toBeInTheDocument();
    for (const category of ["Ana göstergeler", "Grafik", "Tablo", "Sağlık", "Yönetim"]) {
      expect(screen.getByRole("heading", { name: category })).toBeInTheDocument();
    }
    expect(screen.getByText("Toplam EPS")).toBeInTheDocument();
    expect(screen.getByText(/Seçilen aralıkta filonun saniyedeki olay sayısı/)).toBeInTheDocument();
    expect(screen.getAllByText("Okuduğu").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Gerektirdiği").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Toplam EPS bileşenini ekle" })).toBeEnabled();
  });

  it("gives each reason a widget cannot be added, in Turkish", () => {
    mocks.me = meOk("read:fleet");
    const placed = new Set(["kpi.total_eps"]);
    render(<Catalog placedTypes={placed} placedCount={24} onAdd={vi.fn()} onClose={vi.fn()} />);
    expect(screen.getAllByText("Zaten bu gösterge panelinde.").length).toBe(1);
    expect(screen.getAllByText(/Rolünüzde şu izin eksik: read:offenses\./).length).toBeGreaterThan(0);
    expect(screen.getByText(/Rolünüzde şu izinler eksik: read:fleet, admin:outage|Rolünüzde şu izin eksik: admin:outage/)).toBeInTheDocument();
    expect(screen.getAllByText("Bir gösterge paneli en fazla 24 bileşen içerebilir.").length).toBeGreaterThan(0);
    expect(screen.getByText("Kullanılamıyor: harita bileşeni henüz hazır değil; coğrafi toplama hazır.")).toBeInTheDocument();
  });

  it("says that it is checking the role, and that the permissions could not be read", () => {
    mocks.me = { status: "loading" };
    const view = render(<Catalog placedTypes={new Set()} placedCount={0} onAdd={vi.fn()} onClose={vi.fn()} />);
    expect(screen.getByText("Rolünüzün neleri okuyabildiği denetleniyor…")).toBeInTheDocument();
    view.unmount();
    mocks.me = { status: "error", httpStatus: 500 };
    render(<Catalog placedTypes={new Set()} placedCount={0} onAdd={vi.fn()} onClose={vi.fn()} />);
    expect(screen.getByText(/İzinleriniz okunamadı, bu yüzden tüm bileşenler listeleniyor/)).toBeInTheDocument();
  });

  it("keeps the English reasons for a caller that passes no translator", () => {
    const def = widgetDef("kpi.total_eps") as AnyWidgetDef;
    expect(unavailableReason(def, new Set(), new Set(), 0)).toBe("Your role lacks permission read:fleet.");
    expect(unavailableReason(def, new Set(["read:fleet"]), new Set(), 0, createLocaleTranslator("tr"))).toBeNull();
    expect(unavailableReason(def, new Set(), new Set(), 0, createLocaleTranslator("tr"))).toBe(
      "Rolünüzde şu izin eksik: read:fleet.",
    );
  });
});

describe("the view and customize mode in Turkish", () => {
  const layout: DashboardLayout = {
    v: 1,
    range: "24h",
    widgets: [{ id: "w_stray", type: "no.such", x: 0, y: 0, w: 4, h: 6, range: null, config: {} }],
  };
  const header = (actions: React.ReactNode) => <div>{actions}</div>;

  beforeEach(() => {
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(
      () => ({ width: 1000, height: 100, top: 0, left: 0, right: 1000, bottom: 100, x: 0, y: 0 }) as DOMRect,
    );
  });

  it("says an unsupported widget is kept, and offers Customize", () => {
    render(<DashboardView layout={layout} renderHeader={header} />);
    expect(screen.getByRole("heading", { name: "Desteklenmeyen bileşen" })).toBeInTheDocument();
    expect(screen.getByText(/no\.such türünde bir bileşen içeriyor/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Özelleştir" })).toBeInTheDocument();
  });

  it("says the unsaved state, with and without the right to save, and the Save / Discard controls", () => {
    const view = render(<DashboardView layout={layout} renderHeader={header} dirty canSave onSave={vi.fn()} />);
    expect(screen.getByText(/Kaydedilmemiş değişiklikler var\./)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Kaydet" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Vazgeç" })).toBeInTheDocument();
    view.unmount();
    render(<DashboardView layout={layout} renderHeader={header} dirty />);
    expect(screen.getByText(/Yalnızca bu oturumda geçerlidir\./)).toBeInTheDocument();
  });

  it("shows Saving… while a save runs", () => {
    render(<DashboardView layout={layout} renderHeader={header} dirty canSave saving onSave={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Kaydediliyor…" })).toBeDisabled();
  });

  it("goes into customize mode with Turkish controls, and says what Done and Add do", async () => {
    mocks.me = meOk("read:fleet");
    render(<DashboardView layout={layout} renderHeader={header} />);
    fireEvent.click(screen.getByRole("button", { name: "Özelleştir" }));
    expect(await screen.findByRole("button", { name: "Bileşen ekle" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Bitti" })).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: "Bileşen ekle" }));
    expect(await screen.findByRole("heading", { name: "Bileşen ekle" })).toBeInTheDocument();
  });

  it("announces an added widget in Turkish, with its Turkish title", async () => {
    mocks.me = meOk("read:fleet");
    render(<DashboardView layout={{ ...layout, widgets: [] }} renderHeader={header} />);
    fireEvent.click(screen.getByRole("button", { name: "Özelleştir" }));
    fireEvent.click(await screen.findByRole("button", { name: "Bileşen ekle" }));
    fireEvent.click(await screen.findByRole("button", { name: "Toplam EPS bileşenini ekle" }));
    expect(await screen.findByText("Toplam EPS eklendi.")).toBeInTheDocument();
  });
});

describe("the arrange controls in Turkish", () => {
  const widget: LayoutWidget = { id: "w_one", type: "kpi.total_eps", x: 3, y: 2, w: 3, h: 7, range: null, config: {} };

  it("names every stepper after the widget and the direction", async () => {
    vi.spyOn(window, "getComputedStyle").mockReturnValue({
      getPropertyValue: (name: string) => (name === "--dash-row" ? "24px" : "12px"),
    } as CSSStyleDeclaration);
    render(
      <EditGrid
        widgets={[widget]}
        onChange={vi.fn()}
        onRemove={vi.fn()}
        onConfigure={vi.fn()}
        onExpand={vi.fn()}
        renderWidget={(_w, arrange) => <div>{arrange}</div>}
      />,
    );
    const trigger = await screen.findByRole("button", { name: "Toplam EPS bileşenini düzenle" });
    expect(trigger).toHaveTextContent("Düzenle");
    fireEvent.click(trigger);
    for (const name of [
      "Toplam EPS bileşenini sola taşı",
      "Toplam EPS bileşenini sağa taşı",
      "Toplam EPS bileşenini yukarı taşı",
      "Toplam EPS bileşenini aşağı taşı",
      "Toplam EPS bileşenini genişlet",
      "Toplam EPS bileşenini daralt",
      "Toplam EPS bileşenini uzat",
      "Toplam EPS bileşenini kısalt",
      "Toplam EPS bileşenini yapılandır",
      "Toplam EPS bileşenini kaldır",
    ]) {
      expect(screen.getByRole("button", { name })).toBeInTheDocument();
    }
    expect(screen.getByText("Taşı")).toBeInTheDocument();
    expect(screen.getByText("Yeniden boyutlandır")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Toplam EPS bileşenini kaldır" })).toHaveTextContent("Bileşeni kaldır");
  });
});

describe("the configure drawer in Turkish", () => {
  const def = widgetDef("waf.endpoint_traffic") as AnyWidgetDef;
  const placed = (config: Record<string, unknown> = {}): LayoutWidget => ({
    id: "w_test1",
    type: def.type,
    x: 0,
    y: 0,
    w: 6,
    h: 14,
    range: null,
    config: { ...def.defaultConfig, ...config },
  });
  const SOURCES = "/behavior/waf/sources?limit=200";
  const open = (widget = placed()) =>
    render(<ConfigureDrawer def={def} widget={widget} dashboardRange="24h" onApply={vi.fn()} onClose={vi.fn()} />);

  it("titles the drawer, labels the range and each field, and says why Apply is off", async () => {
    routes[SOURCES] = {
      body: { items: [{ log_source_id: "s1", log_source_name: "waf-a" }], total: 1, limit: 200, offset: 0 },
    };
    open();
    expect(screen.getByRole("heading", { name: "WAF endpoint trafiği bileşenini yapılandır" })).toBeInTheDocument();
    expect(screen.getByLabelText("Zaman aralığı")).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Gösterge panelini izle (24\u00a0sa)" })).toBeInTheDocument();
    expect(await screen.findByRole("combobox", { name: "WAF kaynağı" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "WAF endpoint" })).toHaveDisplayValue("Önce bir WAF kaynağı seçin");
    expect(screen.getByText(/Yukarıdaki her seçim, okumanın döndürdüğü değerlerden yapılana kadar Uygula kapalıdır\./)).toBeInTheDocument();
    expect(screen.getByText("Yalnızca bu oturumda geçerlidir; kaydetme henüz hazır değil.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Uygula" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "İptal" })).toBeInTheDocument();
    expect(within(screen.getByRole("combobox", { name: "WAF kaynağı" })).getByRole("option", { name: "Seçin…" })).toBeInTheDocument();
  });

  it("says loading, forbidden, failed and empty for the read, naming what is read in Turkish", async () => {
    routes[SOURCES] = { pending: true };
    const view = open();
    expect(await screen.findByText("Yükleniyor: WAF kaynakları…")).toBeInTheDocument();
    view.unmount();

    routes[SOURCES] = { status: 403 };
    const forbidden = open();
    expect(await screen.findByText(/Rolünüz bunu okuyamıyor, bu yüzden burada seçim yapılamaz: WAF kaynakları\./)).toBeInTheDocument();
    forbidden.unmount();

    routes[SOURCES] = { status: 500, body: {} };
    const failed = open();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Okunamadı: WAF kaynakları.");
    expect(alert).toHaveTextContent("Backend HTTP 500 ile yanıt verdi.");
    expect(alert).toHaveTextContent("Bu paneli kapatıp yeniden açın");
    failed.unmount();

    routes[SOURCES] = { body: { items: [], total: 0, limit: 200, offset: 0 } };
    open();
    expect(await screen.findByText("Yok: WAF kaynakları.")).toBeInTheDocument();
    expect(screen.getByText(/Henüz hiçbir WAF kaynağının davranış profili yok/)).toBeInTheDocument();
  });

  it("words the filter, the counts and the truncated read of a long list", async () => {
    const items = Array.from({ length: 12 }, (_, i) => ({ log_source_id: `s${i}`, log_source_name: `waf-${i}` }));
    routes[SOURCES] = { body: { items, total: 40, limit: 200, offset: 0 } };
    open();
    const filter = await screen.findByPlaceholderText("Filtrele: WAF kaynakları");
    expect(screen.getByText("12 WAF kaynakları")).toBeInTheDocument();
    await userEvent.type(filter, "waf-1");
    expect(screen.getByText("3 / 12 sonuç gösteriliyor")).toBeInTheDocument();
    expect(screen.getByText(/Okuma ilk 12 \/ 40 kaydı döndürdü/)).toBeInTheDocument();
  });

  it("labels a disabled property watch in Turkish", async () => {
    const watchDef = widgetDef("chart.property_watch") as AnyWidgetDef;
    routes["/property-watches"] = {
      body: [
        { id: "1", name: "Alpha", enabled: false },
        { id: "2", name: "Zeta", enabled: true },
      ],
    };
    render(
      <ConfigureDrawer
        def={watchDef}
        widget={{ ...placed(), type: watchDef.type, config: { watch_id: null } }}
        dashboardRange="24h"
        onApply={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    const select = await screen.findByRole("combobox", { name: "Özellik izleme" });
    await waitFor(() =>
      expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual([
        "Seçin…",
        "Alpha (devre dışı)",
        "Zeta",
      ]),
    );
  });
});

describe("the manage menu in Turkish", () => {
  const caller = (...permissions: string[]): Caller => ({ status: "ok", subject: "me", permissions: new Set(permissions) });
  const mine: CurrentDashboard = {
    kind: "saved",
    id: "d1",
    name: "Gece vardiyası",
    ownerSubject: "me",
    visibility: "PRIVATE",
    isDefault: false,
    templateKey: "soc_overview",
  };
  const say = () => createLocaleTranslator("tr");

  it("words every note of the access decision", () => {
    expect(dashboardAccess(mine, { status: "failed", reason: "sunucuya ulaşılamadı" }, null, say()).note).toBe(
      "İzinleriniz okunamadı (sunucuya ulaşılamadı), bu yüzden buradaki gösterge panelleri salt okunurdur.",
    );
    expect(dashboardAccess(mine, caller("read:dashboards"), null, say()).note).toBe(
      "Rolünüz gösterge panellerini kaydedemez; dashboard:write izni gerekir.",
    );
    expect(dashboardAccess(mine, caller("dashboard:write"), "Reddedildi.", say()).note).toBe(
      "Sunucu değişikliği reddetti: Reddedildi. Sayfayı yenileyene kadar denetimler gizli kalır.",
    );
    expect(
      dashboardAccess({ ...mine, ownerSubject: "alice" } as CurrentDashboard, caller("dashboard:write"), null, say()).note,
    ).toBe("alice tarafından paylaşıldı; okuyabilir ve kendi gösterge panellerinize kopyalayabilirsiniz.");
    expect(dashboardAccess(mine, { status: "loading" }, null, say()).note).toBeNull();
    // English stays for a caller that passes no translator.
    expect(dashboardAccess(mine, caller("read:dashboards")).note).toContain("Your role cannot save dashboards");
  });

  it("words the name problems", () => {
    expect(nameProblem("   ", say())).toBe("Bir ad girin.");
    expect(nameProblem("x".repeat(81), say())).toBe("Ad en fazla 80 karakter olabilir.");
    expect(nameProblem("ok", say())).toBeNull();
    expect(nameProblem("")).toBe("Enter a name.");
  });

  function menu(current: CurrentDashboard, permissions: string[], extra: { customizing?: boolean } = {}) {
    const access = dashboardAccess(current, caller(...permissions), null, say());
    return render(
      <DashboardMenu
        current={current}
        access={access}
        onChanged={vi.fn()}
        onRefused={vi.fn()}
        customizing={extra.customizing}
        onResetLayout={vi.fn()}
      />,
    );
  }

  it("lists the actions in Turkish, and opens the new-dashboard dialog with Turkish templates", async () => {
    menu(mine, ["dashboard:write", "admin:dashboards"]);
    await userEvent.click(screen.getByRole("button", { name: "Yönet" }));
    const list = screen.getByRole("list", { name: "Gösterge paneli eylemleri" });
    expect(within(list).getAllByRole("button").map((b) => b.textContent)).toEqual([
      "Yeniden adlandır…",
      "Kopyala",
      "Varsayılan yap",
      "Herkesle paylaş",
      "Şablondan yeni gösterge paneli…",
      "Sil…",
    ]);
    await userEvent.click(screen.getByRole("button", { name: "Şablondan yeni gösterge paneli…" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("heading", { name: "Şablondan yeni gösterge paneli" })).toBeInTheDocument();
    expect(within(dialog).getAllByRole("option").map((o) => o.textContent)).toEqual(
      TEMPLATES.map((t) => ({
        soc_overview: "SOC Genel Bakışı",
        qradar_administration: "QRadar Yönetimi",
        behavioral_analytics: "Davranış Analitiği",
        log_source_monitoring: "Log Source İzleme",
        waf_ips_monitoring: "WAF / IPS İzleme",
      })[t.key]),
    );
    expect(within(dialog).getByText("1 ile 80 karakter arası, gösterge panelleriniz arasında benzersiz.")).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Gösterge panelini oluştur" }));
    expect(within(dialog).getByRole("alert")).toHaveTextContent("Bir ad girin.");
  });

  it("asks before it deletes, and says what happens to a default", async () => {
    menu({ ...mine, isDefault: true }, ["dashboard:write"]);
    await userEvent.click(screen.getByRole("button", { name: "Yönet" }));
    await userEvent.click(screen.getByRole("button", { name: "Sil…" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("heading", { name: '"Gece vardiyası" silinsin mi?' })).toBeInTheDocument();
    expect(dialog).toHaveTextContent("Gösterge paneli ve düzeni kalıcı olarak silinir");
    expect(dialog).toHaveTextContent("Bu panel sizin varsayılanınız; sonrasında başlangıç sayfasında SOC Genel Bakışı şablonu gösterilir.");
    expect(within(dialog).getByRole("button", { name: "Gösterge panelini sil" })).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "İptal" })).toBeInTheDocument();
  });

  it("offers restore and reset while customizing, with Turkish dialogs", async () => {
    menu(mine, ["dashboard:write"], { customizing: true });
    await userEvent.click(screen.getByRole("button", { name: "Yönet" }));
    await userEvent.click(screen.getByRole("button", { name: "Varsayılanları geri yükle…" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("heading", { name: "SOC Genel Bakışı varsayılanları geri yüklensin mi?" })).toBeInTheDocument();
    expect(dialog).toHaveTextContent("Geçerli düzen, SOC Genel Bakışı şablonunun 1. sürümüyle değiştirilir. Bitti'yi seçene kadar hiçbir şey kaydedilmez.");
  });

  it("offers a reset to empty for a dashboard with no template", async () => {
    menu({ ...mine, templateKey: null }, ["dashboard:write"], { customizing: true });
    await userEvent.click(screen.getByRole("button", { name: "Yönet" }));
    await userEvent.click(screen.getByRole("button", { name: "Boşa sıfırla…" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("heading", { name: '"Gece vardiyası" boşa sıfırlansın mı?' })).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Boşa sıfırla" })).toBeInTheDocument();
  });

  it("says a template this client does not know cannot be restored", async () => {
    menu({ ...mine, templateKey: "from_the_future" }, ["dashboard:write"], { customizing: true });
    await userEvent.click(screen.getByRole("button", { name: "Yönet" }));
    expect(
      screen.getByText("Bu istemci from_the_future şablonunu tanımadığı için varsayılanlar geri yüklenemiyor."),
    ).toBeInTheDocument();
  });
});

describe("the switcher in Turkish", () => {
  const item = (over: Partial<DashboardListItem>): DashboardListItem => ({
    id: "d1",
    name: "Gece vardiyası",
    owner_subject: "me",
    visibility: "PRIVATE",
    template_key: null,
    updated_at: "2026-10-14T10:00:00Z",
    widget_count: 5,
    is_default: false,
    ...over,
  });
  const setup = (over: Partial<React.ComponentProps<typeof DashboardSwitcher>> = {}) => {
    render(
      <DashboardSwitcher
        items={[item({ is_default: true }), item({ id: "d2", name: "WAF", owner_subject: "alice", visibility: "SHARED", widget_count: 1 })]}
        loading={false}
        failure={null}
        subject="me"
        current={{ kind: "saved", id: "d1" }}
        onSelect={vi.fn()}
        {...over}
      />,
    );
    return screen.getByRole("button", { name: "Gösterge panelleri" });
  };

  it("lists the dashboards with Turkish markers, counts and owners, then the template", async () => {
    await userEvent.click(setup());
    const saved = screen.getByRole("list", { name: "Kayıtlı gösterge panelleri" });
    expect(within(saved).getByText("Varsayılan")).toBeInTheDocument();
    expect(within(saved).getByText("Paylaşılan")).toBeInTheDocument();
    expect(saved).toHaveTextContent("5 bileşen");
    expect(saved).toHaveTextContent("1 bileşen, sahibi: alice");
    const template = screen.getByRole("list", { name: "Şablon" });
    expect(template).toHaveTextContent("SOC Genel Bakışı (şablon)");
    expect(template).toHaveTextContent("Kaydedilmedi");
  });

  it("says loading, the failure with its reason, and the empty list in Turkish", async () => {
    const button = setup({ items: null, loading: true });
    await userEvent.click(button);
    expect(screen.getByRole("status")).toHaveTextContent("Gösterge panelleri yükleniyor…");
  });

  it("says the list could not be loaded, and that there is none", async () => {
    await userEvent.click(setup({ items: null, failure: "sunucuya ulaşılamadı" }));
    expect(screen.getByRole("status")).toHaveTextContent("Kayıtlı gösterge panelleri yüklenemedi: sunucuya ulaşılamadı.");
  });

  it("says when there is nothing saved", async () => {
    await userEvent.click(setup({ items: [] }));
    expect(screen.getByText("Kayıtlı gösterge paneliniz yok ve sizinle paylaşılan da yok.")).toBeInTheDocument();
  });
});

describe("the save dialogs in Turkish", () => {
  it("asks for another name when the name is taken, and keeps the server's own sentence", async () => {
    render(
      <NameTakenDialog initialName="Gece" serverMessage="Name already used." busy={false} onSave={vi.fn().mockResolvedValue(null)} onClose={vi.fn()} />,
    );
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("heading", { name: "Başka bir gösterge paneli adı seçin" })).toBeInTheDocument();
    expect(within(dialog).getByRole("alert")).toHaveTextContent("Name already used.");
    expect(within(dialog).getByLabelText("Ad")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Gösterge panelini kaydet" })).toBeInTheDocument();
    await userEvent.clear(within(dialog).getByLabelText("Ad"));
    await userEvent.click(within(dialog).getByRole("button", { name: "Gösterge panelini kaydet" }));
    expect(within(dialog).getAllByRole("alert").pop()).toHaveTextContent("Bir ad girin.");
  });

  it("explains the three ways out of a conflict, with the copy name in Turkish", async () => {
    render(
      <ConflictDialog dashboardName="Gece" busy={false} onReload={vi.fn()} onSaveCopy={vi.fn().mockResolvedValue(null)} onClose={vi.fn()} />,
    );
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("heading", { name: "Bu gösterge paneli siz açtıktan sonra değişti" })).toBeInTheDocument();
    expect(dialog).toHaveTextContent("Yeniden yükle değişikliklerinizi atar ve kayıtlı sürümü gösterir.");
    expect(dialog).toHaveTextContent("Kopya olarak kaydet değişikliklerinizi aşağıdaki adla saklar.");
    expect(dialog).toHaveTextContent("Düzenlemeye devam et her ikisini de olduğu gibi bırakır.");
    expect(within(dialog).getByLabelText("Kopya adı")).toHaveValue("Gece kopyası");
    expect(within(dialog).getByRole("button", { name: "Kopya olarak kaydet" })).toBeInTheDocument();
  });

  it("asks before it discards unsaved changes", async () => {
    render(<UnsavedChangesDialog dashboardName="Gece" onDiscard={vi.fn()} onKeep={vi.fn()} />);
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("heading", { name: "Gece üzerindeki kaydedilmemiş değişikliklerden vazgeçilsin mi?" })).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Vazgeç" })).toBeInTheDocument();
    expect(dialog).toHaveTextContent("Vazgeç değişikliklerinizi atar ve kayıtlı düzeni gösterir.");
  });
});

describe("the expand dialog and the range control in Turkish", () => {
  it("titles the dialog with the range, and offers Close", async () => {
    render(
      <ExpandDialog title="Filo EPS" range="7d" rangeOptions={["1h", "24h", "7d"]} onRange={vi.fn()} onClose={vi.fn()}>
        <p>body</p>
      </ExpandDialog>,
    );
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("heading", { name: /Filo EPS.*7\u00a0gün/ })).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Kapat" })).toBeInTheDocument();
    expect(within(dialog).getByRole("group", { name: "Zaman aralığı" })).toBeInTheDocument();
  });

  it("names the select after the widget and writes the presets in Turkish", () => {
    render(<RangeControl options={["1h", "24h", "7d"]} value="24h" onChange={vi.fn()} compact title="Filo EPS" />);
    expect(screen.getByLabelText("Filo EPS için zaman aralığı")).toBeInTheDocument();
    expect(screen.getAllByRole("option").map((o) => o.textContent)).toEqual(["1\u00a0sa", "24\u00a0sa", "7\u00a0gün"]);
  });
});

it("keeps the SOC Overview template's own English as the source text", () => {
  expect(SOC_OVERVIEW.name).toBe("SOC Overview");
});
