import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { admission, dimension, ipsSource, latestBucket, pressure } from "./behaviorFixtures";
import { IpsSourcesTable } from "./IpsSourcesTable";

function row(name: string) {
  return within(screen.getByRole("link", { name }).closest("tr")!);
}

describe("IPS sources table", () => {
  it("lists a source that needs attention first and links each to its page", () => {
    render(
      <IpsSourcesTable
        sources={[
          ipsSource({ log_source_id: "a", log_source_name: "alpha-ok" }),
          ipsSource({ log_source_id: "b", log_source_name: "bravo-disabled", enabled: false }),
        ]}
      />,
    );
    const names = screen.getAllByRole("link", { name: /alpha-ok|bravo-disabled/ }).map((a) => a.textContent);
    expect(names).toEqual(["bravo-disabled", "alpha-ok"]);
    expect(screen.getByRole("link", { name: "alpha-ok" })).toHaveAttribute("href", "/behavior/ips/sources/a");
  });

  it("has column headers, a caption and a labelled scroll region", () => {
    render(<IpsSourcesTable sources={[ipsSource()]} />);
    expect(screen.getAllByRole("columnheader")).toHaveLength(5);
    expect(screen.getByRole("table")).toHaveAccessibleName(/IPS sources with profile and validation/);
    expect(screen.getByRole("region", { name: "IPS sources" })).toBeInTheDocument();
  });

  it("shows a source with no bucket as not collected yet, never as zero", () => {
    render(<IpsSourcesTable sources={[ipsSource({ latest_bucket: null })]} />);
    const r = row("lab-ips-01");
    expect(r.getAllByText("Not collected yet")).toHaveLength(2);
    expect(r.queryByText(/^0$/)).toBeNull();
    expect(r.queryByText(/events$/)).toBeNull();
    expect(r.queryByText("None")).toBeNull();
  });

  it("shows the latest bucket in UTC with its event count and maintenance", () => {
    render(
      <IpsSourcesTable sources={[ipsSource({ latest_bucket: latestBucket({ in_maintenance: true }) })]} />,
    );
    const r = row("lab-ips-01");
    expect(r.getByText("5,184 events")).toBeInTheDocument();
    expect(r.getByText("In maintenance")).toBeInTheDocument();
    expect(r.getByTitle(/2026-10-13 08:55:00 UTC/)).toBeInTheDocument();
  });

  it("does not show a profile valid for an older version as valid, and says it is not collected", () => {
    render(
      <IpsSourcesTable
        sources={[ipsSource({ profile_version: 5, validated_profile_version: 4, validation_current: false })]}
      />,
    );
    const r = row("lab-ips-01");
    expect(r.getByText("STALE")).toBeInTheDocument();
    expect(r.queryByText("VALID")).toBeNull();
    expect(r.getByText("VALID for v4; profile is now v5")).toBeInTheDocument();
    expect(r.getByText("Not collected")).toBeInTheDocument();
  });

  it("marks a disabled profile", () => {
    render(<IpsSourcesTable sources={[ipsSource({ enabled: false })]} />);
    expect(row("lab-ips-01").getByText("Disabled")).toBeInTheDocument();
  });

  it.each([
    ["EXACT", "6 of 6 exact", null],
    ["LOWER_BOUND", "5 of 6 exact", "Lower bound: Source IP"],
    ["NOT_COLLECTED", "5 of 6 exact", "Not collected: Source IP"],
    ["FAILED", "5 of 6 exact", "Failed: Source IP"],
    ["REJECTED", "5 of 6 exact", "Rejected: Source IP"],
  ])("renders a %s dimension measurement", (measurement, summary, line) => {
    const dims = latestBucket().dimensions.map((d) =>
      d.dimension === "SOURCE_IP" ? dimension("SOURCE_IP", measurement) : d,
    );
    render(<IpsSourcesTable sources={[ipsSource({ latest_bucket: latestBucket({ dimensions: dims }) })]} />);
    const r = row("lab-ips-01");
    expect(r.getByText(summary)).toBeInTheDocument();
    if (line) expect(r.getByText(line)).toBeInTheDocument();
    else expect(r.queryByText(/^(Lower bound|Not collected|Failed|Rejected):/)).toBeNull();
  });

  it("shows entity pressure per dimension against the ceiling and flags a full one", () => {
    render(
      <IpsSourcesTable
        sources={[ipsSource({ entities: [pressure("DESTINATION_PORT", 40, 52), pressure("SIGNATURE", 500, 530)] })]}
      />,
    );
    const r = row("lab-ips-01");
    expect(r.getByText("Ports: 40 / 500")).toBeInTheDocument();
    expect(r.getByText("Signatures: 500 / 500")).toBeInTheDocument();
    expect(r.getAllByText("At the ceiling")).toHaveLength(1);
  });

  it("calls out admission overflow only when it is non-zero", () => {
    const { rerender } = render(<IpsSourcesTable sources={[ipsSource()]} />);
    expect(row("lab-ips-01").getByText("None")).toBeInTheDocument();
    expect(screen.queryByText("Overflowing")).toBeNull();

    rerender(
      <IpsSourcesTable
        sources={[
          ipsSource({
            latest_bucket: latestBucket({
              admission: [
                admission("DESTINATION_PORT"),
                admission("SIGNATURE", { rejected_over_quota: 1893, untracked_entities: 310, untracked_events: 48210 }),
              ],
            }),
          }),
        ]}
      />,
    );
    const r = row("lab-ips-01");
    expect(r.getByText("Overflowing")).toBeInTheDocument();
    expect(
      r.getByText("Signature: 1,893 refused over quota, 310 untracked entities, 48,210 untracked events"),
    ).toBeInTheDocument();
    expect(r.queryByText(/^Destination port:.*refused/)).toBeNull();
  });

  it("says admission was not measured when no entity dimension carried a figure", () => {
    const blank = {
      admitted: null,
      below_admission_threshold: null,
      rejected_over_quota: null,
      untracked_entities: null,
      untracked_events: null,
      admission_truncated: null,
    };
    render(
      <IpsSourcesTable
        sources={[
          ipsSource({
            latest_bucket: latestBucket({ admission: [{ dimension: "SIGNATURE", ...blank }] }),
          }),
        ]}
      />,
    );
    const r = row("lab-ips-01");
    expect(r.getByText("Not measured")).toBeInTheDocument();
    expect(r.queryByText("None")).toBeNull();
  });

  it("links the profile settings of each source", () => {
    render(<IpsSourcesTable sources={[ipsSource()]} />);
    expect(screen.getByRole("link", { name: "Profile settings" })).toHaveAttribute(
      "href",
      "/admin/ips-sources/1b5c0000-0000-4000-8000-000000000501",
    );
  });
});
