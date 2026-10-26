// I18N-4: the behavior routes, the anomaly routes and their helpers in Turkish. English is pinned by
// the existing tests of each file; these cover Turkish per state, the Turkish number form, and the
// code-to-message lookups (an unknown code shows itself, never an empty string).

import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setTestLocale } from "../../vitest.setup";
import AnomalyDetailPage from "@/app/anomalies/[id]/page";
import AnomaliesError from "@/app/anomalies/error";
import AnomaliesPage from "@/app/anomalies/page";
import BehaviorError from "@/app/behavior/error";
import BehaviorPage from "@/app/behavior/page";
import SourceDetailPage from "@/app/behavior/sources/[id]/page";
import SourcesPage from "@/app/behavior/sources/page";
import { Evidence } from "@/components/Evidence";
import { ApiError, api, type AnomalySummary, type BehaviorSummary, type SourceBehavior } from "@/lib/api";
import { dimensionLabel, dimensionMeaningIn, stateMeaning, stateMeaningIn } from "@/lib/behavior";
import { buildAttentionRows } from "@/lib/behaviorOverview";
import { ENGLISH_BEHAVIOR, wordOrValue, type BehaviorI18n } from "@/lib/behaviorI18n";
import { summarizeAnomaly } from "@/lib/summarize";
import { createTextTranslator } from "@/i18n/textTranslator";
import en from "../../messages/en";
import tr from "../../messages/tr";

const TR: BehaviorI18n = { t: createTextTranslator("tr", tr as never), locale: "tr" };

const summary = (over: Partial<BehaviorSummary> = {}): BehaviorSummary => ({
  open_anomalies: 1234, spikes: 1, drops: 0, silent_sources: 0, candidates: 0, recovering: 0,
  insufficient_data_sources: 2, monitored_sources: 5, evidence_pending: 0, evidence_failed: 1,
  sources_with_evaluation_faults: 0, buckets_not_judged: 0, resolved_last_24h: 0,
  recently_resolved: [], highest_deviation: [], ...over,
});
const source = (over: Partial<SourceBehavior> = {}): SourceBehavior => ({
  log_source_id: "s-1", name: "LAB Firewall", criticality: "HIGH", observed_eps: 1234.5, expected_eps: 2,
  expected_low: 1, expected_high: 3, deviation_ratio: 3, state: "OPEN", baseline_sample_count: 30,
  baseline_completeness: 0.9, last_bucket_at: "2026-07-20T10:00:00Z", last_event_at: "2026-07-20T10:04:00Z",
  open_anomaly_count: 1, ...over,
});
const anomaly = (over: Partial<AnomalySummary> = {}): AnomalySummary => ({
  id: "a-1", log_source_id: "s-1", log_source_name: "LAB Firewall", anomaly_type: "VOLUME_SPIKE", state: "OPEN",
  severity: "HIGH", observed_value: 6, expected_value: 2, deviation_ratio: 3, robust_z: 1, absolute_delta: 4,
  consecutive_buckets: 2, confidence: 0.8, detected_at: "2026-07-20T10:05:00Z", opened_at: null,
  anomaly_start: "2026-07-20T10:00:00Z", anomaly_end: null, resolved_at: null, duration_seconds: null,
  evidence_status: "PARTIAL", suppressed: false, explanation: null, subject_kind: "LOG_SOURCE", ...over,
});
const page = <T,>(items: T[]) => ({ items, total: items.length, limit: 25, offset: 0 });

beforeEach(() => {
  vi.restoreAllMocks();
  setTestLocale("tr");
});

describe("/behavior in Turkish", () => {
  async function renderOverview(s = summary(), active = [anomaly()], sources = [source(), source({ log_source_id: "s-2", name: "Mail", state: "INSUFFICIENT_DATA", expected_eps: null, deviation_ratio: null })]) {
    vi.spyOn(api, "behaviorSummary").mockResolvedValue(s);
    vi.spyOn(api, "sourceBehaviors").mockResolvedValue(sources);
    vi.spyOn(api, "anomalies").mockResolvedValue(page(active));
    render(await BehaviorPage());
  }
  it("words the heading, the counters, the insufficient-data banner and the table", async () => {
    await renderOverview();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Davranış genel bakışı");
    expect(screen.getByText("Açık anomaliler")).toBeInTheDocument();
    expect(screen.getAllByText("Yetersiz veri").length).toBeGreaterThan(0);
    expect(screen.getByText(/izlenen kaynakta geçerli mevsimsel hücre için yeterli baseline yok/)).toBeInTheDocument();
    expect(screen.getAllByText("Dikkat gerekiyor").length).toBeGreaterThan(0);
    expect(screen.getByRole("columnheader", { name: "Gözlenen / beklenen (EPS)" })).toBeInTheDocument();
    expect(screen.getByText("Yeterli baseline yok")).toBeInTheDocument();
  });
  it("uses the Turkish number form for a counter and a table figure", async () => {
    await renderOverview();
    expect(screen.getByText("1.234")).toBeInTheDocument();
    expect(screen.getByText(/6,00 \/ 2,00/)).toBeInTheDocument();
  });
  it("says what is empty, in words, when nothing needs attention", async () => {
    await renderOverview(summary({ open_anomalies: 0, insufficient_data_sources: 0 }), [], []);
    expect(screen.getByText(/hiçbir şey yok/)).toBeInTheDocument();
    expect(screen.getByText("Henüz çözülmüş anomali yok.")).toBeInTheDocument();
  });
  it("names the failure and the permission-free refusals in Turkish", async () => {
    vi.spyOn(api, "behaviorSummary").mockRejectedValue(new ApiError(403, "no"));
    vi.spyOn(api, "sourceBehaviors").mockResolvedValue([]);
    vi.spyOn(api, "anomalies").mockResolvedValue(page([]));
    render(await BehaviorPage());
    expect(screen.getByRole("alert")).toHaveTextContent("Davranış analizini görüntüleme izniniz yok.");
  });
  it("words the route error boundary", () => {
    render(<BehaviorError error={Object.assign(new Error("x"), { digest: "r1" })} reset={() => {}} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Davranış analizi gösterilemedi.");
    expect(screen.getByRole("alert")).toHaveTextContent("Referans: r1.");
  });
});

describe("/behavior/sources in Turkish", () => {
  const params = (q: Record<string, string> = {}) => ({ searchParams: Promise.resolve(q) });
  it("words the table, the pill and the still-learning cell", async () => {
    vi.spyOn(api, "sourceBehaviors").mockResolvedValue([
      source(),
      source({ log_source_id: "s-2", name: "Mail", state: "INSUFFICIENT_DATA", expected_eps: null, deviation_ratio: null }),
    ]);
    render(await SourcesPage(params()));
    expect(screen.getByRole("columnheader", { name: /Gözlenen \(EPS\)/ })).toBeInTheDocument();
    expect(screen.getByText("öğreniyor")).toBeInTheDocument();
    expect(screen.getAllByText("YETERSİZ VERİ").length).toBeGreaterThan(0);
    expect(screen.getAllByText(/1234,50/).length).toBeGreaterThan(0);
  });
  it("words the empty, filtered-empty and failed states", async () => {
    vi.spyOn(api, "sourceBehaviors").mockResolvedValue([]);
    const view = render(await SourcesPage(params()));
    expect(screen.getByText(/İzlenen log source yok/)).toBeInTheDocument();
    view.unmount();
    render(await SourcesPage(params({ q: "zz" })));
    expect(screen.getByText(/Bu filtrelerle eşleşen kaynak yok/)).toBeInTheDocument();
  });
  it("names a failed read", async () => {
    vi.spyOn(api, "sourceBehaviors").mockRejectedValue(new Error("down"));
    render(await SourcesPage(params()));
    expect(screen.getByRole("alert")).toHaveTextContent("Kaynak davranışı yüklenemedi.");
  });
});

describe("/behavior/sources/[id] and /anomalies in Turkish", () => {
  it("words every refusal of the source page", async () => {
    for (const [status, text] of [[404, "Bu log source mevcut değil"], [403, "Davranış analizini görüntüleme izniniz yok."], [401, "Oturumunuzun süresi doldu."], [500, "Bu kaynak yüklenemedi."]] as const) {
      vi.spyOn(api, "sourceBehavior").mockRejectedValue(new ApiError(status, "x"));
      const view = render(await SourceDetailPage({ params: Promise.resolve({ id: "s" }), searchParams: Promise.resolve({}) }));
      expect(screen.getByRole("alert")).toHaveTextContent(text);
      view.unmount();
    }
  });
  it("words the anomaly list: rows, filtered-empty and the read failure", async () => {
    vi.spyOn(api, "sourceBehaviors").mockResolvedValue([source()]);
    vi.spyOn(api, "anomalies").mockResolvedValue(page([anomaly(), anomaly({ id: "a-2", state: "INSUFFICIENT_DATA", observed_value: null, expected_value: null })]));
    const view = render(await AnomaliesPage({ searchParams: Promise.resolve({}) }));
    expect(screen.getAllByText("Hacim artışı").length).toBeGreaterThan(0);
    expect(screen.getByRole("columnheader", { name: /Gözlenen → Beklenen \(EPS\)/ })).toBeInTheDocument();
    expect(screen.getAllByText("YETERSİZ VERİ").length).toBeGreaterThan(0);
    view.unmount();
    vi.spyOn(api, "anomalies").mockResolvedValue(page([]));
    const filtered = render(await AnomaliesPage({ searchParams: Promise.resolve({ state: "OPEN" }) }));
    expect(screen.getByText(/Bu filtrelerle eşleşen anomali yok/)).toBeInTheDocument();
    filtered.unmount();
    vi.spyOn(api, "anomalies").mockRejectedValue(new ApiError(403, "x"));
    render(await AnomaliesPage({ searchParams: Promise.resolve({}) }));
    expect(screen.getByRole("alert")).toHaveTextContent("read:anomalies");
  });
  it("words the anomaly detail refusals and the route error", async () => {
    vi.spyOn(api, "anomaly").mockRejectedValue(new ApiError(404, "x"));
    const view = render(await AnomalyDetailPage({ params: Promise.resolve({ id: "a" }) }));
    expect(screen.getByRole("alert")).toHaveTextContent("Bu anomali mevcut değil.");
    expect(screen.getByText("Tüm anomalilere dön")).toBeInTheDocument();
    view.unmount();
    render(<AnomaliesError error={new Error("x")} reset={() => {}} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Anomaliler gösterilemedi.");
  });
});

describe("Evidence in Turkish", () => {
  it("words labels and units, with the Turkish number form", () => {
    render(<Evidence data={{ anomaly_type: "VOLUME_SPIKE", observed_value: 1234.5, sample_count: 1, baseline_low: 2 }} />);
    expect(screen.getByText("Gözlenen")).toBeInTheDocument();
    expect(screen.getByText("1.234,5")).toBeInTheDocument();
    expect(screen.getByText("baseline örneği")).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "Algılayıcı kanıt ölçümleri" })).toBeInTheDocument();
  });
  it("words the empty state", () => {
    render(<Evidence data={{}} />);
    expect(screen.getByText("kanıt kaydedilmedi")).toBeInTheDocument();
  });
});

describe("helpers take the language as the last optional argument", () => {
  it("every state and availability has a Turkish meaning; English is unchanged", () => {
    for (const s of ["INSUFFICIENT_DATA", "NORMAL", "CANDIDATE", "OPEN", "RECOVERING", "RESOLVED", "SUPPRESSED"] as const) {
      expect(stateMeaningIn(s, TR).length, s).toBeGreaterThan(0);
      expect(stateMeaningIn(s, TR)).not.toBe(stateMeaning(s));
    }
    for (const a of ["AVAILABLE", "TRUNCATED", "UNAVAILABLE", "FAILED"] as const) {
      expect(dimensionMeaningIn(a, TR)).not.toBe("");
    }
    expect(stateMeaning("OPEN")).toBe(en.behavior.state.meaning.OPEN);
  });
  it("an unknown dimension or code shows itself", () => {
    expect(dimensionLabel("event_name", TR)).toBe("Olay adı");
    expect(dimensionLabel("zz_unknown", TR)).toBe("zz_unknown");
    expect(wordOrValue(TR, "behavior.evidenceStatus", "NOVEL")).toBe("NOVEL");
    expect(wordOrValue(ENGLISH_BEHAVIOR, "behavior.evidenceStatus", "")).toBe("");
  });
  it("the attention issue and the summary sentence come from codes", () => {
    const rows = buildAttentionRows([], [anomaly({ anomaly_type: "NO_EVENTS" })], TR);
    expect(rows[0].issue).toBe("Olay alınmadı");
    expect(buildAttentionRows([], [anomaly({ anomaly_type: "NO_EVENTS" })])[0].issue).toBe("No events received");
    const text = summarizeAnomaly({ ...anomaly(), explanation_package: null, detection: null, transitions: [], baseline_version: null, policy_version: 1 }, TR).text;
    expect(text).toContain("Olay hacmi");
    expect(text).toContain("6,00");
  });
});
