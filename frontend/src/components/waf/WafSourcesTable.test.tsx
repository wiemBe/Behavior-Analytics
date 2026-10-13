import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { WAF_SOURCE_ID, wafSource } from "./fixtures";
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
    expect(row.getByText("for profile v3")).toBeInTheDocument();
    expect(row.getByText(/97\.2%/)).toBeInTheDocument();
    expect(row.getByText(/132 \/ 500/)).toBeInTheDocument();
    expect(row.getByText("None")).toBeInTheDocument();
  });

  it("does not show a source as valid when it was validated for an older profile version", () => {
    render(
      <WafSourcesTable
        sources={[wafSource({ profile_version: 5, validated_profile_version: 4, validation_current: false })]}
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
    expect(row.getByText("No bucket collected yet")).toBeInTheDocument();
    expect(row.getByText("No bucket collected in range")).toBeInTheDocument();
    expect(row.queryByText("None")).not.toBeInTheDocument();
    expect(row.queryByText(/0\.0%/)).not.toBeInTheDocument();
    // The em dash carries its meaning for assistive technology.
    expect(row.getByText(/no bucket has been collected/)).toBeInTheDocument();
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
    expect(row.getByText(/≥ 62\.4%/)).toBeInTheDocument();
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
    expect(screen.getByRole("columnheader", { name: /Endpoint coverage/ })).toHaveClass("num");
    expect(screen.getByRole("columnheader", { name: /Active endpoints/ })).toHaveClass("num");
  });
});
