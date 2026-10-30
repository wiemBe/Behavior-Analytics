import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createTranslator } from "next-intl";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setTestLocale } from "../../../vitest.setup";
import SearchCadenceError from "@/app/admin/search-cadence/error";
import Loading from "@/app/admin/search-cadence/loading";
import SearchCadencePage, { generateMetadata } from "@/app/admin/search-cadence/page";
import { pwLatency } from "@/components/propertywatch/pwFormat";
import { asTextTranslator } from "@/i18n/textTranslator";
import { ApiError, api, type SearchCadenceItem } from "@/lib/api";
import type { BehaviorI18n } from "@/lib/behaviorI18n";

import en from "../../../messages/en";
import tr from "../../../messages/tr";
import { CadenceLatency, delayText, latencyParts, partsText } from "./CadenceLatency";
import { SearchCadenceTable } from "./SearchCadenceTable";

const NB = " ";
const ALLOWED = [300, 900, 1800, 3600, 7200, 14400, 28800];

function say(locale: "en" | "tr"): BehaviorI18n {
  const translate = createTranslator({ locale, messages: (locale === "tr" ? tr : en) as never });
  return { t: asTextTranslator(translate), locale };
}

function item(over: Partial<SearchCadenceItem> = {}): SearchCadenceItem {
  return {
    family: "PHASE_A_VOLUME",
    cadence_seconds: 3600,
    allowed_cadences_seconds: ALLOWED,
    bucket_seconds: 300,
    settle_seconds: 120,
    last_slot_start: "2026-10-12T09:00:00Z",
    next_dispatch_at: "2026-10-12T10:00:00Z",
    updated_at: "2026-10-12T08:00:00Z",
    updated_by: null,
    enabled: true,
    last_run: {
      at: "2026-10-12T09:00:20Z",
      outcome: "ok",
      searches_sent: 1204,
      buckets_collected: 12,
      split_count: 0,
    },
    ...over,
  };
}

beforeEach(() => {
  vi.restoreAllMocks();
  setTestLocale("tr");
});

describe("the search cadence table in Turkish", () => {
  it("words the headers, the families, the options, the delay and the notes", () => {
    render(
      <SearchCadenceTable
        items={[
          item(),
          item({ family: "ANOMALY_EXPLANATION", bucket_seconds: null, last_run: null }),
          item({ family: "RULE_MATCH", last_run: { at: "2026-10-12T09:00:20Z", outcome: "no-instance", searches_sent: null, buckets_collected: null, split_count: null } as never }),
          item({ family: "WAF_COLLECTION", last_run: { at: "2026-10-12T09:00:20Z", outcome: "brand-new", searches_sent: 0, buckets_collected: 0, split_count: 0 } }),
        ]}
      />,
    );
    const table = screen.getByRole("table");
    for (const name of ["Aile", "Sıklık", /Zaman dilimi/, /En kötü durum algılama gecikmesi/, "Son çalıştırma (UTC)", "Sonraki çalıştırma (UTC)", "Son çalıştırmadaki arama"]) {
      expect(within(table).getByRole("columnheader", { name })).toBeInTheDocument();
    }
    expect(screen.getByText("— = zaman dilimi yok")).toBeInTheDocument();
    expect(screen.getByText("sıklık + zaman dilimi + yerleşme payı")).toBeInTheDocument();
    expect(screen.getByText("Phase A hacmi")).toBeInTheDocument();
    expect(screen.getByText("Log source başına olay sayısı")).toBeInTheDocument();
    const select = screen.getByRole("combobox", { name: "Phase A hacmi için sıklık" });
    expect(select).toHaveValue("3600");
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual(
      ["5 dk", "15 dk", "30 dk", "1 sa", "2 sa", "4 sa", "8 sa"].map((x) => x.replace(" ", NB)),
    );
    const phase = screen.getByRole("row", { name: /Phase A hacmi/ });
    expect(phase.textContent).toContain(`en fazla 1${NB}sa 7${NB}dk`);
    expect(within(phase).getByText("Tamamlandı")).toHaveClass("pill", "ok");
    expect(within(phase).getByText("1.204")).toBeInTheDocument();
    expect(phase.textContent).toContain("10-12 09:00 UTC");
    const anomalies = screen.getByRole("row", { name: /Anomali açıklamaları/ });
    expect(anomalies.textContent).toContain(`kanıt en fazla 1${NB}sa 2${NB}dk`);
    expect(within(anomalies).getByText("Yükseltmeden beri çalışmadı")).toBeInTheDocument();
    expect(within(anomalies).getByText("Henüz çalışmadı")).toBeInTheDocument();
    expect(within(anomalies).getByTitle("Bu ailenin zaman dilimi yok")).toHaveTextContent("—");
    expect(within(anomalies).getByTitle("Bu aile hiç çalışmadı ve ileri bir çalıştırma zamanı yoktur.")).toHaveTextContent("—");
    const rules = screen.getByRole("row", { name: /Kural eşleşmeleri/ });
    expect(within(rules).getByText("Etkin sistem yok")).toBeInTheDocument();
    expect(within(rules).getByText("sayılmadı")).toHaveAttribute("title", "Bu aile aramalarını henüz saymıyor");
    // An outcome this build does not know is shown as recorded, in code.
    expect(within(screen.getByRole("row", { name: /WAF toplama/ })).getByText("brand-new").tagName).toBe("CODE");
    expect(screen.getByRole("list", { name: "Tablonun okunuşu" })).toHaveTextContent("İkisi de sıfır değildir.");
    expect(table.textContent).not.toMatch(/Cadence|Bucket|Not run|min\b/);
  });

  it("confirms a change with who and when, and measures the time in UTC", async () => {
    vi.spyOn(api, "setSearchCadence").mockResolvedValue(
      item({ cadence_seconds: 900, updated_by: "ada", updated_at: "2026-10-12T10:30:00Z" }),
    );
    render(<SearchCadenceTable items={[item()]} />);
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Phase A hacmi için sıklık" }), "900");
    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent("Kaydedildi. ada tarafından 2026-10-12 10:30:00 UTC zamanında değiştirildi.");
  });

  it("says who changed it only when the API says", async () => {
    vi.spyOn(api, "setSearchCadence").mockResolvedValue(item({ cadence_seconds: 900, updated_by: null, updated_at: "2026-10-12T10:30:00Z" }));
    render(<SearchCadenceTable items={[item()]} />);
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Phase A hacmi için sıklık" }), "900");
    expect(await screen.findByRole("status")).toHaveTextContent("Kaydedildi. 2026-10-12 10:30:00 UTC zamanında değiştirildi.");
  });

  it("reverts the select and names the permission on a refusal", async () => {
    vi.spyOn(api, "setSearchCadence").mockRejectedValue(new ApiError(403, "no"));
    render(<SearchCadenceTable items={[item()]} />);
    const select = screen.getByRole("combobox", { name: "Phase A hacmi için sıklık" });
    await userEvent.selectOptions(select, "900");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Rolünüz arama sıklığını değiştiremiyor (izin: admin:search_cadence). Hiçbir şey değiştirilmedi.");
    expect(alert.querySelector("code.perm")).toHaveTextContent("admin:search_cadence");
    await waitFor(() => expect(select).toHaveValue("3600"));
  });

  it("words a failed change", async () => {
    vi.spyOn(api, "setSearchCadence").mockRejectedValue(new ApiError(500, "x"));
    render(<SearchCadenceTable items={[item()]} />);
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Phase A hacmi için sıklık" }), "900");
    expect(await screen.findByRole("alert")).toHaveTextContent("Değiştirilmedi. Sunucu isteği tamamlayamadı.");
  });
});

describe("the search cadence page in Turkish", () => {
  it("titles the page and words the metadata", async () => {
    vi.spyOn(api, "searchCadence").mockResolvedValue([item()]);
    render(await SearchCadencePage());
    expect(await generateMetadata()).toEqual({ title: "Arama sıklığı" });
    expect(screen.getByRole("heading", { level: 1, name: "Arama sıklığı" })).toBeInTheDocument();
    expect(screen.getAllByText(/Saatler UTC'dir\./)).toHaveLength(2); // the description and the table caption
  });

  it("names the permission when the read is refused, and says the session expired", async () => {
    vi.spyOn(api, "searchCadence").mockRejectedValue(new ApiError(403, "no"));
    const first = render(await SearchCadencePage());
    expect(screen.getByRole("alert")).toHaveTextContent("Arama sıklığı gösterilemedi.");
    expect(screen.getByRole("alert")).toHaveTextContent("Rolünüz arama sıklığını okuyamıyor (izin: read:search_cadence). Hiçbir şey değiştirilmedi.");
    first.unmount();
    vi.spyOn(api, "searchCadence").mockRejectedValue(new ApiError(401, "no"));
    const second = render(await SearchCadencePage());
    expect(screen.getByRole("alert")).toHaveTextContent("Oturumunuzun süresi doldu. Devam etmek için yeniden giriş yapın.");
    second.unmount();
    vi.spyOn(api, "searchCadence").mockRejectedValue(new Error("down"));
    render(await SearchCadencePage());
    expect(screen.getByRole("alert")).toHaveTextContent("Arama sıklığı yüklenemedi. Backend'e erişilemiyor olabilir");
  });

  it("says what is empty and why", async () => {
    vi.spyOn(api, "searchCadence").mockResolvedValue([]);
    render(await SearchCadencePage());
    expect(screen.getByRole("alert")).toHaveTextContent("Hiçbir arama ailesi döndürülmedi.");
    expect(screen.getByRole("alert")).toHaveTextContent("migration");
  });

  it("keeps the boundaries in Turkish", async () => {
    const reset = vi.fn();
    const first = render(<SearchCadenceError error={Object.assign(new Error("x"), { digest: "d-3" })} reset={reset} />);
    expect(screen.getByRole("heading", { level: 1, name: "Arama sıklığı" })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Arama sıklığı gösterilemedi.");
    expect(screen.getByRole("alert")).toHaveTextContent("Referans: d-3.");
    await userEvent.click(screen.getByRole("button", { name: "Tekrar dene" }));
    expect(reset).toHaveBeenCalled();
    first.unmount();
    render(<Loading />);
    expect(screen.getByRole("status")).toHaveTextContent("Arama sıklığı yükleniyor…");
  });
});

describe("the latency sentence in Turkish", () => {
  const TR = say("tr");
  const known = { cadenceSeconds: 900, bucketSeconds: 300, settleSeconds: 120 };

  it("builds the sentence by code, with the delay in the locale's units", () => {
    expect(delayText(known, TR)).toBe(`en fazla 22${NB}dk`);
    const parts = latencyParts("Özellik izlemeleri", known, "Kapanış.", TR);
    expect(parts.lead).toBe(
      `Özellik izlemeleri, olaylar geldikten sonra en fazla 22${NB}dk içinde, toplanmış 5 dakikalık zaman dilimlerinde değerlendirilir (zamanlama şurada ayarlanır: `,
    );
    expect(parts.tail).toBe("). Kapanış.");
    expect(partsText(parts, TR)).toContain("Arama sıklığı). Kapanış.");
    expect(latencyParts("X", null, "Z.", TR).lead).toContain("ailelerinin her toplamasından sonra");
    expect(delayText(null, TR)).toBeNull();
  });

  it("keeps English for a caller with no translator (the property-watch pages)", () => {
    expect(latencyParts("Watches", known, "Done.").lead).toBe(
      "Watches are judged on collected 5-minute buckets, up to 22 min after the events arrive (the schedule is set on ",
    );
    expect(partsText(pwLatency(known))).toContain("Search cadence). ");
  });

  it("renders the link in Turkish when the reader can open the page, plain text otherwise", () => {
    const parts = latencyParts("Özellik izlemeleri", known, "Kapanış.", TR);
    const first = render(<CadenceLatency parts={parts} canLink className="x" />);
    expect(screen.getByRole("link", { name: "Arama sıklığı" })).toHaveAttribute("href", "/admin/search-cadence");
    first.unmount();
    render(<CadenceLatency parts={parts} canLink={false} className="x" />);
    expect(screen.queryByRole("link")).toBeNull();
    expect(document.body).toHaveTextContent("(zamanlama şurada ayarlanır: Arama sıklığı). Kapanış.");
  });
});
