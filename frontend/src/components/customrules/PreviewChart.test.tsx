import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { PreviewSummary, buildPreviewOption, previewText, previewTooltip } from "./PreviewChart";
import { preview } from "./fixtures";
import type { ChartPalette } from "@/lib/chartPalette";

const palette = new Proxy({}, { get: (_target, key) => String(key) }) as ChartPalette;

vi.mock("echarts/core", async () => {
  const actual = await vi.importActual<typeof import("echarts/core")>("echarts/core");
  return { ...actual, use: vi.fn(), init: vi.fn() };
});

describe("custom-rule preview honesty", () => {
  it("leaves NOT_MEASURED as null, never bridges it, and labels the threshold", () => {
    const option = buildPreviewOption(preview(), "edge-web-01", palette) as {
      series: Array<{ connectNulls: boolean; data: Array<[number, number | null]>; markLine: { label: { formatter: string } } }>;
    };
    expect(option.series[0].connectNulls).toBe(false);
    expect(option.series[0].data[1][1]).toBeNull();
    expect(option.series[0].markLine.label.formatter).toBe("Threshold 10 events/s");
  });

  it("encodes a QRadar-sourced subject before returning tooltip HTML", () => {
    const hostile = '<img src=x onerror="globalThis.pwned=true">';
    const html = previewTooltip(preview(), hostile, 2);
    expect(html).not.toContain("<img");
    expect(html).toContain("&lt;img");
    expect(html).toContain("At least 13 events/s");
  });

  it("states lower bounds and maintenance in the text alternative", () => {
    const text = previewText(preview(), "edge-web-01");
    expect(text).toContain("Would have fired 1 times");
    expect(text).toContain("1 lower-bound bucket reads as at least");
    expect(text).toContain("1 maintenance bucket");
  });

  it("calls out an all-unmeasured window and an unmeasured majority", () => {
    const allMissing = preview({
      measured_count: 0,
      unmeasured_count: 3,
      buckets: [
        { bucket_start: "2026-10-16T07:00:00Z", value: null, measurement: "NOT_MEASURED", completeness: null, in_maintenance: false },
        { bucket_start: "2026-10-16T07:05:00Z", value: null, measurement: "NOT_MEASURED", completeness: "PARTIAL", in_maintenance: false },
        { bucket_start: "2026-10-16T07:10:00Z", value: null, measurement: "NOT_MEASURED", completeness: null, in_maintenance: false },
      ],
    });
    render(<PreviewSummary preview={allMissing} />);
    expect(screen.getByText(/Most buckets.*not measured/)).toBeInTheDocument();
    expect(screen.getByText(/Every bucket is unmeasured/)).toBeInTheDocument();
  });

  it("labels lower-bound and maintenance buckets beside the chart", () => {
    render(<PreviewSummary preview={preview()} />);
    expect(screen.getByText(/Diamond marks are lower bounds/)).toBeInTheDocument();
    expect(screen.getByText(/Maintenance buckets are shaded/)).toBeInTheDocument();
  });
});
