import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { GUIDE_ROUTES, guideFor } from "@/lib/guideRoutes";
import { GUIDE_SECTIONS } from "@/lib/guideSections";

import ReportsError from "./error";
import ReportsLoading from "./loading";
import ReportsPage from "./page";

vi.mock("@/components/reports/ReportsView", () => ({
  ReportsView: ({ period, start }: { period?: string; start?: string }) => (
    <p data-testid="view">{`view:${period ?? "default"}:${start ?? "-"}`}</p>
  ),
}));

describe("the reports route", () => {
  it("asks the question, with the guide link", async () => {
    render(await ReportsPage({ searchParams: Promise.resolve({}) }));
    expect(screen.getByRole("heading", { level: 1, name: "Reports" })).toBeInTheDocument();
    expect(screen.getByText("How did the period go?")).toBeInTheDocument();
    expect(screen.getByTestId("view")).toHaveTextContent("view:default:-");
    expect(screen.getByRole("link", { name: /guide/i })).toHaveAttribute("href", expect.stringContaining("reports"));
  });

  it("hands the period and the start in the URL to the view", async () => {
    render(
      await ReportsPage({ searchParams: Promise.resolve({ period: "calendar_week", start: "2026-11-02T00:00:00Z" }) }),
    );
    expect(screen.getByTestId("view")).toHaveTextContent("view:calendar_week:2026-11-02T00:00:00Z");
  });

  it("keeps the heading while loading and offers a retry on failure", () => {
    const { unmount } = render(<ReportsLoading />);
    expect(screen.getByRole("heading", { level: 1, name: "Reports" })).toBeInTheDocument();
    unmount();
    const reset = vi.fn();
    render(<ReportsError error={Object.assign(new Error("x"), { digest: "d1" })} reset={reset} />);
    screen.getByRole("button", { name: "Try again" }).click();
    expect(reset).toHaveBeenCalled();
  });
});

describe("the reports guide section", () => {
  it("is a pages-group section for /reports, principal-only, and links the views it names", () => {
    expect(GUIDE_ROUTES["/reports"]).toBe("reports");
    expect(guideFor("/reports")).toBe("reports");
    const section = GUIDE_SECTIONS.find((s) => s.id === "reports");
    expect(section).toMatchObject({
      group: "pages",
      routes: ["/reports"],
      audience: "analyst",
      permissions: [],
    });
    expect(section?.related).toEqual(["alerts", "coverage", "source-behavior"]);
  });
});
