import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { UtcTime, utcTimeHtml, utcTimeText } from "./UtcTime";

const ISO = "2026-10-12T09:15:42Z";

describe("UtcTime", () => {
  it("renders the full instant as a time element whose datetime is the source value", () => {
    render(<UtcTime value={ISO} />);
    const time = screen.getByText("2026-10-12 09:15:42 UTC");
    expect(time.tagName).toBe("TIME");
    expect(time).toHaveAttribute("datetime", ISO);
    expect(time).not.toHaveAttribute("title");
  });

  it("renders the compact instant and titles the full one", () => {
    render(<UtcTime value={ISO} variant="compact" />);
    const time = screen.getByText("10-12 09:15 UTC");
    expect(time).toHaveAttribute("datetime", ISO);
    expect(time).toHaveAttribute("title", "2026-10-12 09:15:42 UTC");
  });

  it("drops the zone from compact text only when the header already names it", () => {
    render(<UtcTime value={ISO} variant="compact" bare />);
    const time = screen.getByText("10-12 09:15");
    expect(time).toHaveAttribute("title", "2026-10-12 09:15:42 UTC");
  });

  it("places a relative age after the absolute time, and omits the missing mark", () => {
    const { rerender } = render(<UtcTime value={ISO} variant="compact" age="12 min ago" />);
    const time = screen.getByText("10-12 09:15 UTC");
    expect(time.parentElement).toHaveTextContent("10-12 09:15 UTC 12 min ago");
    expect(time.nextElementSibling).toHaveTextContent("12 min ago");
    expect(time.compareDocumentPosition(time.nextElementSibling!)).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );

    rerender(<UtcTime value={ISO} age="—" />);
    expect(screen.getByText("2026-10-12 09:15:42 UTC").nextElementSibling).toBeNull();
  });

  it("renders a missing or unparsable instant as an em dash, with no time element", () => {
    const { rerender } = render(<UtcTime value={null} />);
    expect(screen.getByText("—").tagName).not.toBe("TIME");
    expect(screen.queryByRole("time")).toBeNull();
    rerender(<UtcTime value="not-a-date" />);
    expect(screen.getByText("—").tagName).not.toBe("TIME");
    rerender(<UtcTime value="" />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("lets a caller override the visible label without losing the datetime or the full title", () => {
    render(<UtcTime value={ISO}>axis</UtcTime>);
    const time = screen.getByText("axis");
    expect(time).toHaveAttribute("datetime", ISO);
    expect(time).toHaveAttribute("title", "2026-10-12 09:15:42 UTC");
  });
});

describe("utcTimeText and utcTimeHtml", () => {
  it("matches the component's text", () => {
    expect(utcTimeText(ISO)).toBe("2026-10-12 09:15:42 UTC");
    expect(utcTimeText(ISO, "compact")).toBe("10-12 09:15 UTC");
    expect(utcTimeText(ISO, "compact", true)).toBe("10-12 09:15");
    expect(utcTimeText(null)).toBe("—");
  });

  it("emits a time element for a chart tooltip, and a dash when there is no instant", () => {
    expect(utcTimeHtml(ISO, "compact")).toBe(
      '<time datetime="2026-10-12T09:15:42Z" title="2026-10-12 09:15:42 UTC">10-12 09:15 UTC</time>',
    );
    expect(utcTimeHtml("not-a-date")).toBe("—");
    expect(utcTimeHtml(null)).toBe("—");
  });
});
