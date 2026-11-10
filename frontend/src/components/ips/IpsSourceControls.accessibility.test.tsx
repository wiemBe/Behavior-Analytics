import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import {
  IpsDimensionFilter,
  IpsRangeSelector,
  IpsSeriesSelector,
  sourceHref,
  type IpsSourceQuery,
} from "./IpsSourceControls";

const query: IpsSourceQuery = { range: "24h", series: "events", dimension: undefined };

function controls(q = query) {
  return render(
    <>
      <IpsRangeSelector id="s1" query={q} />
      <IpsSeriesSelector id="s1" query={q} />
      <IpsDimensionFilter id="s1" query={q} />
    </>,
  );
}

describe("IPS source controls", () => {
  it("are three named navigation landmarks", () => {
    controls();
    expect(screen.getByRole("navigation", { name: "Range" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Series" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Entity dimension" })).toBeInTheDocument();
  });

  it("are reached by Tab in reading order: range, series, then dimension filter", async () => {
    controls();
    const user = userEvent.setup();
    const seen: string[] = [];
    for (let i = 0; i < 9; i += 1) {
      await user.tab();
      seen.push(document.activeElement?.textContent ?? "");
    }
    expect(seen).toEqual([
      "24 hours",
      "7 days",
      "Total events",
      "Top source IP share",
      "Port fan-out",
      "Blocked share",
      "All",
      "Destination port",
      "Destination",
    ]);
  });

  it("mark exactly one active option per control with aria-current", () => {
    controls({ range: "7d", series: "fanout", dimension: "SIGNATURE" });
    const current = screen.getAllByRole("link").filter((a) => a.getAttribute("aria-current") === "page");
    expect(current.map((a) => a.textContent)).toEqual(["7 days", "Port fan-out", "Signature"]);
  });

  it("build links that keep the other choices, leave out defaults and drop the page offset", () => {
    expect(sourceHref("s1", query)).toBe("/behavior/ips/sources/s1");
    expect(sourceHref("s1", { range: "7d", series: "fanout", dimension: "SIGNATURE" }, { range: "24h" })).toBe(
      "/behavior/ips/sources/s1?series=fanout&dimension=SIGNATURE",
    );
  });
});
