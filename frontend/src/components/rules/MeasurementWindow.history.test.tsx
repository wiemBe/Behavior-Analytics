import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { RuleStatisticsWindow } from "@/lib/api";

import { MeasurementWindow } from "./MeasurementWindow";

const win = (over: Partial<RuleStatisticsWindow> = {}): RuleStatisticsWindow => ({
  days: 7,
  span_start: "2026-10-06T10:00:00Z",
  span_end: "2026-10-13T10:00:00Z",
  span_seconds: 604800,
  window_seconds: 3600,
  expected_windows: 168,
  complete_windows: 168,
  truncated_windows: 0,
  failed_windows: 0,
  missing_windows: 0,
  measured_seconds: 604800,
  complete: true,
  reason: null,
  ...over,
});

const region = () => screen.getByRole("region", { name: "Measurement window" });

describe("MeasurementWindow: windows read from QRadar history", () => {
  it("states n of m complete windows when some were read from history", () => {
    render(<MeasurementWindow window={win({ history_windows: 48 })} />);
    expect(region()).toHaveTextContent(
      "48 of 168 complete windows were read from QRadar history, not judged.",
    );
  });

  it("states it on a partial window too", () => {
    render(
      <MeasurementWindow
        window={win({ complete: false, complete_windows: 100, history_windows: 20, reason: "Some windows are missing." })}
      />,
    );
    expect(region()).toHaveTextContent("20 of 100 complete windows were read from QRadar history");
  });

  it("adds nothing for zero or an absent count", () => {
    const { rerender } = render(<MeasurementWindow window={win({ history_windows: 0 })} />);
    expect(region()).not.toHaveTextContent("history");
    rerender(<MeasurementWindow window={win()} />);
    expect(region()).not.toHaveTextContent("history");
  });
});
