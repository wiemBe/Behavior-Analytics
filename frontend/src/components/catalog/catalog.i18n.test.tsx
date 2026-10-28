// The fleet and health catalog widgets in Turkish, in every state they have: loading, empty,
// failed, forbidden (with the permission), populated, and the honesty states. The Turkish number
// form is checked in a table, a figure and a count line. The English texts are pinned by
// catalog.test.tsx; here the English words of the lookups are checked against their sources.

import { render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setTestLocale } from "../../../vitest.setup";
import { failed, forbidden, loading, ok } from "@/components/home/testkit";
import { createTextTranslator } from "@/i18n/textTranslator";
import { fleetTextOf } from "@/lib/fleetText";
import { homeReads } from "@/lib/api";
import { fleetClass, fleet, page, summary } from "@/lib/home/fixtures";
import { MODE_LABELS } from "@/app/admin/outage-policy/outage";
import { FAMILY_INFO } from "@/lib/searchCadence";

import en from "../../../messages/en";

import tr from "../../../messages/tr";
import { CatchallSenders } from "./CatchallSenders";
import { cadence, coverage, outage, outageClass, sender } from "./fixtures";
import { cadenceRows, familyName, lateText } from "./derive";
import { FleetClasses } from "./FleetClasses";
import { HealthCollection } from "./HealthCollection";
import { HealthDetection } from "./HealthDetection";
import { HealthEvidence } from "./HealthEvidence";
import { OutagePolicy } from "./OutagePolicy";

const NB = " ";
const turkish = fleetTextOf(createTextTranslator("tr", tr), "tr");

const res = vi.hoisted(() => ({ byKey: {} as Record<string, unknown> }));
vi.mock("@/hooks/useApiResource", () => ({
  useApiResource: (key: string | null) => res.byKey[key ?? ""] ?? { status: "loading" },
}));

const NOW = Date.parse("2026-10-14T14:00:00Z");
beforeEach(() => {
  res.byKey = {};
  setTestLocale("tr");
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
});
afterEach(() => vi.useRealTimers());

/** A matcher for text that holds no-break spaces: `getByText` compares a string literally. */
const nb = (text: string) => new RegExp(`^${text.replace(/\u00a0/g, ".")}$`);
const link = (name: string) => screen.getByRole("link", { name });

describe("health.detection in Turkish", () => {
  const COV = homeReads.coverageSummary().key;
  const RULES = homeReads.ruleHealthSummary().key;

  it("leads with the covered share in the Turkish form, then the verdicts in words", () => {
    res.byKey = {
      [COV]: ok(coverage({ total_techniques: 2000, by_status: { COVERED: 500, MISSING: 1204, NOT_EVALUATED: 296 }, covered_ratio: 0.25 })),
      [RULES]: ok([
        { status: "HEALTHY", count: 1204 },
        { status: "NOISY", count: 7 },
        { status: "NEVER_OBSERVED", count: 31 },
        { status: "FUTURE_VERDICT", count: 2 },
      ]),
    };
    render(<HealthDetection />);
    expect(screen.getByRole("heading", { level: 2, name: /Algılama sağlığı/ })).toBeInTheDocument();
    expect(screen.getByText("%25,0")).toBeInTheDocument();
    expect(document.body.textContent).toContain("500 / 2.000 teknik kapsandı".replace(/ \/ /, `${NB}/${NB}`));
    expect(screen.getByText(/Diğer durumlar:/)).toHaveTextContent("EKSİK 1.204");
    expect(screen.getByText(/Diğer durumlar:/)).toHaveTextContent("DEĞERLENDİRİLMEDİ 296");
    const rules = screen.getByRole("table", { name: "Karara göre kural sağlığı" });
    expect(within(rules).getByRole("columnheader", { name: "Kural kararı" })).toBeInTheDocument();
    expect(within(rules).getByText("SAĞLIKLI").closest("tr")).toHaveTextContent("1.204");
    expect(within(rules).getByText("HİÇ GÖZLENMEDİ")).toBeInTheDocument();
    expect(within(rules).getByText("GÜRÜLTÜLÜ")).toBeInTheDocument();
    // A verdict this build does not know reads as the code, never empty.
    expect(within(rules).getByText("FUTURE VERDICT")).toBeInTheDocument();
    expect(link("Coverage")).toHaveAttribute("href", "/coverage");
  });

  it("says coverage was not evaluated instead of 0 %", () => {
    res.byKey = { [COV]: ok(coverage({ total_techniques: 0, by_status: {}, covered_ratio: 0 })), [RULES]: ok([]) };
    render(<HealthDetection />);
    expect(screen.getByText("Coverage değerlendirilmedi.")).toBeInTheDocument();
    expect(document.body).toHaveTextContent("Henüz hiçbir teknik eşlenmedi; bu yüzden bu, sıfır coverage bulgusu değildir.");
    expect(screen.queryByText(/%/)).toBeNull();
  });

  it("writes the rule half as a dash with the reason when rules are refused, and names the link", () => {
    res.byKey = { [COV]: ok(coverage()), [RULES]: forbidden };
    render(<HealthDetection />);
    expect(document.body).toHaveTextContent(/Kural sağlığı kullanılamıyor \(.+\)\./);
    expect(link("Kurallar")).toHaveAttribute("href", "/rules");
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });

  it("says no rule was synced, and shows the dash rather than a zero", () => {
    res.byKey = { [COV]: ok(coverage()), [RULES]: ok([]) };
    render(<HealthDetection />);
    expect(screen.getByText("Hiçbir kural eşitlenmedi; bu yüzden kural kararı yok.")).toBeInTheDocument();
    expect(screen.getByText("Tüm kurallar").closest("tr")).toHaveTextContent("—");
  });

  it("is polite loading text while the rules are read, and says what failed or was refused", () => {
    res.byKey = { [COV]: ok(coverage()), [RULES]: loading };
    const a = render(<HealthDetection />);
    expect(screen.getByRole("status")).toHaveTextContent("Kural sağlığı yükleniyor…");
    a.unmount();
    res.byKey = { [COV]: forbidden };
    const b = render(<HealthDetection />);
    expect(document.body).toHaveTextContent("izin: read:coverage");
    b.unmount();
    res.byKey = { [COV]: failed(500) };
    render(<HealthDetection />);
    expect(screen.getByRole("alert")).toHaveTextContent("algılama coverage'ı");
  });
});

describe("health.collection in Turkish", () => {
  const KEY = homeReads.searchCadence().key;
  const ranAt = (at: string) => ({ at, outcome: "ok", searches_sent: 1, buckets_collected: 1, split_count: 0 });

  it("shows the family names, the cadence with its unit and how late, in Turkish", () => {
    res.byKey = {
      [KEY]: ok([
        cadence(),
        cadence({ family: "WAF_COLLECTION", cadence_seconds: 900, last_run: ranAt("2026-10-14T12:00:00Z") }),
        cadence({ family: "NEW_FAMILY" as never, last_run: null }),
      ]),
    };
    render(<HealthCollection />);
    expect(screen.getByRole("heading", { level: 2, name: /Zamanlanmış aramalar/ })).toBeInTheDocument();
    expect(document.body.textContent).toContain(`1${NB}/${NB}3 aile gecikmeli`);
    expect(screen.getByText(/bazıları yükseltmeden beri çalışmadı/)).toBeInTheDocument();
    const rows = screen.getAllByRole("row");
    for (const name of ["Aile", "Sıklık", "Son çalıştırma", "Gecikme"]) {
      expect(screen.getByRole("columnheader", { name })).toBeInTheDocument();
    }
    expect(within(rows[1]).getByText("Phase A hacmi")).toBeInTheDocument();
    expect(within(rows[1]).getByText(nb(`15${NB}dk`))).toBeInTheDocument();
    expect(within(rows[1]).getByText("Zamanında")).toBeInTheDocument();
    expect(within(rows[2]).getByText("WAF toplama")).toBeInTheDocument();
    // Last ran 120 min ago, due after 15 min + 60 s: 1 h 44 min late.
    expect(within(rows[2]).getByText(nb(`1${NB}sa 44${NB}dk`))).toBeInTheDocument();
    // A family this build does not know keeps its code; one that never ran has a dash, not "on time".
    expect(within(rows[3]).getByText("NEW_FAMILY")).toBeInTheDocument();
    expect(within(rows[3]).queryByText("Zamanında")).toBeNull();
    expect(within(rows[3]).getAllByText("—").length).toBe(2);
  });

  it("explains an empty answer, and names the permission when it is refused", () => {
    res.byKey = { [KEY]: ok([]) };
    const a = render(<HealthCollection />);
    expect(screen.getByText(/Hiçbir arama ailesi döndürülmedi/)).toBeInTheDocument();
    a.unmount();
    res.byKey = { [KEY]: forbidden };
    render(<HealthCollection />);
    expect(document.body).toHaveTextContent("izin: read:search_cadence");
    expect(link("Sıklık")).toHaveAttribute("href", "/admin/search-cadence");
  });

  it("has a Turkish name for every search family, and the English names are the cadence table's", () => {
    for (const family of Object.keys(FAMILY_INFO)) {
      expect(familyName(family)).toBe(FAMILY_INFO[family as keyof typeof FAMILY_INFO].name);
      expect(familyName(family, turkish), family).not.toBe(familyName(family));
    }
    expect(familyName("UNKNOWN_FAMILY", turkish)).toBe("UNKNOWN_FAMILY");
  });

  it("words lateness with the unit words of the language, English unchanged", () => {
    const rows = cadenceRows([cadence({ last_run: ranAt("2026-10-14T12:00:00Z") })], NOW);
    expect(lateText(rows[0])).toBe("1 h 44 min");
    const turkishRows = cadenceRows([cadence({ last_run: ranAt("2026-10-14T12:00:00Z") })], NOW, turkish);
    expect(lateText(turkishRows[0], turkish)).toBe(`1${NB}sa 44${NB}dk`);
    expect(turkishRows[0].cadence).toBe(`15${NB}dk`);
  });
});

describe("health.evidence in Turkish", () => {
  const KEY = homeReads.anomalySummary().key;

  it("shows the pending and failed counts with a note on what to do", () => {
    res.byKey = { [KEY]: ok(summary({ evidence_pending: 1204, evidence_failed: 2 })) };
    render(<HealthEvidence />);
    expect(screen.getByRole("heading", { level: 2, name: /Kanıt toplama/ })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Kanıt durumu" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "Bekliyor" }).closest("tr")).toHaveTextContent("1.204");
    expect(screen.getByRole("cell", { name: "Başarısız" }).closest("tr")).toHaveTextContent("2");
    expect(screen.getByText("Başarısız anomaliler için kanıt toplanamadı; nedenini görmek için bunları açın.")).toBeInTheDocument();
    expect(link("Başarısız")).toHaveAttribute("href", "/anomalies?evidence_status=FAILED");
  });

  it("says nothing failed only when the count is a measured zero; names the permission when refused", () => {
    res.byKey = { [KEY]: ok(summary()) };
    const a = render(<HealthEvidence />);
    expect(screen.getByText("Hiçbir kanıt toplama başarısız olmadı.")).toBeInTheDocument();
    a.unmount();
    res.byKey = { [KEY]: forbidden };
    render(<HealthEvidence />);
    expect(document.body).toHaveTextContent("izin: read:anomalies");
  });
});

describe("table.catchall_senders in Turkish", () => {
  const KEY = homeReads.newCatchallSenders(200).key;

  it("lists the newest first with unknown for an unnamed count, and counts the Turkish way", () => {
    const items = Array.from({ length: 7 }, (_, i) =>
      sender({ id: `c${i}`, identifier: `10.0.0.${i}`, first_seen_at: `2026-10-14T0${i}:00:00Z`, events_24h: i === 6 ? null : 1100 + i }),
    );
    res.byKey = { [KEY]: ok(page(items, 1204)) };
    render(<CatchallSenders />);
    expect(screen.getByRole("heading", { level: 2, name: /İncelenecek catch-all gönderenleri/ })).toBeInTheDocument();
    expect(screen.getByText("1.204 yeni")).toBeInTheDocument();
    for (const name of ["Gönderen", "İlk görülme", /^Olay, 24.sa$/]) {
      expect(screen.getByRole("columnheader", { name })).toBeInTheDocument();
    }
    const body = screen.getAllByRole("row").slice(1);
    expect(within(body[0]).getByText("bilinmiyor")).toBeInTheDocument();
    expect(within(body[1]).getByText(/≥ 1\.105/)).toBeInTheDocument();
    expect(document.body.textContent).toContain(`En yeni 5${NB}/${NB}1.204 gösteriliyor.`);
    // Truncated read, said in words.
    expect(document.body).toHaveTextContent("Kırpılmış");
    expect(link("İncele")).toHaveAttribute("href", "/fleet/catchall");
  });

  it("says what an empty queue means, and names the permission when refused", () => {
    res.byKey = { [KEY]: ok(page([], 0)) };
    const a = render(<CatchallSenders />);
    expect(screen.getByText(/İncelenmemiş gönderen yok\. Catch-all kaynaklarını besleyen/)).toBeInTheDocument();
    a.unmount();
    res.byKey = { [KEY]: forbidden };
    render(<CatchallSenders />);
    expect(document.body).toHaveTextContent("izin: read:fleet");
  });

  it("is polite loading text with the drill-down link already there", () => {
    render(<CatchallSenders />);
    expect(screen.getByRole("status")).toHaveTextContent("catch-all gönderenleri yükleniyor…".replace(/^c/, "C"));
    expect(link("İncele")).toBeInTheDocument();
  });
});

describe("table.fleet_classes in Turkish", () => {
  const KEY = homeReads.fleetSummary("24h").key;

  it("sorts by mean EPS, words the classes and writes a missing mean as a dash", () => {
    res.byKey = {
      [KEY]: ok(
        fleet([
          fleetClass("FIREWALL", { mean_eps: 1234.5, open_incidents: { NO_EVENTS: 2 }, sources: { enabled: 1204, disabled: 0 } }),
          fleetClass("PROXY", { mean_eps: null, events: null }),
          fleetClass("DNS", { mean_eps: 55.5 }),
          fleetClass("EMAIL", { monitored_sources: 0 }),
        ]),
      ),
    };
    render(<FleetClasses />);
    expect(screen.getByRole("heading", { level: 2, name: /Kaynak sınıfları/ })).toBeInTheDocument();
    for (const name of ["Sınıf", "Kaynaklar", "Ortalama EPS", "Sessiz"]) {
      expect(screen.getByRole("columnheader", { name })).toBeInTheDocument();
    }
    const rows = screen.getAllByRole("row").slice(1);
    expect(within(rows[0]).getByText("Güvenlik duvarı")).toBeInTheDocument();
    expect(within(rows[0]).getByText("1.234,50")).toBeInTheDocument();
    expect(within(rows[0]).getByText("1.204")).toBeInTheDocument();
    expect(within(rows[1]).getByText("DNS")).toBeInTheDocument();
    expect(within(rows[1]).getByText("55,50")).toBeInTheDocument();
    expect(within(rows[3]).getByText("Proxy").closest("tr")!.textContent).toContain("—");
    // A class with no monitored source has no silence figure.
    expect(within(rows[2]).getAllByRole("cell")[3]).toHaveTextContent("—");
    expect(link("Filo")).toHaveAttribute("href", "/fleet?window=24h");
  });

  it("qualifies incomplete buckets as partial and bounds the list in words", () => {
    const classes = (["DNS", "VPN", "WAF", "IPS", "PROXY", "EMAIL", "CLOUD", "LINUX", "WINDOWS", "OTHER"] as const).map((c, i) =>
      fleetClass(c, { mean_eps: 10 + i, buckets: { expected: 48, complete: 40, complete_from_history: 0, partial: 4, missing: 4 } }),
    );
    res.byKey = { [KEY]: ok(fleet(classes)) };
    render(<FleetClasses />);
    expect(document.body.textContent).toContain(`Kısmi`);
    expect(document.body.textContent).toContain(`En yoğun 8 sınıf gösteriliyor; toplam 10.`);
  });

  it("explains a fleet with no classified source, and names the permission when refused", () => {
    res.byKey = { [KEY]: ok(fleet([])) };
    const a = render(<FleetClasses />);
    expect(screen.getByText("Henüz kaynağı olan bir kaynak sınıfı yok. Sınıflandırmak için log source'ları eşitleyin.")).toBeInTheDocument();
    a.unmount();
    res.byKey = { [KEY]: forbidden };
    render(<FleetClasses />);
    expect(document.body).toHaveTextContent("izin: read:fleet");
  });
});

describe("table.outage_policy in Turkish", () => {
  const KEY = homeReads.outageSummary("24h").key;

  it("has a Turkish word for each outage mode, and the English is the policy page's own", () => {
    expect(en.catalog.outage.mode.ALERT).toBe(MODE_LABELS.ALERT);
    expect(en.catalog.outage.mode.RECORD_ONLY).toBe(MODE_LABELS.RECORD_ONLY);
    expect(tr.catalog.outage.mode.ALERT).not.toBe(MODE_LABELS.ALERT);
    expect(tr.catalog.outage.mode.RECORD_ONLY).not.toBe(MODE_LABELS.RECORD_ONLY);
  });

  it("leads with the default in words, and lists only the classes that depart from it", () => {
    res.byKey = {
      [KEY]: ok(
        outage([
          outageClass("DNS"),
          outageClass("VPN", { outage_mode: "RECORD_ONLY" }),
          outageClass("WAF", { outage_overrides: 1204 }),
        ], { default_silence_grace_buckets: 6, bucket_seconds: 330 }),
      ),
    };
    render(<OutagePolicy />);
    expect(screen.getByRole("heading", { level: 2, name: /Kesinti politikası/ })).toBeInTheDocument();
    expect(screen.getByText(/varsayılan olarak, sessizlik toleransı 6 zaman dilimi \(33.dk\)/).closest("p")).toHaveTextContent(/^Uyarı/);
    expect(document.body).toHaveTextContent("1.204 kaynak bazlı geçersiz kılma");
    expect(document.body.textContent).toContain(" · 1 sınıf yalnızca kayıt tutuyor");
    const table = screen.getByRole("table", { name: "Varsayılan kesinti politikasından ayrılan sınıflar" });
    for (const name of ["Sınıf", "Mod", "Geçersiz kılmalar"]) {
      expect(within(table).getByRole("columnheader", { name })).toBeInTheDocument();
    }
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText("WAF")).toBeInTheDocument();
    expect(within(rows[0]).getByText("Uyarı")).toBeInTheDocument();
    expect(within(rows[1]).getByText("Yalnızca kayıt")).toBeInTheDocument();
  });

  it("writes the grace minutes with a decimal comma, and the buckets alone when the width is unknown", () => {
    res.byKey = { [KEY]: ok(outage([outageClass("DNS")], { default_silence_grace_buckets: 1, bucket_seconds: 450 })) };
    const a = render(<OutagePolicy />);
    expect(document.body.textContent).toContain(`1 zaman dilimi (7,5${NB}dk)`);
    a.unmount();
    res.byKey = { [KEY]: ok(outage([outageClass("DNS")], { default_silence_grace_buckets: 3, bucket_seconds: null as never })) };
    render(<OutagePolicy />);
    expect(document.body.textContent).toContain("sessizlik toleransı 3 zaman dilimi");
    expect(document.body.textContent).not.toContain("(");
  });

  it("says no class departs from the default only when none does", () => {
    res.byKey = { [KEY]: ok(outage([outageClass("DNS")])) };
    render(<OutagePolicy />);
    expect(screen.getByText("Hiçbir sınıf varsayılandan ayrılmıyor ve hiçbir kaynak onu geçersiz kılmıyor.")).toBeInTheDocument();
  });

  it("shows the dash with its reason when the default mode was not returned, and counts nothing without classes", () => {
    res.byKey = { [KEY]: ok(outage([outageClass("DNS")], { outage_default: undefined as never })) };
    const a = render(<OutagePolicy />);
    expect(screen.getAllByText(/Varsayılan kesinti modu döndürülmedi/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/varsayılan olarak/)).toBeNull();
    a.unmount();
    res.byKey = { [KEY]: ok(outage([])) };
    render(<OutagePolicy />);
    expect(screen.getByText(/Henüz kaynak sınıfı yok; bu yüzden sayılacak geçersiz kılma yok\./)).toBeInTheDocument();
    expect(screen.queryByText(/geçersiz kılma$/)).toBeNull();
  });

  it("names the permission when refused", () => {
    res.byKey = { [KEY]: forbidden };
    render(<OutagePolicy />);
    expect(document.body).toHaveTextContent("izin: read:fleet");
    expect(link("Politika")).toHaveAttribute("href", "/admin/outage-policy");
  });
});
