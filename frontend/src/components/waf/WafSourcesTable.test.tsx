import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { NO_RUN_RECORDED, WAF_SOURCE_ID, wafCollection, wafSource } from "./fixtures";
import { WafSourcesTable } from "./WafSourcesTable";

function rowOf(name: string) {
  return screen.getByRole("link", { name }).closest("tr") as HTMLElement;
}

const NO_ADMISSION = {
  buckets_in_range: 0,
  buckets_admission_truncated: null,
  rejected_over_quota: null,
  below_admission_threshold: null,
  untracked_requests: null,
};

describe("WafSourcesTable", () => {
  it("shows a healthy source as VALID for its profile version, with coverage and ceiling", () => {
    render(<WafSourcesTable sources={[wafSource()]} />);
    const row = within(rowOf("lab-waf-behavior-01"));
    expect(row.getByText("VALID")).toBeInTheDocument();
    expect(row.queryByText(/for profile v3/)).not.toBeInTheDocument(); // the version sits in the Profile column
    expect(row.getByText("v3")).toBeInTheDocument();
    expect(row.getByText(/97\.2 %/)).toBeInTheDocument();
    expect(row.getByText(/132 \/ 500/)).toBeInTheDocument();
    expect(row.getByText("None")).toBeInTheDocument();
  });

  it("does not show a source as valid when it was validated for an older profile version", () => {
    render(
      <WafSourcesTable
        sources={[
          wafSource({
            profile_version: 5,
            validated_profile_version: 4,
            validation_current: false,
            collection: wafCollection({ state: "not_eligible", ineligible_code: "validation_stale" }),
          }),
        ]}
      />,
    );
    const row = within(rowOf("lab-waf-behavior-01"));
    expect(row.queryByText("VALID")).not.toBeInTheDocument();
    expect(row.getByText("STALE")).toBeInTheDocument();
    expect(row.getByText(/VALID for v4; profile is now v5/)).toBeInTheDocument();
    expect(row.getByText("Not collected")).toBeInTheDocument();
  });

  it("reports admission overflow with its counts, and says the ceiling is reached", () => {
    render(
      <WafSourcesTable
        sources={[
          wafSource({
            active_endpoints: 500,
            admission: {
              buckets_in_range: 211,
              buckets_admission_truncated: 64,
              rejected_over_quota: 1893,
              below_admission_threshold: 702,
              untracked_requests: 48210,
            },
          }),
        ]}
      />,
    );
    const row = within(rowOf("lab-waf-behavior-01"));
    expect(row.getByText("Overflowing")).toBeInTheDocument();
    expect(row.getByText("64 of 211 buckets truncated")).toBeInTheDocument();
    expect(row.getByText("1,893 refused over quota")).toBeInTheDocument();
    expect(row.getByText("48,210 untracked requests")).toBeInTheDocument();
    expect(row.getByText("At the ceiling")).toBeInTheDocument();
  });

  it("renders no collected bucket as an unmeasured coverage and overflow, never as 0 or None", () => {
    render(<WafSourcesTable sources={[wafSource({ latest_bucket: null, admission: NO_ADMISSION })]} />);
    const row = within(rowOf("lab-waf-behavior-01"));
    // Said once above the table, not in every row.
    expect(screen.getByText("No bucket collected yet")).toBeInTheDocument();
    expect(row.queryByText("No bucket collected yet")).not.toBeInTheDocument();
    expect(row.queryByText("No bucket collected in range")).not.toBeInTheDocument();
    expect(row.queryByText("None")).not.toBeInTheDocument();
    expect(row.queryByText(/0\.0 %/)).not.toBeInTheDocument();
    // The em dash carries its meaning for assistive technology.
    expect(row.getByText(/no bucket has been collected/)).toBeInTheDocument();
  });

  it("does not repeat the no-bucket line when only some sources lack a bucket", () => {
    render(
      <WafSourcesTable
        sources={[
          wafSource({ log_source_id: "a", log_source_name: "alpha-ok" }),
          wafSource({ log_source_id: "b", log_source_name: "bravo-empty", latest_bucket: null, admission: NO_ADMISSION }),
        ]}
      />,
    );
    expect(screen.queryByText("No bucket collected yet")).not.toBeInTheDocument();
    expect(within(rowOf("bravo-empty")).getByText(/no bucket has been collected/)).toBeInTheDocument();
  });

  it("renders a lower-bound coverage as ≥ and a partial bucket as such", () => {
    render(
      <WafSourcesTable
        sources={[
          wafSource({
            latest_bucket: {
              ...wafSource().latest_bucket!,
              endpoint_coverage_ratio: 0.624,
              coverage_measurement: "LOWER_BOUND",
              completeness: "PARTIAL",
            },
          }),
        ]}
      />,
    );
    const row = within(rowOf("lab-waf-behavior-01"));
    expect(row.getByText(/≥ 62\.4 %/)).toBeInTheDocument();
    expect(row.getByText("PARTIAL bucket")).toBeInTheDocument();
  });

  it("lists sources that need attention first", () => {
    render(
      <WafSourcesTable
        sources={[
          wafSource({ log_source_id: "a", log_source_name: "alpha-ok" }),
          wafSource({ log_source_id: "b", log_source_name: "bravo-disabled", enabled: false }),
        ]}
      />,
    );
    const names = screen.getAllByRole("row").slice(1).map((r) => r.querySelector("a")?.textContent);
    expect(names).toEqual(["bravo-disabled", "alpha-ok"]);
  });

  it("links the source to its endpoints and its profile to administration", () => {
    render(<WafSourcesTable sources={[wafSource()]} />);
    expect(screen.getByRole("link", { name: "lab-waf-behavior-01" })).toHaveAttribute(
      "href",
      `/behavior/waf/sources/${WAF_SOURCE_ID}`,
    );
    expect(screen.getByRole("link", { name: "Profile settings" })).toHaveAttribute(
      "href",
      `/admin/waf-sources/${WAF_SOURCE_ID}`,
    );
  });

  it("labels the table and right-aligns its numeric columns", () => {
    render(<WafSourcesTable sources={[wafSource()]} />);
    expect(screen.getByRole("region", { name: "WAF sources" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Coverage (%)" })).toHaveClass("num");
    expect(screen.getByRole("columnheader", { name: /Active endpoints/ })).toHaveClass("num");
  });
});

describe("WafSourcesTable: admission wording", () => {
  const overflow = (over = {}) =>
    wafSource({
      admission: {
        buckets_in_range: 211,
        buckets_admission_truncated: 0,
        rejected_over_quota: 1893,
        below_admission_threshold: 702,
        untracked_requests: 10,
        ...over,
      },
    });

  it("says quota refusals and below-threshold keys apart, each counted per key-bucket", () => {
    render(<WafSourcesTable sources={[overflow()]} />);
    const row = within(rowOf("lab-waf-behavior-01"));
    expect(row.getByText("1,893 refused over quota")).toBeInTheDocument();
    expect(row.getByText("702 below admission threshold")).toBeInTheDocument();
    // The key-bucket definition lives in the Guide, not under the table.
    expect(row.queryByText("(key-buckets)")).not.toBeInTheDocument();
    expect(screen.queryByText(/A key-bucket is one endpoint key in one collected bucket/)).not.toBeInTheDocument();
  });

  it("calls quota refusals overflow, but never the below-threshold count", () => {
    const { unmount } = render(<WafSourcesTable sources={[overflow()]} />);
    expect(screen.getByText("Overflowing")).toBeInTheDocument();
    unmount();
    render(<WafSourcesTable sources={[overflow({ rejected_over_quota: 0 })]} />);
    expect(screen.queryByText("Overflowing")).not.toBeInTheDocument();
    expect(screen.getByText("702 below admission threshold")).toBeInTheDocument();
  });
});


describe("WafSourcesTable: collection health (FIX-71e)", () => {
  const ran = (over = {}) => wafSource({ collection: wafCollection(over) });
  const bucketless = { latest_bucket: null, admission: NO_ADMISSION };

  it("says No source run recorded for an enabled VALID source nothing ran for, never Collecting", () => {
    render(<WafSourcesTable sources={[wafSource({ ...bucketless, collection: NO_RUN_RECORDED })]} />);
    const row = within(rowOf("lab-waf-behavior-01"));
    expect(row.getByText("No source run recorded")).toHaveClass("warn");
    expect(row.queryByText("Collecting")).not.toBeInTheDocument();
    expect(row.queryByText("Details")).not.toBeInTheDocument();
    // Said about the source, not as a claim that a task was lost.
    expect(row.queryByText(/queue|lost|fail/i)).not.toBeInTheDocument();
  });

  it("shows a failed run with its stage and time, and the class behind the disclosure", () => {
    render(
      <WafSourcesTable
        sources={[
          ran({
            state: "failed",
            last_failure_at: "2026-10-13T08:50:00Z",
            failure_stage: "collect",
            failure_class: "ProviderTimeoutError",
            consecutive_failures: 4,
            intervals_due: 2,
            intervals_collected: 0,
          }),
        ]}
      />,
    );
    const row = within(rowOf("lab-waf-behavior-01"));
    expect(row.getByText("Last run failed")).toHaveClass("crit");
    expect(row.getByText(/collect stage/)).toBeInTheDocument();
    expect(row.getByText("Error class ProviderTimeoutError")).toBeInTheDocument();
    expect(row.getByText("4 failed runs in a row")).toBeInTheDocument();
    expect(row.getByText("0 of 2 intervals stored")).toBeInTheDocument();
    // The class sits inside the disclosure, not in the row's first line.
    expect(row.getByText("Details").closest("details")).toContainElement(
      row.getByText("Error class ProviderTimeoutError"),
    );
  });

  it("does not show a stage the server did not name from the closed list", () => {
    render(
      <WafSourcesTable
        sources={[
          ran({ state: "failed", last_failure_at: "2026-10-13T08:50:00Z", failure_stage: "SELECT 1" }),
        ]}
      />,
    );
    const row = within(rowOf("lab-waf-behavior-01"));
    expect(row.queryByText(/SELECT/)).not.toBeInTheDocument();
    expect(row.queryByText(/stage/)).not.toBeInTheDocument();
    expect(row.getByText("Last run failed")).toBeInTheDocument();
  });

  it.each([
    ["source_busy", "Source busy", "warn"],
    ["capacity_unavailable", "Waiting for search capacity", "warn"],
    ["budget_exhausted", "Search budget used up", "warn"],
    ["no_progress", "Ran, stored no bucket", "warn"],
    ["history_reading", "Reading history", ""],
    ["no_interval_due", "No interval due", ""],
    ["collecting", "Collecting", "ok"],
  ] as const)("words the %s state as %s", (state, label, tone) => {
    render(<WafSourcesTable sources={[ran({ state })]} />);
    const pill = within(rowOf("lab-waf-behavior-01")).getByText(label);
    if (tone) expect(pill).toHaveClass(tone);
    else expect(pill).not.toHaveClass("ok", "warn", "crit");
  });

  it("shows when a busy or capacity-bound source was last skipped, in UTC", () => {
    render(
      <WafSourcesTable
        sources={[ran({ state: "source_busy", skip_reason: "source_locked", skipped_at: "2026-10-13T08:55:00Z" })]}
      />,
    );
    expect(within(rowOf("lab-waf-behavior-01")).getByText(/Skipped/)).toHaveTextContent(/UTC/);
  });

  it("keeps the latest bucket separate: an old bucket with a newer failure shows both", () => {
    render(
      <WafSourcesTable
        sources={[ran({ state: "failed", last_failure_at: "2026-10-13T09:50:00Z", failure_stage: "evaluate" })]}
      />,
    );
    const row = within(rowOf("lab-waf-behavior-01"));
    expect(row.getByText(/97\.2 %/)).toBeInTheDocument();
    expect(row.getByText("Last run failed")).toBeInTheDocument();
  });

  it("names collection and monitoring switches as the reason a source is not collected", () => {
    render(
      <WafSourcesTable
        sources={[
          wafSource({
            log_source_id: "a",
            log_source_name: "alpha",
            collection: wafCollection({ state: "not_eligible", ineligible_code: "collection_disabled" }),
          }),
          wafSource({
            log_source_id: "b",
            log_source_name: "bravo",
            collection: wafCollection({ state: "not_eligible", ineligible_code: "monitoring_disabled" }),
          }),
          wafSource({
            log_source_id: "c",
            log_source_name: "charlie",
            validation_current: false,
            collection: wafCollection({ state: "not_eligible", ineligible_code: "validation_stale" }),
          }),
        ]}
      />,
    );
    expect(within(rowOf("alpha")).getByText("Collection is off")).toBeInTheDocument();
    expect(within(rowOf("bravo")).getByText("Monitoring is off for this source")).toBeInTheDocument();
    // Validation and profile columns carry the other reasons; the cell does not repeat them.
    expect(within(rowOf("charlie")).getByText("Not collected")).toBeInTheDocument();
    for (const name of ["alpha", "bravo", "charlie"]) {
      expect(within(rowOf(name)).getAllByText("Not collected")).toHaveLength(1);
    }
  });

  it("puts the Collection column between Validation and Coverage", () => {
    render(<WafSourcesTable sources={[wafSource()]} />);
    const heads = screen.getAllByRole("columnheader").map((h) => h.textContent);
    expect(heads).toEqual([
      "Source",
      "Profile",
      "Validation",
      "Collection",
      "Coverage (%)",
      "Active endpoints",
      "Overflow",
    ]);
  });
});
