import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LoginForm } from "@/app/login/LoginForm";
import { Pagination } from "@/components/Pagination";
import { ApiVersionLine } from "@/components/auth/ApiVersionLine";
import { AppFrame } from "@/components/auth/AppFrame";
import { NoRole } from "@/components/auth/NoRole";
import { PasswordChange } from "@/components/auth/PasswordChange";
import { SignOutButton } from "@/components/auth/SignOutButton";
import { StaticSession, type SessionValue } from "@/components/auth/session";
import { Account } from "@/components/shell/Account";
import { AlertBell } from "@/components/shell/AlertBell";
import { AppNav } from "@/components/shell/AppNav";
import { AutoRefresh } from "@/components/shell/AutoRefresh";
import { ConnectionStatus } from "@/components/shell/ConnectionStatus";
import { TopSearch } from "@/components/shell/TopSearch";
import { ChangeStatus } from "@/components/ui/ChangeStatus";
import { CodePanel } from "@/components/ui/CodePanel";
import { Disclosure } from "@/components/ui/Disclosure";
import { PageHeader } from "@/components/ui/PageHeader";
import { ErrorState, LoadingState } from "@/components/ui/RouteState";
import { TableScroll } from "@/components/ui/TableScroll";
import { Tabs } from "@/components/ui/Tabs";
import { UtcTime } from "@/components/ui/UtcTime";
import { accountStatus } from "@/components/auth/accountStatus";
import { bindOutcome, probeHeadline, trustWords } from "@/components/auth/probeCopy";
import { errorTexts } from "@/components/auth/reasons";
import { createLocaleTranslator } from "@/i18n/fallback";
import type { CallerInfo } from "@/lib/api";
import { setTestLocale } from "../../vitest.setup";

const path = vi.hoisted(() => ({ value: "/behavior" }));
vi.mock("next/navigation", () => ({
  usePathname: () => path.value,
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

function me(over: Partial<CallerInfo> = {}): CallerInfo {
  return {
    subject: "ada",
    email: "ada@example.com",
    permissions: ["admin:*"],
    effective_permissions: ["admin:*"],
    username: "ada",
    auth_source: "LOCAL",
    roles: ["admin"],
    ...over,
  };
}

function session(over: Partial<SessionValue> = {}): SessionValue {
  return {
    status: "ready",
    me: me(),
    error: null,
    reload: vi.fn(),
    signOut: vi.fn().mockResolvedValue(null),
    ...over,
  };
}

beforeEach(() => {
  setTestLocale("tr");
  path.value = "/behavior";
});

describe("Turkish shared interface", () => {
  it("translates navigation, search, alerts, refresh and connection status", () => {
    render(
      <>
        <AppNav />
        <TopSearch />
        <AlertBell overview={{ state: "ok", data: { alerts: { open: 1204 } } as never }} />
        <AutoRefresh />
        <ConnectionStatus
          health={{ state: "ok", data: { status: "ready", database: "ok" } }}
          overview={{ state: "forbidden" }}
          cadence={{ state: "forbidden" }}
        />
      </>,
    );
    expect(screen.getByRole("navigation", { name: "Ana gezinti" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Açık uyarılar: 1.204" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Yenileme" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: /Kaynaklarda/ })).toHaveAttribute(
      "placeholder",
      "Kaynak, kural ve anomali ara",
    );
    expect(screen.getByText("Durum").parentElement).toHaveAccessibleName("Bağlantı durumu: Bağlı");
  });

  it("translates the account menu and account-level auth controls", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        status: 200,
        json: async () => ({ version: "1.2.3", git_sha: null, migration_head: "1" }),
      })),
    );
    const value = session();
    render(
      <StaticSession value={value}>
        <Account />
        <ApiVersionLine />
        <SignOutButton />
      </StaticSession>,
    );
    await userEvent.click(screen.getByLabelText("Hesap: ada"));
    expect(screen.getByText("ada olarak oturum açıldı")).toBeVisible();
    expect(screen.getByRole("link", { name: "Hesap ayarları" })).toBeVisible();
    expect(await screen.findAllByText("API sürümü 1.2.3")).not.toHaveLength(0);
    expect(screen.getAllByRole("button", { name: "Oturumu kapat" })).not.toHaveLength(0);
  });

  it("translates login, password, no-role and frame states", () => {
    const anonymous = session({ status: "anonymous", me: null });
    const { unmount } = render(
      <StaticSession value={anonymous}><AppFrame><p>İçerik</p></AppFrame></StaticSession>,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Oturum açmanız gerekiyor.");
    unmount();

    render(
      <StaticSession value={session()}>
        <LoginForm />
        <PasswordChange />
        <NoRole me={me()} />
      </StaticSession>,
    );
    expect(screen.getByRole("form", { name: "Oturum aç" })).toBeInTheDocument();
    expect(screen.getByRole("form", { name: "Parolayı değiştir" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Rol atanmadı" })).toBeInTheDocument();
  });

  it("translates shared state, copy, save and pagination controls", () => {
    render(
      <>
        <LoadingState what="kaynaklar" />
        <ErrorState what="kaynaklar" onRetry={vi.fn()} />
        <ChangeStatus outcome={{ kind: "pending" }} />
        <CodePanel label="AQL" code="SELECT 1" />
        <Pagination total={23} limit={10} offset={0} basePath="/rules" />
      </>,
    );
    expect(screen.getByText("kaynaklar yükleniyor…")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("kaynaklar gösterilemedi.");
    expect(screen.getByText("Kaydediliyor…")).toBeInTheDocument();
    fireEvent.click(screen.getByText("AQL"));
    expect(screen.getByRole("button", { name: "AQL sorgusunu panoya kopyala" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Sayfalama" })).toHaveTextContent("1–10 / 23");
    expect(screen.getByText("Sonraki")).toBeInTheDocument();
  });

  it("keeps caller-owned shared content and UTC time intact", () => {
    render(
      <>
        <PageHeader title="İnceleme" description="Toplama penceresi" />
        <Disclosure summary="Kanıt"><p>Katkı sağlayanlar</p></Disclosure>
        <Tabs label="Kanıt boyutları" tabs={[{ id: "one", label: "Kaynak", content: () => <p>Değer</p> }]} />
        <TableScroll label="Kaynaklar"><table><tbody><tr><td>Bir</td></tr></tbody></table></TableScroll>
        <UtcTime value="2026-10-12T09:15:00Z" />
      </>,
    );
    expect(screen.getByRole("heading", { name: "İnceleme" })).toBeInTheDocument();
    expect(screen.getByText("Kanıt")).toBeInTheDocument();
    expect(screen.getByRole("tablist", { name: "Kanıt boyutları" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Kaynaklar" })).toBeInTheDocument();
    expect(screen.getByText("2026-10-12 09:15:00 UTC")).toBeInTheDocument();
  });

  it("translates pure auth copy while preserving backend sentences", () => {
    const t = createLocaleTranslator("tr");
    expect(bindOutcome("not_attempted", null, t).label).toBe("Denenmedi");
    expect(trustWords("system", t)).toBe("Sistem güven deposu");
    expect(probeHeadline("failed", "not_attempted", "ada", t)).toBe(
      "Bağlantı TLS aşamasında başarısız oldu.",
    );
    expect(accountStatus({ disabled: true, locked_until: null }, Date.now(), t)).toEqual([
      "devre dışı",
    ]);
    expect(errorTexts(new Error("backend sentence"), t)).toEqual([
      "İstek gönderilemedi. Bağlantınızı denetleyip tekrar deneyin.",
    ]);
  });
});
