// The Home page in Turkish (D-37): which dashboard is shown and how, every state of the page
// (loading, the template, the list failing, a dashboard that is missing, forbidden or unreadable),
// the badges, and the save outcomes. The fake server is the English test's, reduced to what these
// states need. English stays the default of the harness; each test opts in with setTestLocale("tr").

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setTestLocale } from "../../../vitest.setup";

import { HomeDashboard } from "./HomeDashboard";

const mocks = vi.hoisted(() => ({ search: "", push: vi.fn() }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mocks.push }),
  useSearchParams: () => new URLSearchParams(mocks.search),
}));
vi.mock("@/components/shell/AutoRefresh", () => ({
  formatClockUtc: () => "13:58:00 UTC",
  useRefreshTick: () => 0,
  useReportDataAge: vi.fn(),
}));
vi.mock("@/widgets/DashboardView", () => ({
  DashboardView: (props: {
    renderHeader: (actions: React.ReactNode) => React.ReactNode;
    leading?: React.ReactNode | ((customizing: boolean) => React.ReactNode);
    saveMessage?: string | null;
    onSave?: () => Promise<boolean>;
  }) => (
    <>
      {props.renderHeader(typeof props.leading === "function" ? props.leading(false) : props.leading)}
      <button type="button" onClick={() => void props.onSave?.()}>
        kaydet-tetikle
      </button>
      <p data-testid="save-message">{props.saveMessage ?? ""}</p>
    </>
  ),
}));

const layout = { v: 1, range: "24h", widgets: [] };
interface Row {
  id: string;
  name: string;
  owner: string;
  visibility: "PRIVATE" | "SHARED";
  isDefault: boolean;
  description?: string | null;
  templateKey?: string | null;
}
let rows: Row[];
let me: { status: number; body: unknown };
let listStatus: number;
let createStatus: number;

const out = (r: Row) => ({
  id: r.id,
  name: r.name,
  description: r.description ?? null,
  owner_subject: r.owner,
  visibility: r.visibility,
  template_key: r.templateKey ?? null,
  template_version: null,
  schema_version: 1,
  layout,
  revision: 1,
  created_at: "2026-10-14T10:00:00Z",
  updated_at: "2026-10-14T10:00:00Z",
  updated_by: r.owner,
  is_default: r.isDefault,
});
const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status });

beforeEach(() => {
  setTestLocale("tr");
  mocks.search = "";
  rows = [];
  me = { status: 200, body: { subject: "me", email: null, permissions: [], effective_permissions: ["read:dashboards", "dashboard:write"] } };
  listStatus = 200;
  createStatus = 201;
  HTMLDialogElement.prototype.showModal = function showModal() {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function close() {
    this.removeAttribute("open");
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string, init?: RequestInit) => {
      const path = String(input).replace(/^.*\/api\/v1/, "");
      if (path === "/me") return json(me.status, me.body);
      if (init?.method === "POST" && path === "/dashboards") {
        if (createStatus !== 201) return json(createStatus, { detail: "Name already used." });
        return json(201, out({ id: "new1", name: "SOC Overview", owner: "me", visibility: "PRIVATE", isDefault: false }));
      }
      if (init?.method && init.method !== "GET") return json(204, {});
      if (path.startsWith("/dashboards?")) {
        if (listStatus !== 200) return json(listStatus, { detail: "unavailable" });
        return json(200, {
          items: rows.map((r) => ({
            id: r.id,
            name: r.name,
            owner_subject: r.owner,
            visibility: r.visibility,
            template_key: r.templateKey ?? null,
            updated_at: "2026-10-14T10:00:00Z",
            widget_count: 0,
            is_default: r.isDefault,
          })),
          total: rows.length,
          limit: 200,
          offset: 0,
        });
      }
      const match = /^\/dashboards\/([^/]+)$/.exec(path);
      if (match) {
        const row = rows.find((r) => r.id === match[1]);
        return row ? json(200, out(row)) : json(404, { detail: "dashboard not found" });
      }
      return json(404, {});
    }),
  );
});

const h1 = () => screen.getByRole("heading", { level: 1 });

describe("the Home page in Turkish", () => {
  it("shows the template under its Turkish name, with the not-saved badge and the 24 h sentence", async () => {
    render(<HomeDashboard />);
    await waitFor(() => expect(h1()).toHaveTextContent("SOC Genel Bakışı"));
    expect(await screen.findByText("Şablon, kaydedilmedi")).toBeInTheDocument();
    expect(screen.getByText("Filo hacmi, kaynak sağlığı, anomaliler ve offense'ların 24 saatlik görünümü. Saatler UTC.")).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Gösterge panelleri" })).toBeInTheDocument();
  });

  it("shows a saved default dashboard with its badges, and the template's Turkish sentence when it has none of its own", async () => {
    rows = [{ id: "d1", name: "Gündüz vardiyası", owner: "me", visibility: "SHARED", isDefault: true, templateKey: "soc_overview" }];
    render(<HomeDashboard />);
    await waitFor(() => expect(h1()).toHaveTextContent("Gündüz vardiyası"));
    expect(await screen.findByText("Varsayılan")).toBeInTheDocument();
    expect(screen.getByText("Paylaşılan")).toBeInTheDocument();
    expect(
      screen.getByText("Filo hacmi, kaynak sağlığı, anomaliler ve offense'lar. Harita hazır olana kadar coğrafya bileşeni yer almaz."),
    ).toBeInTheDocument();
  });

  it("falls back to the template with a sentence that names why, when the list fails", async () => {
    listStatus = 503;
    render(<HomeDashboard />);
    const status = await screen.findByText(/Kayıtlı gösterge panelleri yüklenemedi/);
    expect(status).toHaveTextContent("sunucu HTTP 503 ile yanıt verdi");
    expect(status).toHaveTextContent("Bu, SOC Genel Bakışı şablonudur ve burada yapılan değişiklikler kaydedilmez.");
  });

  it("says a dashboard that is missing does not exist, and where to go", async () => {
    mocks.search = "dashboard=nope";
    render(<HomeDashboard />);
    expect(await screen.findByText("Bu gösterge paneli yok veya sizinle paylaşılmadı.")).toBeInTheDocument();
    expect(screen.getByText("Bağlantı oluşturulduktan sonra silinmiş veya özel yapılmış olabilir.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "SOC Genel Bakışı şablonunu aç" })).toHaveAttribute("href", "/");
  });

  it("offers the default dashboard when one exists, in Turkish", async () => {
    rows = [{ id: "d2", name: "Varsayılanım", owner: "me", visibility: "PRIVATE", isDefault: true }];
    mocks.search = "dashboard=nope";
    render(<HomeDashboard />);
    expect(await screen.findByRole("link", { name: "Varsayılan gösterge panelinizi açın" })).toBeInTheDocument();
  });

  it("says a dashboard could not be loaded, with the reason and what to do", async () => {
    rows = [{ id: "d1", name: "X", owner: "me", visibility: "PRIVATE", isDefault: false }];
    mocks.search = "dashboard=d1";
    const original = globalThis.fetch;
    globalThis.fetch = vi.fn(async (input: string, init?: RequestInit) => {
      if (/^.*\/dashboards\/d1$/.test(String(input))) return json(500, { detail: "boom" });
      return (original as typeof fetch)(input, init);
    }) as unknown as typeof fetch;
    render(<HomeDashboard />);
    expect(await screen.findByText("Bu gösterge paneli yüklenemedi.")).toBeInTheDocument();
    expect(screen.getByText(/Ayrıntı: sunucu HTTP 500 ile yanıt verdi\. Hiçbir şey değiştirilmedi\./)).toBeInTheDocument();
  });

  it("says why there is less to do when the role cannot save, in the header", async () => {
    me = { status: 200, body: { subject: "me", email: null, permissions: [], effective_permissions: ["read:dashboards"] } };
    render(<HomeDashboard />);
    expect(await screen.findByText("Rolünüz gösterge panellerini kaydedemez; dashboard:write izni gerekir.")).toBeInTheDocument();
  });

  it("says the permissions could not be read when /me fails", async () => {
    me = { status: 500, body: {} };
    render(<HomeDashboard />);
    expect(
      await screen.findByText(/İzinleriniz okunamadı \(sunucu HTTP 500 ile yanıt verdi\), bu yüzden buradaki gösterge panelleri salt okunurdur\./),
    ).toBeInTheDocument();
  });

  it("lists the template in the switcher under its Turkish name", async () => {
    render(<HomeDashboard />);
    await screen.findByText("Şablon, kaydedilmedi");
    await userEvent.click(await screen.findByRole("button", { name: "Gösterge panelleri" }));
    expect(screen.getByText("SOC Genel Bakışı (şablon)")).toBeInTheDocument();
  });

  it("says Kaydedildi after a save, not the reducer's English word", async () => {
    render(<HomeDashboard />);
    await screen.findByText("Şablon, kaydedilmedi");
    expect(screen.getByTestId("save-message")).toHaveTextContent("");
    await userEvent.click(screen.getByRole("button", { name: "kaydet-tetikle" }));
    await waitFor(() => expect(screen.getByTestId("save-message")).toHaveTextContent("Kaydedildi"));
  });

  it("opens the name dialog, in Turkish, when the server says the name is taken", async () => {
    createStatus = 409;
    render(<HomeDashboard />);
    await screen.findByText("Şablon, kaydedilmedi");
    await userEvent.click(screen.getByRole("button", { name: "kaydet-tetikle" }));
    expect(await screen.findByRole("heading", { name: "Başka bir gösterge paneli adı seçin" })).toBeInTheDocument();
  });
});
