import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { wafEndpoint } from "./fixtures";
import { WafEndpointsTable } from "./WafEndpointsTable";

function rowOf(text: string) {
  return screen.getByText(text).closest("tr") as HTMLElement;
}

describe("WafEndpointsTable", () => {
  it("shows method and path, the virtual host, adequacy and last seen in UTC", () => {
    render(<WafEndpointsTable endpoints={[wafEndpoint()]} />);
    const row = within(rowOf("GET /portal/login"));
    expect(row.getByText("Virtual host portal.lab.example")).toBeInTheDocument();
    expect(row.getByText("131 / 168")).toBeInTheDocument();
    expect(row.getAllByText(/10-13 08:55 UTC/).length).toBeGreaterThan(0);
    expect(row.getByText("Normal")).toBeInTheDocument();
    expect(row.getByText("Active")).toBeInTheDocument();
  });

  it("marks a retired endpoint as retired, with when", () => {
    render(
      <WafEndpointsTable
        endpoints={[wafEndpoint({ is_active: false, retired_at: "2026-10-05T00:00:00Z" })]}
      />,
    );
    const row = within(rowOf("GET /portal/login"));
    expect(row.getByText("Retired")).toBeInTheDocument();
    expect(row.getByText("10-05 00:00 UTC")).toBeInTheDocument();
    expect(row.queryByText("Active")).not.toBeInTheDocument();
  });

  it("says a never-evaluated endpoint is not evaluated, and does not draw it as NORMAL", () => {
    render(<WafEndpointsTable endpoints={[wafEndpoint({ state: null })]} />);
    const row = within(rowOf("GET /portal/login"));
    expect(row.getByText("Not evaluated")).toBeInTheDocument();
    expect(row.getByText("No detector has judged this endpoint")).toBeInTheDocument();
    expect(row.queryByText("Normal")).not.toBeInTheDocument();
  });

  it("keeps INSUFFICIENT_DATA neutral rather than healthy", () => {
    render(<WafEndpointsTable endpoints={[wafEndpoint({ state: "INSUFFICIENT_DATA" })]} />);
    const pill = screen.getByText("Insufficient data");
    expect(pill.className).not.toContain("ok");
    expect(pill.className).not.toContain("crit");
  });

  it("says no baseline was built rather than showing 0 / 0", () => {
    render(
      <WafEndpointsTable
        endpoints={[wafEndpoint({ baseline: { cells_total: 0, reliable_cells: 0 }, baseline_rebuilt_at: null })]}
      />,
    );
    const row = within(rowOf("GET /portal/login"));
    expect(row.getByText("No cells")).toBeInTheDocument();
    expect(row.getByText("No baseline built")).toBeInTheDocument();
    expect(row.getByText("Never rebuilt")).toBeInTheDocument();
    expect(row.queryByText("0 / 0")).not.toBeInTheDocument();
  });

  it("states that no cell is reliable yet when cells exist but none can drive a verdict", () => {
    render(
      <WafEndpointsTable endpoints={[wafEndpoint({ baseline: { cells_total: 3, reliable_cells: 0 } })]} />,
    );
    const row = within(rowOf("GET /portal/login"));
    expect(row.getByText("0 / 3")).toBeInTheDocument();
    expect(row.getByText("None reliable yet")).toBeInTheDocument();
  });

  it("counts active anomalies in words", () => {
    render(<WafEndpointsTable endpoints={[wafEndpoint({ state: "OPEN", active_anomaly_count: 2 })]} />);
    expect(screen.getByText("2 active anomalies")).toBeInTheDocument();
  });

  it("wraps a very long path in its cell rather than widening the table", () => {
    const long = "/" + "segment/".repeat(30) + "end";
    render(<WafEndpointsTable endpoints={[wafEndpoint({ normalized_endpoint: long })]} />);
    expect(screen.getByText(`GET ${long}`).closest("td")).toHaveClass("cell-name");
    expect(screen.getByRole("region", { name: "Tracked endpoints" })).toBeInTheDocument();
  });

  it("right-aligns the numeric columns", () => {
    render(<WafEndpointsTable endpoints={[wafEndpoint()]} />);
    expect(screen.getByRole("columnheader", { name: /Baseline cells/ })).toHaveClass("num");
    expect(screen.getByRole("columnheader", { name: /Last seen/ })).toHaveClass("num");
  });
});
