import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { HealthDistribution } from "./HealthDistribution";
import type { HealthGroup } from "@/lib/behaviorOverview";

const groups: HealthGroup[] = [
  { key: "attention", label: "Attention required", count: 3, tone: "crit" },
  { key: "insufficient", label: "Insufficient data", count: 1, tone: "" },
  { key: "normal", label: "Normal", count: 27, tone: "ok" },
];

describe("HealthDistribution", () => {
  it("draws segments in proportion to their counts, with no inline style", () => {
    const { container } = render(
      <HealthDistribution groups={groups} total={31} summary="3 need attention." />,
    );
    const rects = Array.from(container.querySelectorAll("rect"));
    expect(rects.map((r) => Number(r.getAttribute("width")))).toEqual([3, 1, 27]);
    expect(rects.map((r) => Number(r.getAttribute("x")))).toEqual([0, 3, 4]);
    expect(container.querySelector("svg")).toHaveAttribute("viewBox", "0 0 31 1");
    expect(container.querySelector("[style]")).toBeNull();
  });

  it("lists every count as text, so the bar is not the only carrier", () => {
    render(<HealthDistribution groups={groups} total={31} summary="3 need attention." />);
    expect(screen.getByText("Attention required")).toBeInTheDocument();
    expect(screen.getByText("Insufficient data")).toBeInTheDocument();
    expect(screen.getByText("27")).toBeInTheDocument();
    expect(screen.getByText("3 need attention.")).toBeInTheDocument();
  });

  it("hides the bar itself from assistive technology", () => {
    const { container } = render(
      <HealthDistribution groups={groups} total={31} summary="s" />,
    );
    expect(container.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
  });

  it("keeps insufficient data neutral, never the positive tone", () => {
    const { container } = render(
      <HealthDistribution groups={groups} total={31} summary="s" />,
    );
    const seg = container.querySelectorAll("rect")[1];
    expect(seg.getAttribute("class")).not.toMatch(/\bok\b/);
  });

  it("says what is empty when there are no monitored sources", () => {
    render(<HealthDistribution groups={[]} total={0} summary="" />);
    expect(screen.getByText(/No monitored log sources/)).toBeInTheDocument();
  });
});
