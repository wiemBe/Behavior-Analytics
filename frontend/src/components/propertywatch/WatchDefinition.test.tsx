import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { catalog, iso, scope, watch } from "./fixtures";
import { WatchDefinition } from "./WatchDefinition";

describe("the Definition tab cadence", () => {
  it.each([
    [300, "Every 5 min"],
    [3600, "Every 1 h"],
    [8 * 3600, "Every 8 h"],
  ])("states a %s-second family cadence", (cadence_seconds, expected) => {
    render(
      <WatchDefinition
        watch={watch({ cadence_seconds })}
        catalog={catalog()}
        scope={scope()}
      />,
    );
    const cadence = screen.getByText(expected, { exact: false });
    expect(cadence).toHaveTextContent("PROPERTY_WATCH family's setting");
    expect(cadence).toHaveTextContent("buckets are five minutes wide");
    expect(screen.getByRole("link", { name: "Search cadence" })).toHaveAttribute(
      "href",
      "/admin/search-cadence",
    );
  });
});

describe("the Definition tab history", () => {
  it("states the request, window, floor, gaps and current membership limitation", () => {
    const base = watch();
    render(
      <WatchDefinition
        watch={watch({
          history: {
            ...base.history,
            state: "PARTIAL",
            days_requested: 28,
            target_start: "2026-09-20T09:00:00Z",
            floor: "2026-10-10T09:00:00Z",
            through: "2026-10-18T08:55:00Z",
            end: "2026-10-18T09:00:00Z",
            days_read: 9,
            gaps: [{ start: iso(0), end: iso(1), reason: "search failed 3 times at the minimum width" }],
          },
        })}
        catalog={catalog()}
        scope={scope()}
      />,
    );
    expect(screen.getByText("28 days")).toBeInTheDocument();
    expect(screen.getByText("History floor").closest("div")).toHaveTextContent("2026-10-10 09:00:00 UTC");
    expect(screen.getByRole("list", { name: "History gaps" })).toHaveTextContent("could not be read (search failed 3 times at the minimum width).");
    expect(screen.getByText(/membership of a group is today's/)).toBeInTheDocument();
  });
});

describe("the coverage floor sentence", () => {
  it("spells the floor as a whole percent with U+00A0 before the sign", () => {
    render(<WatchDefinition watch={watch()} catalog={catalog()} scope={scope()} />);
    const sentence = screen.getByText((_, node) => /coverage\s+floor is/.test(node?.textContent ?? "") && node?.tagName === "P");
    expect(sentence.textContent).toContain("floor is 90\u00A0%:");
  });
});
