import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { homeReads } from "@/lib/api";
import { offenseRow, page } from "@/lib/home/fixtures";
import { recentOffensesRead } from "@/lib/home/recentOffenses";

import { failed, forbidden, loading, ok } from "./testkit";
import { RecentOffenses } from "./RecentOffenses";

const res = vi.hoisted(() => ({ byKey: {} as Record<string, unknown> }));
vi.mock("@/hooks/useApiResource", () => ({
  useApiResource: (key: string | null) => res.byKey[key ?? ""] ?? { status: "loading" },
}));

const OPEN = recentOffensesRead.key;
const PROBE = homeReads.offenseProbe().key;

function setup(open: unknown, probe: unknown = ok(page([offenseRow()], 9))) {
  res.byKey = { [OPEN]: open, [PROBE]: probe };
}

beforeEach(() => {
  res.byKey = {};
});

describe("RecentOffenses", () => {
  it("draws a ten-segment bar filled to the magnitude, beside the number", () => {
    setup(ok(page([offenseRow({ magnitude: 7 })])));
    const { container } = render(<RecentOffenses />);
    expect(container.querySelectorAll("rect.tw-seg")).toHaveLength(10);
    expect(container.querySelectorAll("rect.tw-seg.on")).toHaveLength(7);
    expect(screen.getByText("7")).toBeInTheDocument();
    expect(container.querySelector("[style]")).toBeNull();
  });

  it("draws no segment at all for a null magnitude, only a dash", () => {
    setup(ok(page([offenseRow({ magnitude: null, last_updated_time: "2026-10-14T09:00:00Z" })])));
    const { container } = render(<RecentOffenses />);
    expect(container.querySelectorAll("rect.tw-seg")).toHaveLength(0);
    const row = screen.getByRole("link", { name: /Offense 1/ }).closest("tr")!;
    expect(within(row).getByText("—")).toBeInTheDocument();
  });

  it("keeps a measured magnitude of 0 as 0 with no filled segment", () => {
    setup(ok(page([offenseRow({ magnitude: 0 })])));
    const { container } = render(<RecentOffenses />);
    expect(container.querySelectorAll("rect.tw-seg")).toHaveLength(10);
    expect(container.querySelectorAll("rect.tw-seg.on")).toHaveLength(0);
    expect(screen.getByText("0")).toBeInTheDocument();
  });

  it("shows the update time in UTC with its zone, a datetime attribute and its age", () => {
    setup(ok(page([offenseRow({ last_updated_time: "2026-10-14T09:15:00Z" })])));
    const { container } = render(<RecentOffenses />);
    const time = container.querySelector("td time");
    expect(time).toHaveAttribute("datetime", "2026-10-14T09:15:00Z");
    expect(time).toHaveTextContent("10-14 09:15 UTC");
  });

  it("prints a dash, not a date, when no update time was recorded", () => {
    setup(ok(page([offenseRow({ last_updated_time: null })])));
    const { container } = render(<RecentOffenses />);
    expect(container.querySelector("td time")).toBeNull();
  });

  it("links each row to its offense and 'View all' to the list", () => {
    setup(ok(page([offenseRow({ qradar_offense_id: 42, description: "Port scan" })])));
    render(<RecentOffenses />);
    expect(screen.getByRole("link", { name: "Port scan" })).toHaveAttribute("href", "/offenses/42");
    expect(screen.getByRole("link", { name: "View all" })).toHaveAttribute("href", "/offenses");
  });

  it("says NOT_COLLECTED when no snapshot exists, not 'no open offenses'", () => {
    setup(ok(page([])), ok(page([], 0)));
    render(<RecentOffenses />);
    expect(screen.getByText("Not collected")).toBeInTheDocument();
    expect(screen.getByText(/No offense snapshot has been collected yet/)).toBeInTheDocument();
    expect(screen.queryByText(/No open offenses/)).toBeNull();
  });

  it("says no open offenses only when a snapshot exists", () => {
    setup(ok(page([])), ok(page([offenseRow({ status: "CLOSED" })], 3)));
    render(<RecentOffenses />);
    expect(screen.getByText("No open offenses in the latest snapshot.")).toBeInTheDocument();
  });

  it("fails closed when the snapshot probe failed", () => {
    setup(ok(page([])), failed());
    render(<RecentOffenses />);
    expect(screen.getByText(/Could not confirm that an offense snapshot exists/)).toBeInTheDocument();
    expect(screen.queryByText(/No open offenses/)).toBeNull();
  });

  it("is loading text in a polite live region", () => {
    setup(loading);
    render(<RecentOffenses />);
    expect(screen.getByRole("status")).toHaveTextContent(/Loading the open offenses/);
  });

  it("names the permission when refused", () => {
    setup(forbidden);
    render(<RecentOffenses />);
    expect(screen.getByText(/Your role cannot read the open offenses \(permission read:offenses\)/)).toBeInTheDocument();
  });

  it("says what failed, and not the exception", () => {
    setup(failed(500));
    render(<RecentOffenses />);
    expect(screen.getByRole("alert")).toHaveTextContent(/could not be read/);
  });

  it("keeps the old rows and says so when a refresh failed", () => {
    setup(ok(page([offenseRow({ description: "Kept" })]), { refreshFailed: { httpStatus: 500 } }));
    render(<RecentOffenses />);
    expect(screen.getByRole("link", { name: "Kept" })).toBeInTheDocument();
    expect(screen.getByText(/latest refresh failed/)).toBeInTheDocument();
  });
});
