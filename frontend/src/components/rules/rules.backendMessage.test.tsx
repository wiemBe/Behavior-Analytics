// The rule-statistics sentences the backend writes (window reasons, the silent-rules exclusion,
// the benchmark label and unit) in the reader's language: a coded sentence renders from the
// `backend` catalog in Turkish and as the API's own text in English; an uncoded one (an old row,
// a client older than the API) stays the API's English in both.

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { CostSection, RuleStatisticsWindow, SilentSection } from "@/lib/api";

import { setTestLocale } from "../../../vitest.setup";

import { CostliestRules } from "./CostliestRules";
import { MeasurementWindow } from "./MeasurementWindow";
import { MostMatched } from "./MostMatched";
import { SilentRules } from "./SilentRules";

const INCOMPLETE_EN =
  "Only 23 of 24 windows in the span are complete (1 missing, 0 truncated, 0 failed)." +
  " Absence from an incomplete window proves nothing.";
const INCOMPLETE_PARAMS = { complete: 23, expected: 24, missing: 1, truncated: 0, failed: 0 };
const INCOMPLETE_TR =
  "Aralıkta 23 / 24 pencere eksiksiz (1 eksik, 0 kırpılmış, 0 başarısız)." +
  " Eksiksiz olmayan bir penceredeki yokluk hiçbir şeyi kanıtlamaz.";
const NONE_EN =
  "No rule-match window has been collected for this instance: collection is disabled " +
  "or has not run yet, so nothing is measured.";
const EXCLUDED_EN =
  "Only EVENT rules and EVENT building blocks are judged: an event search sees every one of " +
  "their matches. COMMON rules also match on flows, which are not searched, so their silence " +
  "is unknown. FLOW and OFFENSE rules are not searched at all.";
const EXCLUDED_TR =
  "EVENT kuralları ve EVENT yapı taşları değerlendirilen tek türdür: olay araması bunların " +
  "her eşleşmesini görür. COMMON kuralları akışlarda da eşleşir, ancak akışlar aranmaz; bu " +
  "yüzden sessizlikleri bilinmez. FLOW ve OFFENSE kuralları hiç aranmaz.";

const win = (over: Partial<RuleStatisticsWindow> = {}): RuleStatisticsWindow => ({
  days: 1,
  span_start: "2026-10-12T00:00:00Z",
  span_end: "2026-10-13T00:00:00Z",
  span_seconds: 86400,
  window_seconds: 3600,
  expected_windows: 24,
  complete_windows: 23,
  truncated_windows: 0,
  failed_windows: 0,
  missing_windows: 1,
  measured_seconds: 82800,
  complete: false,
  reason: INCOMPLETE_EN,
  reason_code: "rule_statistics.window.incomplete",
  reason_params: INCOMPLETE_PARAMS,
  ...over,
});

const QUERY = { rank: "events" } as unknown as Parameters<typeof MostMatched>[0]["query"];

const silent = (over: Partial<SilentSection> = {}): SilentSection => ({
  items: [],
  total: 0,
  items_truncated: false,
  excluded: [{ rule_type: "FLOW", evidence: "NOT_MEASURED", count: 3 }],
  excluded_count: 3,
  excluded_reason: EXCLUDED_EN,
  excluded_reason_code: "rule_statistics.silent.excluded",
  excluded_reason_params: {},
  ...over,
});

const cost = (over: Partial<CostSection> = {}): CostSection => ({
  label:
    "QRadar capacity benchmark: how fast the rule could be evaluated. " +
    "Lower is costlier to evaluate. This is not a firing rate.",
  label_code: "rule_statistics.cost.label",
  label_params: {},
  unit: "millions of events per second (QRadar's unit)",
  unit_code: "rule_statistics.cost.unit",
  unit_params: {},
  items: [
    { rule_id: "r1", qradar_id: 1, name: "Rule one", rule_type: "EVENT", is_building_block: false, average_capacity: 0.4 },
  ],
  no_benchmark_count: 0,
  ...over,
});

describe("MeasurementWindow reason", () => {
  it("renders a partial window's coded reason in Turkish", () => {
    setTestLocale("tr");
    render(<MeasurementWindow window={win()} />);
    const region = screen.getByRole("region", { name: "Ölçüm penceresi" });
    expect(region).toHaveTextContent(INCOMPLETE_TR);
    expect(region).not.toHaveTextContent("Only 23 of 24");
  });

  it("renders the no-collection reason in Turkish", () => {
    setTestLocale("tr");
    render(
      <MeasurementWindow
        window={win({
          expected_windows: 0,
          complete_windows: 0,
          missing_windows: 0,
          measured_seconds: 0,
          reason: NONE_EN,
          reason_code: "rule_statistics.window.none",
          reason_params: {},
        })}
      />,
    );
    expect(screen.getByRole("region", { name: "Ölçüm penceresi" })).toHaveTextContent(
      "Bu instance için hiçbir kural eşleşmesi penceresi toplanmadı",
    );
  });

  it("keeps the API's English in Turkish when the window has no code", () => {
    setTestLocale("tr");
    render(<MeasurementWindow window={win({ reason_code: null, reason_params: null })} />);
    expect(screen.getByRole("region", { name: "Ölçüm penceresi" })).toHaveTextContent(INCOMPLETE_EN);
  });

  it("falls back to the page's own words when there is no reason at all", () => {
    setTestLocale("tr");
    render(<MeasurementWindow window={win({ reason: null, reason_code: null, reason_params: null })} />);
    expect(screen.getByRole("region", { name: "Ölçüm penceresi" })).toHaveTextContent(
      "İstenen zaman aralığı bütünüyle toplanmadı.",
    );
  });

  it("shows the API's text verbatim in English even with a code", () => {
    setTestLocale("en");
    render(<MeasurementWindow window={win()} />);
    expect(screen.getByRole("region", { name: "Measurement window" })).toHaveTextContent(INCOMPLETE_EN);
  });
});

describe("plain counts in a window reason", () => {
  const big = { complete: 8640, expected: 25920, missing: 17280, truncated: 0, failed: 0 };
  const reason = (code = "rule_statistics.window.incomplete") =>
    win({ reason_code: code, reason_params: big });

  it("groups a `plain` count in Turkish: 8640 reads 8.640 and 25920 reads 25.920", () => {
    setTestLocale("tr");
    render(<MeasurementWindow window={reason()} />);
    const text = screen.getByRole("region", { name: "Ölçüm penceresi" }).textContent ?? "";
    expect(text).toContain("8.640");
    expect(text).toContain("25.920");
    expect(text).toContain("17.280 eksik");
    expect(text).not.toMatch(/\b(8640|25920)\b/);
  });

  it("leaves English as the API sent it, ungrouped", () => {
    setTestLocale("en");
    render(<MeasurementWindow window={reason()} />);
    expect(screen.getByRole("region", { name: "Measurement window" })).toHaveTextContent(
      INCOMPLETE_EN,
    );
  });
});

describe("MostMatched reason", () => {
  it("words the reason of an unmeasured list in Turkish", () => {
    setTestLocale("tr");
    render(
      <MostMatched
        rows={null}
        reason={INCOMPLETE_EN}
        reasonCode="rule_statistics.window.incomplete"
        reasonParams={INCOMPLETE_PARAMS}
        query={QUERY}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent(INCOMPLETE_TR);
  });

  it("words the partial note above a measured list in Turkish", () => {
    setTestLocale("tr");
    const { container } = render(
      <MostMatched
        rows={[
          {
            rule_id: "r1",
            qradar_id: 1,
            name: "Rule one",
            enabled: true,
            rule_type: "EVENT",
            is_building_block: false,
            match_records: 12,
            matched_events: 12,
            match_rate_per_second: 0.5,
            lower_bound: false,
          },
        ]}
        reason={INCOMPLETE_EN}
        reasonCode="rule_statistics.window.incomplete"
        reasonParams={INCOMPLETE_PARAMS}
        query={QUERY}
      />,
    );
    const note = container.querySelector(".stats-top-reason");
    expect(note).toHaveTextContent(INCOMPLETE_TR);
    expect(note).not.toHaveTextContent("Only 23 of 24");
  });

  it("keeps the API's English for an uncoded reason, and in English", () => {
    setTestLocale("tr");
    const { unmount } = render(<MostMatched rows={null} reason={INCOMPLETE_EN} query={QUERY} />);
    expect(screen.getByRole("status")).toHaveTextContent(INCOMPLETE_EN);
    unmount();
    setTestLocale("en");
    render(
      <MostMatched
        rows={null}
        reason={INCOMPLETE_EN}
        reasonCode="rule_statistics.window.incomplete"
        reasonParams={INCOMPLETE_PARAMS}
        query={QUERY}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent(INCOMPLETE_EN);
  });
});

describe("SilentRules reason and exclusion", () => {
  it("words the not-judged reason in Turkish", () => {
    setTestLocale("tr");
    render(
      <SilentRules
        silent={null}
        reason={INCOMPLETE_EN}
        reasonCode="rule_statistics.window.incomplete"
        reasonParams={INCOMPLETE_PARAMS}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Sessizlik değerlendirilmedi.");
    expect(screen.getByRole("status")).toHaveTextContent(INCOMPLETE_TR);
  });

  it("embeds the Turkish exclusion sentence after 'çünkü' with the type name in capitals", () => {
    setTestLocale("tr");
    render(<SilentRules silent={silent()} reason={null} />);
    expect(document.body).toHaveTextContent(`çünkü ${EXCLUDED_TR}`);
    // The sentence opens with the initialism EVENT, so it reads on after "çünkü" with no
    // lower-casing step in Turkish.
    expect(document.body).not.toHaveTextContent("çünkü event kuralları");
  });

  it("keeps the English lower-casing after 'because' in English", () => {
    setTestLocale("en");
    render(<SilentRules silent={silent()} reason={null} />);
    expect(document.body).toHaveTextContent(
      "because only EVENT rules and EVENT building blocks are judged: an event search",
    );
  });

  it("keeps the API's English after 'çünkü' for an uncoded exclusion (an old API)", () => {
    setTestLocale("tr");
    render(
      <SilentRules
        silent={silent({ excluded_reason_code: null, excluded_reason_params: null })}
        reason={null}
      />,
    );
    expect(document.body).toHaveTextContent(`çünkü ${EXCLUDED_EN}`);
  });
});

describe("CostliestRules label and unit", () => {
  it("renders the benchmark label and unit in Turkish", () => {
    setTestLocale("tr");
    render(<CostliestRules cost={cost()} />);
    expect(
      screen.getByText(
        "QRadar kapasite ölçütü: kuralın ne kadar hızlı değerlendirilebileceği. Düşük değer, " +
          "değerlendirmenin daha maliyetli olduğunu gösterir. Bu bir tetiklenme hızı değildir.",
      ),
    ).toBeInTheDocument();
    expect(screen.getAllByText("saniyede milyon olay (QRadar birimi)").length).toBeGreaterThan(0);
    expect(screen.queryByText(/millions of events per second/)).toBeNull();
  });

  it("keeps the API's text in English and for an uncoded label", () => {
    setTestLocale("en");
    const { unmount } = render(<CostliestRules cost={cost()} />);
    expect(screen.getByText(/QRadar capacity benchmark: how fast/)).toBeInTheDocument();
    expect(screen.getAllByText("millions of events per second (QRadar's unit)").length).toBeGreaterThan(0);
    unmount();
    setTestLocale("tr");
    render(
      <CostliestRules
        cost={cost({ label_code: null, label_params: null, unit_code: null, unit_params: null })}
      />,
    );
    expect(screen.getByText(/QRadar capacity benchmark: how fast/)).toBeInTheDocument();
  });
});
