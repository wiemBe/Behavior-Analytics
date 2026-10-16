import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { wafEndpoint, wafSource } from "./fixtures";
import { WafEndpointsTable } from "./WafEndpointsTable";
import { WafSourcesTable } from "./WafSourcesTable";

describe("WafEndpointsTable: zone said once", () => {
  it("names the zone in the Last seen header and not in that column's cells", () => {
    render(<WafEndpointsTable endpoints={[wafEndpoint()]} />);
    expect(screen.getByRole("columnheader", { name: /Last seen \(UTC\)/ })).toBeInTheDocument();
    const cell = screen.getByText("GET /portal/login").closest("tr")!.querySelectorAll("td")[2];
    expect(cell.textContent).toContain("10-13 08:55");
    expect(cell.textContent).not.toContain("UTC");
    expect(cell.querySelector("time")).toHaveAttribute("title", expect.stringContaining("UTC"));
  });
});

describe("WafEndpointsTable: path breaks at slashes", () => {
  it("offers a break opportunity after every slash and keeps the whole path in the text", () => {
    const path = "/api/v2/organizations/{id}/projects";
    render(<WafEndpointsTable endpoints={[wafEndpoint({ normalized_endpoint: path })]} />);
    const el = screen.getByText(`GET ${path}`);
    expect(el.querySelectorAll("wbr")).toHaveLength(path.split("/").length - 1);
    for (const wbr of Array.from(el.querySelectorAll("wbr"))) {
      expect(wbr.previousSibling?.textContent?.endsWith("/")).toBe(true);
    }
  });
});

describe("WafSourcesTable: a missing count carries no unit", () => {
  it("shows a bare dash for null overflow counts, never '— refused over quota'", () => {
    render(
      <WafSourcesTable
        sources={[
          wafSource({
            admission: {
              buckets_in_range: 12,
              buckets_admission_truncated: null,
              rejected_over_quota: null,
              below_admission_threshold: null,
              untracked_requests: null,
            },
          }),
        ]}
      />,
    );
    const row = within(screen.getByRole("link", { name: "lab-waf-behavior-01" }).closest("tr")!);
    const text = row.getAllByRole("cell")[5].textContent ?? "";
    expect(text).not.toMatch(/—\s*(of|refused|below|untracked)/);
    expect(text).not.toMatch(/—[^—]*(refused over quota|below admission|untracked requests|buckets truncated)/);
    expect(row.getAllByText("—").length).toBeGreaterThanOrEqual(4);
  });

  it("still words a measured zero", () => {
    render(
      <WafSourcesTable
        sources={[
          wafSource({
            admission: {
              buckets_in_range: 12,
              buckets_admission_truncated: 0,
              rejected_over_quota: 0,
              below_admission_threshold: 0,
              untracked_requests: 0,
            },
          }),
        ]}
      />,
    );
    expect(screen.getByText(/0 refused over quota/)).toBeInTheDocument();
    expect(screen.getByText(/0 untracked requests/)).toBeInTheDocument();
  });
});
