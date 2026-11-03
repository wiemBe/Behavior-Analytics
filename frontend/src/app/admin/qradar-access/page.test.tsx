import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { qradarAccessApi } from "@/lib/api";

import QRadarAccessError from "./error";
import Loading from "./loading";
import Page from "./page";

describe("QRadar access route", () => {
  it("has exactly one h1, a guide link, and states that times are UTC", async () => {
    vi.spyOn(qradarAccessApi, "get").mockReturnValue(new Promise(() => {}));
    render(await Page());
    const h1s = screen.getAllByRole("heading", { level: 1 });
    expect(h1s).toHaveLength(1);
    expect(h1s[0]).toHaveTextContent("QRadar access");
    expect(screen.getByText(/Times are UTC\./)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /guide/i })).toHaveAttribute("href", "/guide/qradar-access");
    expect(document.body.textContent).not.toMatch(/real-time|\blive\b|!/i);
  });

  it("keeps its heading in the loading boundary", () => {
    render(<Loading />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("QRadar access");
    expect(screen.getByRole("status")).toHaveTextContent("Loading the QRadar access results");
  });

  it("explains a render failure without the exception, and offers to retry", () => {
    const reset = vi.fn();
    render(
      <QRadarAccessError
        error={Object.assign(new Error("secret stack"), { digest: "ref-3" })}
        reset={reset}
      />,
    );
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("QRadar access could not be displayed.");
    expect(alert).toHaveTextContent("Reference: ref-3.");
    expect(alert).not.toHaveTextContent("secret stack");
    screen.getByRole("button", { name: "Try again" }).click();
    expect(reset).toHaveBeenCalledTimes(1);
  });
});
