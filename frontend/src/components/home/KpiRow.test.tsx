import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { forbidden } from "./KpiTestStates";
import { KpiRow } from "./KpiRow";

vi.mock("@/hooks/useApiResource", () => ({
  useApiResource: (key: string) =>
    key.startsWith("/offenses/analytics") ? forbidden : { status: "loading" },
}));

describe("KpiRow", () => {
  it("renders four cards, each with its own drill-down", () => {
    render(<KpiRow />);
    expect(screen.getAllByRole("article")).toHaveLength(4);
    const hrefs = screen.getAllByRole("link").map((a) => a.getAttribute("href"));
    expect(hrefs).toEqual([
      "/fleet",
      "/behavior/sources",
      "/offenses?status=OPEN",
      "/anomalies?active_only=true",
    ]);
  });

  it("lets one card be refused while the others keep their own state", () => {
    render(<KpiRow />);
    expect(screen.getByText(/permission read:offenses/)).toBeInTheDocument();
    expect(screen.getAllByText(/^Loading /)).toHaveLength(3);
  });

  it("stretches every card border to the height of its grid row", () => {
    const css = readFileSync(resolve(process.cwd(), "src/components/home/kpi.css"), "utf8");
    expect(css).toMatch(/\.kpi-grid\s*\{[^}]*align-items:\s*stretch/s);
    expect(css).not.toMatch(/\.kpi-grid\s*\{[^}]*align-items:\s*start/s);
  });
});
