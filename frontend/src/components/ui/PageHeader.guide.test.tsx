import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { guideFor } from "@/lib/guideRoutes";
import { setTestLocale } from "../../../vitest.setup";
import { PageHeader } from "./PageHeader";

describe("PageHeader guide link", () => {
  it("links a list route to its section with a name that names the section", () => {
    render(<PageHeader title="Anomalies" guide={guideFor("/anomalies")} />);
    const link = screen.getByRole("link", { name: "Guide: Anomalies" });
    expect(link).toHaveAttribute("href", "/guide/anomalies");
    expect(link).toHaveTextContent("Guide");
  });

  it("links a detail route to the section that describes it", () => {
    render(<PageHeader title="web-fw-01" guide={guideFor("/anomalies/[id]")} />);
    expect(screen.getByRole("link", { name: "Guide: Anomaly investigation" })).toHaveAttribute(
      "href",
      "/guide/anomaly-detail",
    );
  });

  it("is Kılavuz in Turkish, with the Turkish section title", () => {
    setTestLocale("tr");
    render(<PageHeader title="Anomaliler" guide={guideFor("/admin/users")} />);
    const link = screen.getByRole("link", { name: /^Kılavuz: / });
    expect(link).toHaveTextContent("Kılavuz");
    expect(link).toHaveAttribute("href", "/guide/users-and-directory");
  });

  it("sits after the page's own actions, in the same area", () => {
    render(<PageHeader title="Rules" actions={<a href="/x">Statistics</a>} guide="rules" />);
    const area = screen.getByRole("link", { name: "Statistics" }).parentElement!;
    expect(Array.from(area.children).map((el) => el.textContent)).toEqual(["Statistics", "Guide"]);
  });

  it("shows no link without a guide prop, and none for an id the guide lacks", () => {
    const { rerender } = render(<PageHeader title="Plain" />);
    expect(screen.queryByRole("link")).toBeNull();
    rerender(<PageHeader title="Plain" guide="no-such-section" />);
    expect(screen.queryByRole("link")).toBeNull();
    rerender(<PageHeader title="Plain" guide="constructor" />);
    expect(screen.queryByRole("link")).toBeNull();
  });
});
