// The alert bell: a count, a quiet zero, and no number at all when the read
// failed or was refused.

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AlertBell } from "./AlertBell";
import type { Loaded } from "./TopBar";
import type { SocOverview } from "@/lib/api";

const ok = (open: number): Loaded<SocOverview> => ({
  state: "ok",
  data: { alerts: { open, acknowledged: 0, by_severity: {} } } as SocOverview,
});

describe("AlertBell", () => {
  it("shows the count, links to the OPEN list, and takes the crit tone above zero", () => {
    render(<AlertBell overview={ok(3)} />);
    const link = screen.getByRole("link", { name: "Open alerts: 3" });
    expect(link).toHaveAttribute("href", "/alerts?status=OPEN");
    expect(link.querySelector(".pill")).toHaveTextContent("3");
    expect(link.querySelector(".pill")).toHaveClass("crit");
  });

  it("shows a real zero without the crit tone", () => {
    render(<AlertBell overview={ok(0)} />);
    const link = screen.getByRole("link", { name: "Open alerts: 0" });
    expect(link.querySelector(".pill")).toHaveTextContent("0");
    expect(link.querySelector(".pill")).not.toHaveClass("crit");
  });

  it.each([["error"], ["forbidden"]] as const)("shows no number when the read is %s", (state) => {
    render(<AlertBell overview={{ state }} />);
    const link = screen.getByRole("link", { name: "Open alerts: unavailable" });
    expect(link).toHaveAttribute("title", "Alert count unavailable");
    expect(link.textContent).not.toMatch(/\d/);
    expect(link.querySelector(".pill")).not.toHaveClass("crit");
  });

  it("shows no number while the first read is pending", () => {
    render(<AlertBell overview={{ state: "loading" }} />);
    const link = screen.getByRole("link", { name: "Open alerts: loading" });
    expect(link.textContent).not.toMatch(/\d/);
    expect(link).not.toHaveAttribute("title");
  });
});
