import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RangeChips } from "./RangeChips";
import type { RangeChip } from "@/lib/timeRange";

const HREFS: Record<RangeChip, string> = {
  "1h": "/x?range=1h",
  "24h": "/x?range=24h",
  "7d": "/x",
  "30d": "/x?range=30d",
};

function chips(over: Partial<Parameters<typeof RangeChips>[0]> = {}) {
  return render(
    <>
      <form id="f" />
      <RangeChips
        formId="f"
        chipHrefs={HREFS}
        active=""
        custom={false}
        since=""
        until=""
        window={null}
        emptyText="All time"
        {...over}
      />
    </>,
  );
}

describe("RangeChips", () => {
  it("offers the four relative chips as links and Custom as a button", () => {
    chips();
    expect(screen.getByRole("link", { name: "Last hour" })).toHaveAttribute("href", "/x?range=1h");
    expect(screen.getByRole("link", { name: "Last 30 days" })).toHaveAttribute("href", "/x?range=30d");
    expect(screen.getByRole("button", { name: "Custom" })).toHaveAttribute("aria-expanded", "false");
  });

  it("carries the active chip with the form, and takes it away while Custom is being typed", () => {
    const { container } = chips({
      active: "7d",
      window: { from: "2026-10-29T19:05:00Z", to: "2026-11-05T19:05:00Z", toIsNow: true },
    });
    const hidden = () => container.querySelector('input[name="range"]');
    expect(hidden()).toHaveValue("7d");
    expect(hidden()).toHaveAttribute("form", "f");
    fireEvent.click(screen.getByRole("button", { name: "Custom" }));
    expect(hidden()).toBeNull();
    // The inputs start from the window the chip was showing, in UTC.
    expect(screen.getByLabelText("Start time (UTC)")).toHaveValue("2026-10-29T19:05");
    expect(screen.getByLabelText("End time (UTC)")).toHaveValue("2026-11-05T19:05");
    expect(screen.getByLabelText("Start time (UTC)")).toHaveAttribute("form", "f");
  });

  it("publishes aria-controls only while the Custom panel is mounted", () => {
    chips();
    const button = screen.getByRole("button", { name: "Custom" });
    expect(button).not.toHaveAttribute("aria-controls");
    fireEvent.click(button);
    const id = button.getAttribute("aria-controls")!;
    expect(document.getElementById(id)).not.toBeNull();
  });

  it("prints the window in absolute UTC, or what the list does by default", () => {
    const { rerender } = chips({ emptyText: "Default window, last 24 hours" });
    expect(screen.getByRole("group", { name: "Time window shown" })).toHaveTextContent(
      "Default window, last 24 hours",
    );
    rerender(
      <RangeChips
        formId="f"
        chipHrefs={HREFS}
        active=""
        custom
        since="2026-11-05T00:00:00Z"
        until="2026-11-05T12:00:00Z"
        window={{ from: "2026-11-05T00:00:00Z", to: "2026-11-05T12:00:00Z", toIsNow: false }}
        emptyText=""
      />,
    );
    expect(screen.getByRole("group", { name: "Time window shown" })).toHaveTextContent(
      "2026-11-05 00:00 to 2026-11-05 12:00 UTC",
    );
  });
});
