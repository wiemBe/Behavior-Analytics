import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setTestLocale } from "../../../vitest.setup";
import { createLocaleTranslator } from "@/i18n/fallback";
import type { CustomRulePreview } from "@/lib/api";

import { CustomRuleWriteNotice } from "./CustomRuleWriteNotice";
import { PreviewChart, PreviewSummary, previewText, previewTooltip } from "./PreviewChart";
import { RuleStatus } from "./RuleStatus";
import {
  conditionText,
  draftReason,
  emptyDraft,
  statusLabel,
  subjectKindLabel,
} from "./authoring";
import { catalog } from "./fixtures";

const chartOptions: Array<Record<string, unknown>> = [];
vi.mock("echarts/core", async () => {
  const actual = await vi.importActual<typeof import("echarts/core")>("echarts/core");
  return {
    ...actual,
    use: vi.fn(),
    init: vi.fn(() => ({
      setOption: (option: Record<string, unknown>) => chartOptions.push(option),
      resize: vi.fn(),
      dispose: vi.fn(),
    })),
  };
});

const tr = createLocaleTranslator("tr");

function preview(over: Partial<CustomRulePreview> = {}): CustomRulePreview {
  return {
    threshold_value: 1234.5,
    unit: "events/s",
    operator: "GT",
    fired_count: 2,
    measured_count: 1,
    unmeasured_count: 1,
    buckets: [
      {
        bucket_start: "2026-10-16T07:00:00Z",
        value: 1234.5,
        measurement: "LOWER_BOUND",
        completeness: "PARTIAL",
        in_maintenance: true,
      },
      {
        bucket_start: "2026-10-16T07:05:00Z",
        value: null,
        measurement: "NOT_MEASURED",
        completeness: "MISSING",
        in_maintenance: false,
      },
    ],
    breach_intervals: [],
    ...over,
  };
}

beforeEach(() => setTestLocale("tr"));

describe("custom-rule authoring in Turkish", () => {
  it("labels every subject kind and leaves an unknown evaluator status raw", () => {
    const kinds = ["LOG_SOURCE", "FLEET_CLASS", "WAF_ENDPOINT", "IPS_ENTITY", "IPS_SOURCE", "PW_VALUE"] as const;
    for (const kind of kinds) expect(subjectKindLabel(kind, tr)).not.toBe("");
    expect(subjectKindLabel("WAF_ENDPOINT", tr)).toBe("WAF endpoint değeri");
    expect(statusLabel("SUBJECT_RETIRED", tr)).toBe("Özne kullanımdan kaldırıldı");
    expect(statusLabel("INSUFFICIENT_DATA", tr)).toBe("Yetersiz veri");
    expect(statusLabel("NORMAL", tr)).toBe("Normal durum");
    expect(statusLabel("FUTURE_STATE", tr)).toBe("FUTURE_STATE");
  });

  it("formats a Turkish condition with grouping, decimal comma and a no-break unit space", () => {
    expect(conditionText({
      metric: "EVENT_COUNT",
      operator: "GE",
      threshold_value: 1234.5,
      threshold_unit: "events/s",
      sustain_buckets: 2,
    }, "tr", tr)).toBe("olay sayısı ≥ 1.234,5\u00a0events/s; 2 zaman dilimi boyunca");
  });

  it("maps each frontend validation exit to Turkish", () => {
    const draft = emptyDraft(catalog());
    expect(draftReason(draft, null, tr)).toBe("Ad zorunludur.");
    expect(draftReason({ ...draft, name: "a", logSourceId: "source-1" }, null, tr))
      .toBe("Bu özne türü için metrik seçin.");
  });
});

describe("custom-rule preview in Turkish", () => {
  it("translates tooltip codes and formats its chart number", () => {
    const text = previewTooltip(preview(), "edge-waf", 0, "tr", tr);
    expect(text).toContain("Okuma: En az 1.234,5\u00a0events/s");
    expect(text).toContain("Ölçüm: Alt sınır");
    expect(text).toContain("Eksiksizlik: Kısmi");
    expect(text).toContain("Bakım: evet");
  });

  it("builds the accessible summary in Turkish", () => {
    expect(previewText(preview(), "edge-waf", "tr", tr)).toContain(
      "Kuralın tetikleneceği durum sayısı: 2",
    );
    render(<PreviewSummary preview={preview()} />);
    expect(screen.getByLabelText("Önizleme özeti")).toHaveTextContent("Ölçülen1 zaman dilimi");
    expect(screen.getByText(/Elmas işaretleri alt sınırdır/)).toBeInTheDocument();
    expect(screen.getByText(/Bakım zaman dilimleri gölgelenir/)).toBeInTheDocument();
  });

  it("hands the chart the Turkish axis name and sentence through the mounted component", () => {
    vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
    chartOptions.length = 0;
    const { container } = render(<PreviewChart preview={preview()} subjectLabel="edge-waf" />);
    expect((chartOptions[0].xAxis as { name: string }).name).toBe("Zaman (UTC)");
    expect(container.querySelector(".sr-only")?.textContent).toContain("Kuralın tetikleneceği durum sayısı");
    expect(container.textContent).not.toContain("customRules.");
    vi.unstubAllGlobals();
  });

  it("designs the empty preview in Turkish", () => {
    render(<PreviewChart preview={preview({ buckets: [] })} subjectLabel="edge-waf" />);
    expect(screen.getByText("Bu önizleme penceresine düşen zaman dilimi yok.")).toBeInTheDocument();
    expect(screen.getByText(/Daha uzun bir pencere seçin/)).toBeInTheDocument();
  });
});

describe("custom-rule status and write states in Turkish", () => {
  it("shows a translated status but preserves the backend reason", () => {
    render(<RuleStatus status="NOT_COLLECTED" reason="collector has not run" />);
    expect(screen.getByText("Toplanmadı")).toBeInTheDocument();
    expect(screen.getByText("collector has not run")).toBeInTheDocument();
  });

  it("names the write permission and granting role as code", () => {
    render(<CustomRuleWriteNotice />);
    expect(screen.getByText("Kural düzenleyici gizlendi.")).toBeInTheDocument();
    expect(screen.getByText("custom_rule:write").tagName).toBe("CODE");
    expect(screen.getByText("detection_engineer").tagName).toBe("CODE");
  });
});
