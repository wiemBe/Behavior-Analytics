import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { CatalogStatus } from "./CatalogStatus";
import { catalog } from "./fixtures";

describe("persisted catalog states shared by WAF and IPS", () => {
  it.each([
    ["COMPLETE", "Property list as of 21:40 UTC"],
    ["REFRESHING", "Refreshing…"],
    ["FAILED", "Last refresh failed (timeout) — showing 21:40 UTC list"],
    ["PARTIAL", "Property names loaded — applicability unknown (unavailable)"],
  ] as const)("renders %s in one short line", (state, line) => {
    render(<CatalogStatus catalog={catalog({ catalog_status: state,
      as_of: "2026-11-18T21:40:00Z", failure: state === "FAILED" ? {
        kind: "timeout", endpoint: "property_expressions", failure_class: "ProviderUnavailableError",
        page_start: 500, page_end: 999, attempts: 3, elapsed_seconds: 46,
      } : null })} />);
    expect(screen.getByRole("status")).toHaveTextContent(line);
    expect(screen.getByRole("status")).not.toHaveTextContent("ProviderUnavailableError");
    if (state === "FAILED") expect(screen.getByText(/ProviderUnavailableError/).closest("details")).not.toHaveAttribute("open");
  });
  it("keeps never loaded and failed without a list distinct", () => {
    const { rerender } = render(<CatalogStatus catalog={catalog({ status: "UNAVAILABLE", catalog_status: "NEVER" })} />);
    expect(screen.getByRole("status")).toHaveTextContent("Not loaded yet — names can still be typed");
    rerender(<CatalogStatus catalog={catalog({ status: "UNAVAILABLE", catalog_status: "FAILED" })} />);
    expect(screen.getByRole("status")).toHaveTextContent("Property list failed (unavailable) — names can still be typed");
  });
  it("keeps a retained complete type map distinct from first-load unknown applicability", () => {
    render(<CatalogStatus catalog={catalog({ catalog_status: "PARTIAL", applicability_complete: true,
      as_of: "2026-11-18T21:40:00Z", failure: { kind: "timeout", endpoint: "property_expressions",
        failure_class: "ProviderUnavailableError", page_start: 500, page_end: 999,
        attempts: 3, elapsed_seconds: 46 } })} />);
    expect(screen.getByRole("status")).toHaveTextContent("Last refresh failed (timeout) — showing 21:40 UTC list");
  });
  it("distinguishes an unreadable API response and a complete empty list", () => {
    const { rerender } = render(<CatalogStatus catalog={null} />);
    expect(screen.getByRole("status")).toHaveTextContent("Property list unavailable — names can still be typed");
    rerender(<CatalogStatus catalog={catalog({ catalog_status: "COMPLETE", property_count: 0,
      as_of: "2026-11-18T21:40:00Z", properties: [] })} />);
    expect(screen.getByRole("status")).toHaveTextContent("No custom properties reported as of 21:40 UTC");
  });
  it("shows the configured background interval behind Details", () => {
    render(<CatalogStatus catalog={catalog({ refresh_seconds: 600 })} />);
    expect(screen.getByText("Refresh interval: 600 seconds").closest("details")).not.toHaveAttribute("open");
  });
});
