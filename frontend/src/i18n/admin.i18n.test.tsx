// I18N-8: administration (the index, users, directory, classification rules, outage policy) and the
// field-mapping form in Turkish. The colocated tests pin the unchanged English contract; these pin
// the Turkish states, the codes that stay codes (role keys, permission names, backend sentences),
// and the Turkish number form.

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setTestLocale } from "../../vitest.setup";
import { AdminIndex } from "@/app/admin/AdminIndex";
import AdminPage, { generateMetadata as adminMetadata } from "@/app/admin/page";
import ClassificationRulesPage, {
  generateMetadata as classificationMetadata,
} from "@/app/admin/classification-rules/page";
import ClassificationError from "@/app/admin/classification-rules/error";
import ClassificationLoading from "@/app/admin/classification-rules/loading";
import { DirectoryPage } from "@/app/admin/directory/DirectoryPage";
import DirectoryError from "@/app/admin/directory/error";
import DirectoryLoading from "@/app/admin/directory/loading";
import { OutagePolicyManager } from "@/app/admin/outage-policy/OutagePolicyManager";
import OutageError from "@/app/admin/outage-policy/error";
import OutageLoading from "@/app/admin/outage-policy/loading";
import OutagePolicyPage, { generateMetadata as outageMetadata } from "@/app/admin/outage-policy/page";
import { SUMMARY, row, sourceRow, fleetSource, GONE } from "@/app/admin/outage-policy/fixtures";
import {
  ENGLISH_OUTAGE,
  classImpact,
  classLines,
  defaultGraceText,
  formatGrace,
  formatMinutes,
  modeLabel,
  parseGrace,
  readError,
  removeFailure,
  saveFailure,
  sourceImpact,
  type ClassChange,
} from "@/app/admin/outage-policy/outage";
import { ProfileEditor } from "@/app/admin/waf-sources/ProfileEditor";
import { SourcePicker } from "@/app/admin/outage-policy/SourcePicker";
import UsersError from "@/app/admin/users/error";
import UsersLoading from "@/app/admin/users/loading";
import { UsersPage } from "@/app/admin/users/UsersPage";
import { holds } from "@/components/auth/access";
import { FieldMappingForm, referenceKindLabel } from "@/components/fieldmapping/FieldMappingForm";
import { MappingHistory } from "@/components/fieldmapping/MappingHistory";
import { PropertyInput } from "@/components/fieldmapping/PropertyInput";
import { ValidationReport } from "@/components/fieldmapping/ValidationReport";
import { ValueMapEditor, outcomeLabel } from "@/components/fieldmapping/ValueMapEditor";
import {
  HISTORY,
  SCHEMA,
  catalog,
  fieldReport,
  profile,
  report,
} from "@/components/fieldmapping/fixtures";
import { RulesManager } from "@/components/fleet/RulesManager";
import { createTextTranslator } from "@/i18n/textTranslator";
import { ApiError, fleetApi, outagePolicyApi, api, type ClassificationRule } from "@/lib/api";
import type { BehaviorI18n } from "@/lib/behaviorI18n";
import {
  collectionState,
  conflictMessage,
  catalogNote,
  draftErrors,
  draftFromProfile,
  enableBlockReason,
  fieldStatusInfo,
  historyActionLabel,
  reasonText,
  validationState,
} from "@/lib/fieldMapping";
import { fleetTextOf } from "@/lib/fleetText";
import { patternHint, precedenceSteps, ruleFailure, ruleKindLabel, ruleLevelTitle, RULE_LEVELS } from "@/lib/fleet";
import en from "../../messages/en";
import tr from "../../messages/tr";

const TR: BehaviorI18n = { t: createTextTranslator("tr", tr as never), locale: "tr" };
const NBSP = " ";

const session = vi.hoisted(() => ({ value: { status: "ready", me: { permissions: [] as string[] } } }));
vi.mock("@/components/auth/session", () => ({ useSession: () => session.value }));

const auth = vi.hoisted(() => ({
  listUsers: vi.fn(),
  listRoles: vi.fn(),
  createUser: vi.fn(),
  setUserRoles: vi.fn(),
  disableUser: vi.fn(),
  enableUser: vi.fn(),
  unlockUser: vi.fn(),
  resetUserPassword: vi.fn(),
  getDirectory: vi.fn(),
  saveDirectory: vi.fn(),
  testDirectory: vi.fn(),
}));
vi.mock("@/components/auth/client", () => auth);
vi.mock("@/components/history/UndecidedNotice", () => ({ UndecidedNotice: () => null }));

beforeEach(() => {
  vi.restoreAllMocks();
  Object.values(auth).forEach((fn) => fn.mockReset());
  setTestLocale("tr");
  session.value = { status: "ready", me: { permissions: [] } };
});

function account(over: Record<string, unknown> = {}) {
  return {
    id: "1",
    username: "ada",
    auth_source: "LOCAL",
    display_name: "Ada Lovelace",
    email: "ada@example.com",
    roles: ["admin"],
    disabled: false,
    disabled_at: null,
    last_login_at: "2026-10-15T12:00:00Z",
    locked_until: null,
    must_change_password: false,
    ...over,
  };
}

describe("the administration index", () => {
  it("names every card in Turkish, and hides the cards the role cannot open", () => {
    session.value = { status: "ready", me: { permissions: [] } };
    render(<AdminIndex />);
    const list = screen.getByRole("list", { name: "Yönetim bölümleri" });
    expect(within(list).getByRole("link", { name: "Arama sıklığı" })).toHaveAttribute("href", "/admin/search-cadence");
    expect(within(list).getByRole("link", { name: "Sınıflama kuralları" })).toBeInTheDocument();
    expect(within(list).getByRole("link", { name: "Kesinti uyarıları" })).toBeInTheDocument();
    expect(within(list).getByRole("link", { name: "WAF kaynakları" })).toBeInTheDocument();
    expect(within(list).getByRole("link", { name: "IPS kaynakları" })).toBeInTheDocument();
    expect(within(list).queryByRole("link", { name: "Kullanıcılar" })).toBeNull();
    expect(within(list).queryByRole("link", { name: "Dizin" })).toBeNull();
    expect(list).toHaveTextContent("Hangi sessiz kaynakların uyarı ürettiği ve hangilerinin yalnızca kaydedildiği");
  });

  it("shows the permission-gated cards when the role holds them", () => {
    session.value = {
      status: "ready",
      me: { permissions: ["admin:users", "admin:auth", "read:history_backfill"] },
    };
    expect(holds(session.value.me.permissions, "admin:users")).toBe(true);
    render(<AdminIndex />);
    expect(screen.getByRole("link", { name: "Kullanıcılar" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Dizin" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Geçmiş veri okuma" })).toBeInTheDocument();
  });

  it("titles the page and its metadata in Turkish", async () => {
    render(<AdminPage />);
    expect(screen.getByRole("heading", { level: 1, name: "Yönetim" })).toBeInTheDocument();
    expect(await adminMetadata()).toEqual({ title: "Yönetim" });
  });
});

describe("users", () => {
  const roles = [
    { name: "admin", permissions: ["admin:*"] },
    { name: "analyst", permissions: [] },
    { name: "viewer", permissions: ["read:*"] },
  ];

  it("lists accounts with Turkish headings, status words and role labels (keys stay codes)", async () => {
    auth.listRoles.mockResolvedValue(roles);
    auth.listUsers.mockResolvedValue([
      account(),
      account({ id: "2", username: "bea", auth_source: "LDAP", display_name: null, roles: [], last_login_at: null }),
      account({ id: "3", username: "cy", roles: ["viewer", "retired"], disabled: true }),
      account({ id: "4", username: "dee", roles: ["analyst"], locked_until: "2999-01-01T00:00:00Z" }),
    ]);
    render(<UsersPage />);
    const table = await screen.findByRole("table");
    expect(screen.getByRole("heading", { level: 1, name: "Kullanıcılar" })).toBeInTheDocument();
    for (const name of ["Kullanıcı adı", "Kaynak", "Görünen ad", "Roller", "Durum", "Son oturum açma (UTC)", "İşlemler"]) {
      expect(within(table).getByRole("columnheader", { name })).toBeInTheDocument();
    }
    const ada = within(table).getByRole("row", { name: /ada/ });
    expect(ada).toHaveTextContent("etkin");
    expect(ada).toHaveTextContent("Yönetici (admin)");
    expect(ada).toHaveTextContent("2026-10-15 12:00:00 UTC");
    // A role the catalog does not list is shown as its own name, never as an empty cell.
    expect(within(table).getByRole("row", { name: /cy/ })).toHaveTextContent("Görüntüleyici (viewer), retired");
    expect(within(table).getByRole("row", { name: /cy/ })).toHaveTextContent("devre dışı");
    expect(within(table).getByRole("row", { name: /dee/ })).toHaveTextContent("kilitli");
    expect(within(table).getByRole("button", { name: "ada hesabını yönet" })).toBeInTheDocument();
  });

  it("opens the actions of a locked account with the unlock button and Turkish sentences", async () => {
    const user = userEvent.setup();
    auth.listRoles.mockResolvedValue(roles);
    auth.listUsers.mockResolvedValue([account({ locked_until: "2999-01-01T00:00:00Z", must_change_password: true })]);
    render(<UsersPage />);
    await user.click(await screen.findByRole("button", { name: "ada hesabını yönet" }));
    expect(screen.getByRole("heading", { name: "ada için işlemler" })).toBeInTheDocument();
    expect(screen.getByText("Bir sonraki oturum açmada zorunlu.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "ada hesabının kilidini aç" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "ada hesabını devre dışı bırak" })).toBeInTheDocument();
    expect(within(screen.getByRole("form", { name: "Roller" })).getByRole("checkbox", { name: /Yönetici \(admin\)/ })).toBeChecked();
  });

  it("says what it did after a role change and after a password mismatch", async () => {
    const user = userEvent.setup();
    auth.listRoles.mockResolvedValue(roles);
    auth.listUsers.mockResolvedValue([account({ roles: ["analyst"] })]);
    auth.setUserRoles.mockResolvedValue(account({ roles: ["admin", "viewer"] }));
    render(<UsersPage />);
    await user.click(await screen.findByRole("button", { name: "ada hesabını yönet" }));
    await user.click(screen.getByRole("button", { name: "ada için rolleri kaydet" }));
    expect(await screen.findByText("ada için roller artık: Yönetici (admin), Görüntüleyici (viewer).")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Yeni parola"), "a");
    await user.type(screen.getByLabelText("Yeni parolayı onayla"), "b");
    await user.click(screen.getByRole("button", { name: "ada için parolayı sıfırla" }));
    expect(screen.getByText("Parola sıfırlanmadı.")).toBeInTheDocument();
    expect(screen.getByText("Yeni parola ile onayı eşleşmiyor.")).toBeInTheDocument();
  });

  it("shows the one-time password sentence after creating a local account", async () => {
    const user = userEvent.setup();
    auth.listRoles.mockResolvedValue(roles);
    auth.listUsers.mockResolvedValue([account()]);
    auth.createUser.mockResolvedValue(account({ id: "9", username: "eda" }));
    render(<UsersPage />);
    await screen.findByRole("table");
    await user.type(screen.getByLabelText("Kullanıcı adı", { selector: "input[autocomplete=off]" }), "eda");
    await user.type(screen.getByLabelText("İlk parola"), "pw-12345678");
    await user.click(screen.getByRole("button", { name: "Hesap oluştur" }));
    expect(await screen.findByText("eda için ilk parola yalnızca bir kez gösterilir. Hesap, bir sonraki oturum açmada bunu değiştirmek zorundadır.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Parolayı gizle" })).toBeInTheDocument();
    expect(screen.getByText("Yerel kullanıcı eda oluşturuldu. Hesap, bir sonraki oturum açmada parolayı değiştirmek zorundadır.")).toBeInTheDocument();
  });

  it("is empty with a reason, forbidden with the permission, and failed with a way out", async () => {
    auth.listRoles.mockResolvedValue(roles);
    auth.listUsers.mockResolvedValueOnce([]);
    const empty = render(<UsersPage />);
    expect(await screen.findByText("Kayıtlı hesap yok.")).toBeInTheDocument();
    empty.unmount();

    auth.listUsers.mockRejectedValueOnce(new ApiError(403, "no"));
    const forbidden = render(<UsersPage />);
    expect(await screen.findByText("Rolünüz kullanıcıları yönetemiyor (izin: admin:users).")).toBeInTheDocument();
    forbidden.unmount();

    auth.listUsers.mockRejectedValueOnce(new ApiError(500, "boom"));
    render(<UsersPage />);
    expect(await screen.findByText("Kullanıcılar yüklenemedi.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Tekrar dene" })).toBeInTheDocument();
  });

  it("announces loading and the route error in Turkish", () => {
    render(<UsersLoading />);
    expect(screen.getByRole("status")).toHaveTextContent("kullanıcılar yükleniyor…");
    render(<UsersError error={Object.assign(new Error("x"), { digest: "d-7" })} reset={() => {}} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Kullanıcılar gösterilemedi.");
    expect(screen.getByRole("alert")).toHaveTextContent("Referans: d-7.");
  });
});

describe("directory", () => {
  const config = {
    enabled: true,
    urls: ["ldaps://dc1.example"],
    start_tls: false,
    ca_pem: null,
    bind_template: "cn={username},dc=example",
    timeout_seconds: 5,
    user_search_base: null,
    version: 1204,
    updated_by: "ada",
    updated_at: "2026-10-15T12:00:00Z",
  };

  it("shows the saved version with the Turkish number form, and the form labels", async () => {
    auth.getDirectory.mockResolvedValue(config);
    render(<DirectoryPage />);
    expect(await screen.findByText(/Sürüm 1\.204, son kayıt/)).toHaveTextContent(
      "Sürüm 1.204, son kayıt 2026-10-15 12:00:00 UTC, kaydeden ada.",
    );
    expect(screen.getByLabelText("Dizin ile oturum açma etkin")).toBeChecked();
    expect(screen.getByRole("button", { name: "URL ekle" })).toBeInTheDocument();
    expect(screen.getByLabelText("Zaman aşımı (saniye)")).toBeInTheDocument();
    expect(screen.getByText("Boş bırakılırsa sistem güven deposu kullanılır.")).toBeInTheDocument();
  });

  it("says nothing is saved yet", async () => {
    auth.getDirectory.mockResolvedValue({ ...config, urls: [], version: 0, updated_by: null, updated_at: null });
    render(<DirectoryPage />);
    expect(await screen.findByText("Henüz kaydedilmiş bir dizin yapılandırması yok.")).toBeInTheDocument();
  });

  it("words a test-connection success and a failure in Turkish, with the server's reason as sent", async () => {
    const user = userEvent.setup();
    auth.getDirectory.mockResolvedValue(config);
    auth.testDirectory.mockResolvedValueOnce({
      tls: { status: "ok", reason: null, trust: "system" },
      bind: { status: "ok", reason: null },
      certificate: { subject: "CN=dc1", issuer: "CN=ca", not_after: "2027-01-01T00:00:00Z" },
      latency_ms: 1234,
    });
    render(<DirectoryPage />);
    await screen.findByRole("heading", { name: "Bağlantıyı test et" });
    await user.type(screen.getByLabelText("Test kullanıcı adı"), "ada");
    await user.type(screen.getByLabelText("Test parolası"), "pw");
    await user.click(screen.getByRole("button", { name: "Bağlantıyı test et" }));
    expect(await screen.findByText("Bağlandı ve ada olarak bind işlemi yapıldı.")).toBeInTheDocument();
    expect(screen.getByText("Sistem güven deposu")).toBeInTheDocument();
    expect(screen.getByText("1.234 ms")).toBeInTheDocument();
    expect(screen.getByText("Sertifika bitiş tarihi (UTC)")).toBeInTheDocument();

    auth.testDirectory.mockResolvedValueOnce({
      tls: { status: "ok", reason: null, trust: "ca_pem" },
      bind: { status: "invalid_credentials", reason: "LDAP error 49: bad password" },
      certificate: null,
      latency_ms: 40,
    });
    await user.type(screen.getByLabelText("Test kullanıcı adı"), "ada");
    await user.type(screen.getByLabelText("Test parolası"), "pw");
    await user.click(screen.getByRole("button", { name: "Bağlantıyı test et" }));
    expect(await screen.findByText("TLS başarılı oldu. Bind işlemi başarısız oldu.")).toBeInTheDocument();
    expect(screen.getByText("Reddedildi")).toBeInTheDocument();
    expect(screen.getByText("— LDAP error 49: bad password")).toBeInTheDocument();
    expect(screen.getByText("Sertifika döndürülmedi.")).toBeInTheDocument();

    auth.testDirectory.mockRejectedValueOnce(new ApiError(502, "unreachable"));
    await user.type(screen.getByLabelText("Test kullanıcı adı"), "ada");
    await user.type(screen.getByLabelText("Test parolası"), "pw");
    await user.click(screen.getByRole("button", { name: "Bağlantıyı test et" }));
    expect(await screen.findByText("Bağlantı testi başarısız oldu.")).toBeInTheDocument();
  });

  it("covers forbidden, failed, conflict-free save and the boundaries", async () => {
    const user = userEvent.setup();
    auth.getDirectory.mockRejectedValueOnce(new ApiError(403, "no"));
    const forbidden = render(<DirectoryPage />);
    expect(await screen.findByText("Rolünüz dizini değiştiremiyor (izin: admin:auth).")).toBeInTheDocument();
    forbidden.unmount();

    auth.getDirectory.mockRejectedValueOnce(new ApiError(500, "boom"));
    const failed = render(<DirectoryPage />);
    expect(await screen.findByText("Dizin yapılandırması yüklenemedi.")).toBeInTheDocument();
    failed.unmount();

    auth.getDirectory.mockResolvedValue(config);
    auth.saveDirectory.mockResolvedValueOnce({ ...config, version: 1205 });
    render(<DirectoryPage />);
    await user.click(await screen.findByRole("button", { name: "Dizini kaydet" }));
    expect(await screen.findByText("Dizin yapılandırması sürüm 1.205 olarak kaydedildi.")).toBeInTheDocument();
    auth.saveDirectory.mockRejectedValueOnce(new ApiError(409, "version 1205 is current"));
    await user.click(screen.getByRole("button", { name: "Dizini kaydet" }));
    expect(await screen.findByText("Dizin yapılandırmasını başka biri değiştirdi.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Yeniden yükle" })).toBeInTheDocument();
  });

  it("announces loading and the route error in Turkish", () => {
    render(<DirectoryLoading />);
    expect(screen.getByRole("status")).toHaveTextContent("dizin yapılandırması yükleniyor…");
    render(<DirectoryError error={new Error("x")} reset={() => {}} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Dizin gösterilemedi.");
  });
});

function rule(over: Partial<ClassificationRule> = {}): ClassificationRule {
  return {
    id: "r1",
    instance_id: null,
    kind: "NAME_REGEX",
    pattern: "fw-.*",
    target_class: "FIREWALL",
    priority: 1200,
    enabled: true,
    built_in: false,
    description: "edge firewalls",
    created_by: "ada",
    created_at: "",
    updated_at: "",
    ...over,
  };
}

describe("classification rules", () => {
  const text = fleetTextOf(createTextTranslator("tr", tr as never), "tr");

  it("states the precedence, the counts and the level tables in Turkish", async () => {
    vi.spyOn(fleetApi, "rules").mockResolvedValue([rule(), rule({ id: "r2", built_in: true, kind: "GROUP", pattern: "12" })]);
    render(await ClassificationRulesPage());
    expect(screen.getByRole("heading", { level: 1, name: "Sınıflama kuralları" })).toBeInTheDocument();
    expect(screen.getByText(/2 kural; 1 yerleşik, 1 özel\./)).toBeInTheDocument();
    const steps = screen.getAllByRole("listitem").filter((li) => li.closest("ol"));
    expect(steps.map((li) => li.querySelector("strong")?.textContent)).toEqual([
      "Operatör geçersiz kılması",
      "QRadar dahili",
      "Grup kuralları",
      "Tür kuralları",
      "Ad kuralları",
      "Sınıflanmamış",
    ]);
    expect(screen.getByRole("heading", { name: "Karar sırası" })).toBeInTheDocument();
    // A level with no rule says what is empty and why, with the level name as a noun.
    expect(screen.getByText("Tür kuralları yok.")).toBeInTheDocument();
    expect(screen.getByText("Aşağıdan bir kural eklenene kadar kaynaklar bu düzeyde sınıflandırılmaz.")).toBeInTheDocument();
    const table = screen.getByRole("table", { name: /Ad kuralları/ });
    for (const name of ["Öncelik", "Desen", "Sınıf", "Köken", "Etkin", "İşlemler"]) {
      expect(within(table).getByRole("columnheader", { name })).toBeInTheDocument();
    }
    const body = within(table).getByRole("row", { name: /fw-/ });
    expect(body).toHaveTextContent("1.200");
    expect(body).toHaveTextContent("Güvenlik duvarı");
    expect(body).toHaveTextContent("Kaynak adı deseni · edge firewalls");
    expect(body).toHaveTextContent("Özel");
    expect(body).toHaveTextContent("Tüm instance'lar · ada");
    expect(body).toHaveTextContent("Açık");
  });

  it("explains a refused read: forbidden names the permission, expired and failed say what to do", async () => {
    const rules = vi.spyOn(fleetApi, "rules");
    rules.mockRejectedValueOnce(new ApiError(403, "no"));
    const first = render(await ClassificationRulesPage());
    expect(screen.getByRole("alert")).toHaveTextContent("Rolünüz sınıflama kurallarını okuyamıyor (izin: read:fleet). Hiçbir şey değiştirilmedi.");
    first.unmount();
    rules.mockRejectedValueOnce(new ApiError(401, "no"));
    const second = render(await ClassificationRulesPage());
    expect(screen.getByRole("alert")).toHaveTextContent("Oturumunuzun süresi doldu.");
    second.unmount();
    rules.mockRejectedValueOnce(new Error("down"));
    render(await ClassificationRulesPage());
    expect(screen.getByRole("alert")).toHaveTextContent("Backend'e erişilemiyor olabilir");
    expect(screen.getByRole("alert")).toHaveTextContent("Sınıflama kuralları gösterilemedi.");
  });

  it("words the editor, the create form and the write outcomes in Turkish", async () => {
    const user = userEvent.setup();
    const update = vi.spyOn(fleetApi, "updateRule").mockResolvedValue(rule({ enabled: false }));
    render(<RulesManager initial={[rule()]} />);
    await user.click(screen.getByRole("checkbox", { name: "Etkin: Kaynak adı deseni fw-.*" }));
    expect(update).toHaveBeenCalledWith("r1", { enabled: false });
    expect(await screen.findByText("Devre dışı bırakıldı. Kaynaklar yeniden sınıflandırılıyor.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Kaynak adı deseni fw-.* kuralını düzenle" }));
    expect(screen.getByRole("form", { name: "fw-.* kuralını düzenle" })).toBeInTheDocument();
    expect(screen.getAllByLabelText("Hedef sınıf").length).toBeGreaterThan(0);
    await user.click(screen.getByRole("button", { name: "Kuralı kaydet" }));
    expect(await screen.findByText("Kaydedildi. Kaynaklar yeniden sınıflandırılıyor.")).toBeInTheDocument();
    // The create form sits in a disclosure: its hint follows the kind.
    expect(screen.getByText("Düzenli ifade, en çok 256 karakter.")).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Eşleşen"), "GROUP");
    expect(screen.getByText("Sayısal bir kimlik.")).toBeInTheDocument();
    expect(screen.getByText("0 ile 10000 arası. Bir düzeyde en yüksek öncelik karar verir.")).toBeInTheDocument();
  });

  it("answers a refused write: forbidden with the permission, 422 as the API sent it, a generic failure translated", () => {
    expect(ruleFailure(new ApiError(403, "x"), text)).toEqual({
      kind: "forbidden",
      text: "Rolünüz sınıflama kurallarını değiştiremiyor (izin: admin:classification). Hiçbir şey değiştirilmedi.",
      field: null,
    });
    // The API's own sentence is never translated, and still marks the pattern field.
    expect(ruleFailure(new ApiError(422, "pattern is not a valid regular expression"), text)).toEqual({
      kind: "error",
      text: "pattern is not a valid regular expression",
      field: "pattern",
    });
    expect(ruleFailure(new ApiError(500, "x"), text).text).toBe(
      "Değiştirilmedi. Sunucu isteği tamamlayamadı. Kısa süre sonra yeniden deneyin.",
    );
    // Without a translator the English stays.
    expect(ruleFailure(new ApiError(403, "x")).text).toMatch(/^Your role cannot change classification rules/);
  });

  it("looks up kinds, levels, hints and steps; an unknown kind shows as sent, never empty", () => {
    expect(ruleKindLabel("TYPE_NAME_REGEX", text)).toBe("Tür adı deseni");
    expect(ruleKindLabel("FUTURE_KIND", text)).toBe("FUTURE_KIND");
    expect(ruleKindLabel("TYPE")).toBe("Type id");
    expect(RULE_LEVELS.map((l) => ruleLevelTitle(l, text))).toEqual(["Grup kuralları", "Tür kuralları", "Ad kuralları"]);
    expect(patternHint("GROUP", text)).toBe("Sayısal bir kimlik.");
    expect(patternHint("NAME_REGEX")).toBe("A regular expression, at most 256 characters.");
    expect(precedenceSteps(text)).toHaveLength(6);
    expect(precedenceSteps()[0].title).toBe("Operator override");
  });

  it("announces loading and the route error in Turkish", () => {
    render(<ClassificationLoading />);
    expect(screen.getByRole("status")).toHaveTextContent("sınıflama kuralları yükleniyor…");
    render(<ClassificationError error={new Error("x")} reset={() => {}} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Sınıflama kuralları gösterilemedi.");
  });

  it("titles the metadata in Turkish", async () => {
    expect(await classificationMetadata()).toEqual({ title: "Sınıflama kuralları" });
  });
});

describe("outage policy", () => {
  const change = (over: Partial<ClassChange> = {}): ClassChange => ({
    source_class: "FIREWALL",
    fromMode: "ALERT",
    fromGrace: null,
    toMode: "RECORD_ONLY",
    toGrace: null,
    covered: 1204,
    defaultGrace: "1 zaman dilimi (5 dk)",
    alertingOverrides: 2,
    recordOnlyOverrides: 0,
    removal: false,
    bucketSeconds: 300,
    ...over,
  });

  it("writes grace, minutes and counts in the Turkish form", () => {
    expect(formatGrace(6, 300, TR)).toBe(`6 zaman dilimi · 30${NBSP}dk`);
    expect(formatGrace(6, null, TR)).toBe("6 zaman dilimi");
    expect(formatMinutes(2.5, TR)).toBe(`2,5${NBSP}dk`);
    expect(formatMinutes(2.5)).toBe("2.5 min");
    expect(defaultGraceText(SUMMARY, TR)).toBe(`1 zaman dilimi (5${NBSP}dk)`);
    expect(modeLabel("RECORD_ONLY", TR)).toBe("Yalnızca kayıt");
    expect(modeLabel("RECORD_ONLY")).toBe("Record only");
    expect(parseGrace("x", TR)).toEqual({
      ok: false,
      message: "Tam sayı olarak zaman dilimi girin ya da varsayılan için boş bırakın.",
    });
    expect(parseGrace("x", ENGLISH_OUTAGE)).toEqual({
      ok: false,
      message: "Enter a whole number of buckets, or leave it blank for the default.",
    });
  });

  it("states the impact of a class change in Turkish, with grouped counts", () => {
    const stop = classImpact(change(), TR);
    expect(stop.known).toBe(true);
    expect(stop.lines).toEqual([
      "Güvenlik duvarı sınıfındaki 1.204 izlenen kaynak artık kesinti uyarısı üretmeyecek.",
      "2 kaynak, kendi geçersiz kılmaları sayesinde uyarı üretmeyi sürdürüyor.",
    ]);
    expect(classImpact(change({ covered: null }), TR)).toEqual({
      known: false,
      lines: [
        "Güvenlik duvarı sınıfı artık kesinti uyarısı üretmeyecek. Etkilenen kaynak sayısı bilinmiyor; çünkü filo özeti okunamadı.",
      ],
    });
    expect(classImpact(change({ toMode: null }), TR).lines[0]).toBe(
      "Varsayılan mod bildirilmedi; bu yüzden bu değişikliğin etkisi burada sayılamaz.",
    );
    expect(classImpact(change({ covered: 0 }), TR).lines[0]).toBe(
      "Güvenlik duvarı sınıfındaki hiçbir izlenen kaynağa bu satır karar vermiyor; bu yüzden hiçbir kaynağın davranışı değişmez.",
    );
    const grace = classImpact(change({ toGrace: 6, covered: 3 }), TR).lines.at(-1);
    expect(grace).toBe(`Sessiz bir kaynak, varsayılan tolerans, 1 zaman dilimi (5 dk) yerine 6 zaman dilimi · 30${NBSP}dk sonra sessiz sayılıyor.`);
    // English default is unchanged.
    expect(classImpact(change({ covered: 12 })).lines[0]).toBe(
      "12 monitored sources in Firewall will no longer raise outage alerts.",
    );
  });

  it("states the impact of a source override in Turkish", () => {
    const base = {
      name: "pa-fw-02",
      source_class: "FIREWALL" as const,
      classMode: "RECORD_ONLY" as const,
      fromMode: "RECORD_ONLY" as const,
      fromGrace: null,
      toMode: "ALERT" as const,
      toGrace: null,
      removal: false,
      bucketSeconds: 300,
      defaultGrace: null,
    };
    expect(sourceImpact(base, TR).lines).toEqual([
      "pa-fw-02 kesinti uyarısı üretecek.",
      "Güvenlik duvarı sınıfı Yalnızca kayıt olarak çözümleniyor; bu kaynak ondan farklı.",
    ]);
    expect(sourceImpact({ ...base, removal: true, toMode: "RECORD_ONLY" }, TR).lines).toEqual([
      "pa-fw-02 zaten Yalnızca kayıt olarak çözümleniyor; geçersiz kılmayı kaldırmak hiçbir modu değiştirmez.",
      "Yeniden Güvenlik duvarı sınıfı için geçerli modu izliyor.",
    ]);
    expect(sourceImpact({ ...base, removal: true, classMode: null, source_class: null }, TR).known).toBe(false);
  });

  it("words refusals: the permission stays a key, the API's validation sentence is verbatim", () => {
    expect(saveFailure(new ApiError(403, "x"), TR).text).toBe(
      "Rolünüz kesinti uyarısı politikasını değiştiremiyor (izin: admin:outage). Hiçbir şey değiştirilmedi.",
    );
    expect(saveFailure(new ApiError(422, "grace must be 0 to 48"), TR)).toMatchObject({
      text: "grace must be 0 to 48",
      field: "grace",
    });
    expect(saveFailure(new ApiError(500, "x"), TR).text).toMatch(/^Kaydedilmedi\. /);
    expect(removeFailure(new ApiError(404, "x"), TR)).toMatchObject({
      gone: true,
      text: "Bu satır başka biri tarafından zaten kaldırılmış. Liste yeniden yüklendi.",
    });
    expect(removeFailure(new ApiError(500, "x"), TR).text).toMatch(/^Kaldırılmadı\. /);
    expect(readError(new ApiError(403, "x"), TR)).toEqual({
      forbidden: true,
      text: "Rolünüz kesinti uyarısı politikasını okuyamıyor (izin: admin:outage). Okumak, değiştirmekle aynı izni gerektirir. Hiçbir şey değiştirilmedi.",
    });
    expect(readError(new ApiError(401, "x"), TR).text).toMatch(/^Oturumunuzun süresi doldu/);
    expect(readError(new Error("x"), TR).text).toMatch(/^Kesinti uyarısı politikası yüklenemedi/);
    expect(readError(new ApiError(403, "x")).text).toMatch(/^Your role cannot read the outage alert policy/);
  });

  it("orders classes by their Turkish labels", () => {
    const lines = classLines(SUMMARY, [row()], TR);
    expect(lines.map((l) => l.source_class)).toEqual(["FIREWALL", "LINUX", "PROXY"]);
  });

  it("renders the page populated, with the note, the class table and the source overrides", async () => {
    vi.spyOn(outagePolicyApi, "list").mockResolvedValue([row({ silence_grace_buckets: 6 }), sourceRow()]);
    vi.spyOn(outagePolicyApi, "summary").mockResolvedValue(SUMMARY);
    render(await OutagePolicyPage());
    expect(screen.getByRole("heading", { level: 1, name: "Kesinti uyarıları" })).toBeInTheDocument();
    expect(screen.getByText(/yalnızca kayıt/i, { selector: "strong" })).toBeInTheDocument();
    expect(document.body.textContent).toMatch(/\/behavior/);
    expect(screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent)).toEqual([
      "Sınıflar",
      "Kaynak bazlı geçersiz kılmalar",
    ]);
    const fw = screen.getByRole("row", { name: /^Güvenlik duvarı/ });
    expect(fw).toHaveTextContent("Yalnızca kayıt");
    expect(fw).toHaveTextContent("Sınıf satırından");
    expect(fw.textContent).toContain(`6 zaman dilimi30${NBSP}dk`);
    expect(screen.getByRole("button", { name: "Güvenlik duvarı için düzenle" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Linux için politika ayarla" })).toBeInTheDocument();
    const src = screen.getByRole("row", { name: /pa-fw-02/ });
    expect(src).toHaveTextContent("Uyarı");
    expect(src).toHaveTextContent("Varsayılan");
    expect(screen.getByRole("button", { name: "PaSeries @ pa-fw-02 geçersiz kılmasını kaldır" })).toBeInTheDocument();
  });

  it("is empty with a reason, and names the forbidden fleet summary", async () => {
    vi.spyOn(outagePolicyApi, "list").mockResolvedValue([]);
    vi.spyOn(outagePolicyApi, "summary").mockRejectedValue(new ApiError(403, "no"));
    render(await OutagePolicyPage());
    expect(screen.getByText("Kesinti politikası ayarlanmadı.")).toBeInTheDocument();
    expect(screen.getByText(/izin: read:fleet/)).toBeInTheDocument();
    expect(screen.getByText("Hiçbir sınıfın izlenen kaynağı yok.")).toBeInTheDocument();
  });

  it("shows a refused read as an alert naming the permission, and a failed summary", async () => {
    vi.spyOn(outagePolicyApi, "list").mockRejectedValue(new ApiError(403, "no"));
    vi.spyOn(outagePolicyApi, "summary").mockResolvedValue(SUMMARY);
    const first = render(await OutagePolicyPage());
    expect(screen.getByRole("alert")).toHaveTextContent("Kesinti uyarısı politikası rolünüze gösterilemiyor.");
    expect(screen.getByRole("alert")).toHaveTextContent("izin: admin:outage");
    first.unmount();
    vi.spyOn(outagePolicyApi, "list").mockResolvedValue([row()]);
    vi.spyOn(outagePolicyApi, "summary").mockRejectedValue(new Error("down"));
    render(await OutagePolicyPage());
    expect(screen.getByText(/Kaynak sayıları gösterilmiyor/)).toBeInTheDocument();
  });

  it("states the effect while editing, in Turkish", async () => {
    const user = userEvent.setup();
    vi.spyOn(outagePolicyApi, "summary").mockResolvedValue(SUMMARY);
    render(<OutagePolicyManager initialRows={[row({ mode: "ALERT" }), sourceRow({ mode: "ALERT" })]} initialSummary={SUMMARY} summaryNote={null} />);
    await user.click(screen.getByRole("button", { name: "Güvenlik duvarı için düzenle" }));
    await user.click(screen.getByRole("radio", { name: "Yalnızca kayıt" }));
    const impact = screen.getByRole("group", { name: "Kaydetmeden önce" });
    expect(impact).toHaveTextContent("Güvenlik duvarı sınıfındaki 12 izlenen kaynak artık kesinti uyarısı üretmeyecek.");
    expect(impact).toHaveTextContent("1 kaynak, kendi geçersiz kılmaları sayesinde uyarı üretmeyi sürdürüyor.");
    expect(screen.getByLabelText("Tolerans, zaman dilimi cinsinden")).toBeInTheDocument();
    expect(screen.getByText(/0 ile 48 arası\. Boş bırakılırsa varsayılan kullanılır\. Bir zaman dilimi 5/)).toBeInTheDocument();
    await user.type(screen.getByLabelText("Tolerans, zaman dilimi cinsinden"), "x");
    await user.click(screen.getByRole("button", { name: "Kaydet" }));
    expect(screen.getByText("Tam sayı olarak zaman dilimi girin ya da varsayılan için boş bırakın.")).toBeInTheDocument();
  });

  it("words the gone-source row and the removal question", async () => {
    const user = userEvent.setup();
    const gone = sourceRow({ scope_value: GONE, source_name: null, source_class: null });
    render(<OutagePolicyManager initialRows={[row(), gone]} initialSummary={SUMMARY} summaryNote={null} />);
    expect(screen.getByText("Kaynak artık envanterde yok")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /kaldır \(kimlik 5a1f0000\)/ }));
    expect(screen.getByRole("group", { name: "Kaldırmadan önce" })).toHaveTextContent(
      "Bu satır hiçbir kaynağı yönetmiyor: kaynağı artık envanterde yok. Kaldırmak hiçbir davranışı değiştirmez.",
    );
  });

  it("words the source picker: searching, a match list, a capped total with the Turkish form, and a failure", async () => {
    const user = userEvent.setup();
    const sources = vi.spyOn(fleetApi, "sources");
    sources.mockResolvedValue({ items: [fleetSource(), fleetSource({ id: "x2", name: "other", enabled: false, type_name: null })], total: 1204, limit: 25, offset: 0 } as never);
    render(<SourcePicker overridden={new Set(["x2"])} onChoose={() => {}} onEdit={() => {}} onClose={() => {}} />);
    expect(screen.getByRole("status")).toHaveTextContent("Log source'lar aranıyor…");
    expect(await screen.findByRole("table", { name: /Aramayla eşleşen log source/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "PaSeries @ pa-fw-02 kaynağını geçersiz kıl" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Geçersiz kılması var: other için düzenle" })).toBeInTheDocument();
    expect(screen.getByText("Devre dışı")).toBeInTheDocument();
    expect(screen.getByText("1.204 log source eşleşiyor; yalnızca ilk 2 log source gösteriliyor. Diğerlerini görmek için aramayı daraltın.")).toBeInTheDocument();
    sources.mockResolvedValue({ items: [], total: 0, limit: 25, offset: 0 } as never);
    await user.type(screen.getByLabelText("Ada ya da QRadar türüne göre ara"), "zz");
    expect(await screen.findByText("Adında ya da türünde “zz” geçen log source yok.")).toBeInTheDocument();
    sources.mockRejectedValue(new ApiError(500, "x"));
    await user.type(screen.getByLabelText("Ada ya da QRadar türüne göre ara"), "q");
    expect(await screen.findByText(/^Log source'lar listelenemedi\./)).toBeInTheDocument();
  });

  it("announces loading and the route error in Turkish, and titles the metadata", async () => {
    render(<OutageLoading />);
    expect(screen.getByRole("status")).toHaveTextContent("kesinti uyarısı politikası yükleniyor…");
    render(<OutageError error={new Error("x")} reset={() => {}} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Kesinti uyarısı politikası gösterilemedi.");
    expect(await outageMetadata()).toEqual({ title: "Kesinti uyarıları" });
  });
});

describe("field mapping helpers", () => {
  const state = (over: Record<string, unknown> = {}) => ({
    enabled: true,
    profile_version: 3,
    validation_status: "VALID" as const,
    validated_profile_version: 3,
    collection_eligible: true,
    ineligible_reason: null,
    ...over,
  });

  it("words a field status by code, with the Turkish number form", () => {
    const low = fieldStatusInfo(
      "INSUFFICIENT_COVERAGE",
      { populatedRatio: 0.318, plausibleRatio: null, requiredCoverage: 0.9, usedByCollection: true },
      TR,
    );
    expect(low).toMatchObject({ label: "Düşük coverage", tone: "crit" });
    expect(low.sentence).toBe("Olayların yalnızca %31,8 kadarı onu taşıyor; toplama için %90,0 gerekiyor.");
    // A missing share is never worded as 0 %.
    const none = fieldStatusInfo(
      "INSUFFICIENT_COVERAGE",
      { populatedRatio: null, plausibleRatio: null, requiredCoverage: null, usedByCollection: false },
      TR,
    );
    expect(none.sentence).toBe("Çok az olay onu taşıyor.");
    expect(fieldStatusInfo("NO_EVENTS", { populatedRatio: null, plausibleRatio: null, requiredCoverage: null, usedByCollection: true }, TR)).toMatchObject({
      label: "Karar yok",
      silent: true,
    });
    // A status this build does not know is shown as recorded.
    expect(fieldStatusInfo("FUTURE", { populatedRatio: null, plausibleRatio: null, requiredCoverage: null, usedByCollection: true }, TR).label).toBe("FUTURE");
    expect(fieldStatusInfo("AVAILABLE", { populatedRatio: 1, plausibleRatio: 1, requiredCoverage: 0.9, usedByCollection: true }).label).toBe("Usable");
  });

  it("words the validation and collection state, with the backend's pause reason as sent", () => {
    expect(validationState(state(), null, TR).label).toBe("Geçerli (v3)");
    expect(validationState(state({ validation_pending: true }), null, TR).label).toBe("Doğrulanıyor: v3…");
    expect(validationState(state({ validated_profile_version: 2 }), null, TR).label).toBe("Sürüm 3 doğrulanmadı");
    expect(
      validationState(
        state({ validation_status: "INVALID", fields: [{ field: "URL", property_name: "URL", status: "PROPERTY_REJECTED" }] }),
        { ...SCHEMA },
        TR,
      ).label,
    ).toBe("Geçersiz: URL path özelliği yok");
    expect(validationState(state({ validation_status: "INCONCLUSIVE", fields: [{ field: "URL", property_name: null, status: "NO_EVENTS" }] }), null, TR).label).toBe(
      "Sonuçsuz: pencerede olay yok",
    );
    expect(validationState(state(), null).label).toBe("Valid (v3)");
    expect(collectionState(state(), TR)).toEqual({ collecting: true, label: "Toplanıyor" });
    expect(collectionState(state({ enabled: false }), TR).label).toBe("Duraklatıldı");
    expect(collectionState(state({ collection_eligible: false, ineligible_reason: "mapping is not valid" }), TR).label).toBe(
      "Duraklatıldı — mapping is not valid",
    );
  });

  it("words why Enable is unavailable, and a conflict, in Turkish", () => {
    expect(enableBlockReason(state({ validated_profile_version: null }), TR)).toBe("Sürüm 3 doğrulanmadı. Önce doğrulayın.");
    expect(enableBlockReason(state(), TR)).toBeNull();
    expect(enableBlockReason(state({ validation_pending: true }), TR)).toMatch(/^Sürüm 3 hâlâ doğrulanıyor/);
    expect(conflictMessage({ version: 5, by: "ada", message: null }, TR)).toBe(
      "Siz düzenlerken bu eşleme değişti: sürüm 5 (ada tarafından). Düzenlemeleriniz aşağıda duruyor; onlarınkini görmek için yeniden yükleyin.",
    );
    expect(conflictMessage({ version: null, by: null, message: "server says no" }, TR)).toMatch(/^server says no\. Düzenlemeleriniz/);
  });

  it("words draft errors and catalog notes by code", () => {
    const draft = draftFromProfile(SCHEMA, profile({ mapping: { ...profile().mapping, URL: "", METHOD: "bad;name" } }));
    const errors = draftErrors(SCHEMA, draft, TR);
    expect(errors.URL).toBe("Zorunlu: toplama bu olmadan çalışamaz.");
    expect(errors.METHOD).toMatch(/^İzin verilen bir özellik adı değil\./);
    expect(catalogNote("nope", catalog(), "Generic WAF DSM", TR)?.text).toBe("Bu konsolun özellik listesinde yok. Doğrulama bunu denetleyecek.");
    expect(catalogNote("WAF_URL", catalog(), "Generic WAF DSM", TR)?.text).toBe(
      "Bu özelliğin, bu kaynağın log source türü (Generic WAF DSM) için bir ayrıştırma ifadesi yok.",
    );
    // Without a catalog nothing is said about the name.
    expect(catalogNote("nope", null, null, TR)).toBeNull();
  });

  it("words a backend reason through the status table, and an action by code; unknown ones as sent", () => {
    expect(reasonText("URL: PROPERTY_REJECTED (QRadar rejected the property)", SCHEMA, 0.9, undefined, TR)).toBe(
      "URL path: QRadar bu adda bir özellik tanımıyor.",
    );
    expect(reasonText("something else entirely", SCHEMA, 0.9, undefined, TR)).toBe("something else entirely");
    expect(historyActionLabel("waf.profile.validated", TR)).toBe("Doğrulandı");
    expect(historyActionLabel("waf.profile.future", TR)).toBe("waf.profile.future");
    expect(historyActionLabel("toString", TR)).toBe("toString");
    expect(outcomeLabel("BLOCKED", TR)).toBe("Engellendi");
    expect(outcomeLabel("BLOCKED")).toBe("Blocked");
    expect(outcomeLabel("QUARANTINED", TR)).toBe("QUARANTINED");
    expect(referenceKindLabel("CUSTOM_PROPERTY", TR)).toBe("Özel özellik");
    expect(referenceKindLabel("OTHER", TR)).toBe("OTHER");
  });
});

describe("field mapping components", () => {
  it("renders the mapping form in Turkish; the schema's own labels and descriptions stay as the API sent them", async () => {
    const user = userEvent.setup();
    const onRefresh = vi.fn().mockResolvedValue(undefined);
    render(
      <FieldMappingForm
        schema={SCHEMA}
        draft={draftFromProfile(SCHEMA, profile())}
        onChange={() => {}}
        catalog={catalog()}
        typeName="Generic WAF DSM"
        showRequired
        onRefreshCatalog={onRefresh}
        copyFrom={{ options: [], onCopy: async () => "" }}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent(
      "Bu konsolun özellik listesi, 2026-10-12 23:00:00 UTC tarihinde alındı. Generic WAF DSM için geçerli özellikler önce gelir.",
    );
    expect(screen.getAllByText("Zorunlu")).toHaveLength(2);
    expect(screen.getAllByText("İsteğe bağlı")).toHaveLength(3);
    // Backend-authored labels and descriptions are shown as sent.
    expect(screen.getByText("URL path")).toBeInTheDocument();
    expect(screen.getByText(SCHEMA.fields[0].description)).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "URL path özelliği" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Listeyi yenile" })).toBeInTheDocument();
    expect(screen.getByLabelText("Eşlemeyi şuradan kopyala…")).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Eşlemesi olan başka kaynak yok" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Action alanının eşlemesini kaldır" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Listeyi yenile" }));
    expect(onRefresh).toHaveBeenCalled();
  });

  it("says the property list is unavailable, with the server's detail as sent", () => {
    render(
      <FieldMappingForm
        schema={SCHEMA}
        draft={draftFromProfile(SCHEMA, profile())}
        onChange={() => {}}
        catalog={catalog({ status: "UNAVAILABLE", detail: "QRadar timed out" } as never)}
        typeName={null}
        showRequired={false}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent(
      "Özellik listesi kullanılamıyor: QRadar timed out Her adı QRadar'ın gösterdiği gibi yazın; doğrulama onu denetler.",
    );
  });

  it("counts the property list in the Turkish form and words the groups and the empty match", async () => {
    const user = userEvent.setup();
    const many = Array.from({ length: 1204 }, (_, i) => ({
      name: `Prop_${i}`,
      property_type: "STRING",
      applies_to_source_type: i % 2 === 0,
      name_allowed: i !== 1,
    }));
    function Harness() {
      const [value, setValue] = useState("");
      return (
        <PropertyInput id="p" label="URL path özelliği" value={value} suggestions={many as never} onChange={setValue} />
      );
    }
    render(<Harness />);
    await user.click(screen.getByRole("combobox"));
    expect(screen.getByText("1.204 özellik")).toBeInTheDocument();
    expect(screen.getByText("Bu kaynak türü için geçerli")).toBeInTheDocument();
    expect(screen.getByText("Diğer kaynak türleri")).toBeInTheDocument();
    expect(screen.getByRole("listbox", { name: "URL path özelliği için özellikler" })).toBeInTheDocument();
    await user.type(screen.getByRole("combobox"), "Prop_1");
    expect(screen.getByText(/^\d+ \/ 1\.204 özellik eşleşiyor$/)).toBeInTheDocument();
    await user.clear(screen.getByRole("combobox"));
    await user.type(screen.getByRole("combobox"), "zzzz");
    expect(screen.getByText(/“zzzz” için\. Yazdığınız ad yine de kullanılabilir/)).toBeInTheDocument();
  });

  it("words the validation report: result, window, facts, a missing measure, and the table", () => {
    render(
      <ValidationReport
        schema={SCHEMA}
        report={report({
          status: "INCONCLUSIVE",
          total_events: 12345,
          reasons: ["URL: UNPOPULATED (no event carried it)", "free text from the backend"],
          fields: [
            fieldReport({ populated_ratio: 0.318, distinct_values: 1204, plausible_ratio: null }),
            fieldReport({ field: "METHOD", status: "NO_EVENTS", populated_ratio: null, distinct_values: null, property_name: null }),
          ],
        })}
        profileVersion={5}
        pending={{ since: "2026-10-12T23:07:00Z" }}
        requiredCoverage={0.9}
      />,
    );
    expect(screen.getAllByRole("status")[0]).toHaveTextContent(
      "Sürüm 5, 23:07 UTC saatinden beri doğrulanıyor. Bu sayfa her 5 saniyede bir denetler.",
    );
    expect(screen.getByText(/Bu rapor 4 sürümüne ait; sürüm 5 doğrulanmadı\./)).toBeInTheDocument();
    expect(screen.getByText("Sonuçsuz", { selector: ".pill" })).toBeInTheDocument();
    expect(screen.getByText("12.345")).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Sonucun nedenleri" })).toHaveTextContent("URL path: Özellik var ama penceredeki hiçbir olay onu taşımadı.");
    expect(screen.getByRole("list", { name: "Sonucun nedenleri" })).toHaveTextContent("free text from the backend");
    const table = screen.getByRole("table");
    for (const name of ["Alan", "Sonuç", "Dolu olayların yüzdesi", "Benzersiz değerler", "Makul örneklenenlerin yüzdesi"]) {
      expect(within(table).getByRole("columnheader", { name })).toBeInTheDocument();
    }
    const url = within(table).getByRole("row", { name: /URL path/ });
    expect(url).toHaveTextContent("%31,8");
    expect(url).toHaveTextContent("1.204");
    expect(url).toHaveTextContent("toplama buna ihtiyaç duyuyor");
    // A missing measure is a dash that says so, never a zero.
    expect(within(url).getByTitle("Ölçülmedi")).toHaveTextContent("— ölçülmedi");
    expect(within(table).getByRole("row", { name: /HTTP method/ })).toHaveTextContent("eşlenmiş özellik yok");
  });

  it("says there is no report when none was run, and a pending run without a start time", () => {
    const { rerender } = render(
      <ValidationReport schema={SCHEMA} report={null} profileVersion={2} pending={null} requiredCoverage={null} />,
    );
    expect(screen.getByText(/^Sürüm 2 doğrulanmadı; bu yüzden rapor yok\./)).toBeInTheDocument();
    rerender(
      <ValidationReport schema={SCHEMA} report={null} profileVersion={2} pending={{ since: null }} requiredCoverage={null} />,
    );
    expect(screen.getByText("Sürüm 2 doğrulanıyor. Bu sayfa her 5 saniyede bir denetler.")).toBeInTheDocument();
  });

  it("words the value map editor: empty, truncated, rows, and the add control", () => {
    const { rerender } = render(
      <ValueMapEditor
        fieldLabel="Action"
        outcomes={["BLOCKED", "ALLOWED"]}
        discovered={null}
        truncated={false}
        map={{ BLOCKED: [], ALLOWED: [] }}
        onChange={() => {}}
      />,
    );
    expect(screen.getByText(/^Son doğrulama hiç action değeri listelemedi\./)).toBeInTheDocument();
    expect(screen.getByLabelText("Listede olmayan bir değer ekle")).toBeInTheDocument();
    rerender(
      <ValueMapEditor
        fieldLabel="Action"
        outcomes={["BLOCKED", "ALLOWED"]}
        discovered={[{ value: "BLOCK", events: 1204 }]}
        truncated
        map={{ BLOCKED: ["BLOCK", "OLD"], ALLOWED: ["OLD"] }}
        onChange={() => {}}
        blockedReason="Eşleme yok"
      />,
    );
    expect(screen.getByText(/^Bu liste kırpılmış: yalnızca en sık görülen action değerleri döndürüldü\./)).toBeInTheDocument();
    const table = screen.getByRole("table", { name: /Action değerleri, son doğrulama penceresindeki olay sayıları/ });
    expect(within(table).getByRole("row", { name: /BLOCK/ })).toHaveTextContent("1.204");
    expect(within(table).getAllByRole("radio", { name: "Engellendi" })).toHaveLength(2);
    expect(within(table).getAllByRole("radio", { name: "Eşlenmedi" }).length).toBeGreaterThan(0);
    expect(within(table).getByText("son doğrulamada görülmedi")).toBeInTheDocument();
    expect(within(table).getByText("iki listede de var; birini seçin")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ekle: izin verildi" })).toBeInTheDocument();
  });

  it("words the mapping history: empty, rows, changes and reasons; an unknown action as recorded", () => {
    const empty = render(<MappingHistory schema={SCHEMA} entries={[]} />);
    expect(screen.getByText(/^Bu profil için henüz değişiklik kaydedilmedi\./)).toBeInTheDocument();
    empty.unmount();
    render(
      <MappingHistory
        schema={SCHEMA}
        entries={[
          ...HISTORY,
          { at: "2026-10-12T20:00:00Z", actor: "", action: "waf.profile.future", profile_version: null, mapping_before: null, mapping_after: null, outcome: null, reasons: ["URL: PROPERTY_REJECTED (rejected)"] },
        ]}
      />,
    );
    const rows = screen.getAllByRole("row").slice(1);
    expect(screen.getByRole("columnheader", { name: "Zaman (UTC)" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Yapan" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Ne değişti" })).toBeInTheDocument();
    expect(rows[0]).toHaveTextContent("Doğrulandı · v4");
    expect(rows[0]).toHaveTextContent("Sonuç: Geçerli");
    expect(rows[1]).toHaveTextContent("Eşleme değişti");
    expect(within(screen.getByRole("list", { name: "Eşleme değişiklikleri" })).getByText(/URL path:/)).toHaveTextContent("URL path: request_uri → URL");
    expect(rows[2]).toHaveTextContent("waf.profile.future");
    expect(within(screen.getByRole("list", { name: "Nedenler" })).getByText(/URL path:/)).toHaveTextContent("QRadar bu adda bir özellik tanımıyor.");
  });
});

describe("the WAF profile editor, which embeds the field-mapping components", () => {
  it("shows the badges, the form, the report, the value map and the history in Turkish", () => {
    render(
      <ProfileEditor
        schema={SCHEMA}
        source={{ id: "ls-1", name: "WAF edge - eu-west", qradarId: 212, typeName: "Generic WAF DSM", instanceId: "inst-1" }}
        profile={profile({ validation_status: "INVALID", validation: { ...profile().validation!, status: "INVALID" }, validated_profile_version: 4, collection_eligible: false, ineligible_reason: "validation is INVALID" })}
        catalog={catalog()}
        history={HISTORY}
        siblings={[]}
        prefill={null}
      />,
    );
    // The header badges come from the converted helpers; the pause reason is the backend's sentence.
    expect(screen.getAllByText("Duraklatıldı — validation is INVALID").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Zorunlu").length).toBe(2);
    expect(screen.getByRole("columnheader", { name: "Benzersiz değerler" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Zaman (UTC)" })).toBeInTheDocument();
    expect(screen.getAllByRole("radio", { name: "Engellendi" }).length).toBeGreaterThan(0);
    // No English word of the field-mapping UI is left in the editor.
    const text = document.body.textContent ?? "";
    for (const word of ["Validation result per field", "Populated", "Not mapped", "Time (UTC)", "What changed", "Refresh list"]) {
      expect(text).not.toContain(word);
    }
  });
});

describe("catalog keys the wave added to shared namespaces", () => {
  it("has a Turkish label for every role the seed creates, and the same keys in English", () => {
    expect(Object.keys(tr.enums.role).sort()).toEqual(Object.keys(en.enums.role).sort());
    expect(Object.keys(tr.enums.role)).toEqual(["admin", "analyst", "viewer", "detection_engineer"]);
  });
});

void api;
