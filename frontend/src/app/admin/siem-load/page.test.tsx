import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { arielLoadApi } from "@/lib/api";

import SiemLoadError from "./error";
import Loading from "./loading";
import Page from "./page";

async function open(range?: string) {
  vi.spyOn(arielLoadApi, "current").mockReturnValue(new Promise(() => {}));
  const summary = vi.spyOn(arielLoadApi, "summary").mockReturnValue(new Promise(() => {}));
  render(await Page({ searchParams: Promise.resolve(range === undefined ? {} : { range }) }));
  return summary;
}

describe("SIEM load route", () => {
  it("has exactly one h1, a guide link, and states that times are UTC", async () => {
    await open();
    const h1s = screen.getAllByRole("heading", { level: 1 });
    expect(h1s).toHaveLength(1);
    expect(h1s[0]).toHaveTextContent("SIEM load");
    expect(screen.getByText(/Times are UTC\./)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /guide/i })).toHaveAttribute("href", "/guide/siem-load");
    expect(document.body.textContent).not.toMatch(/real-time|\blive\b|!/i);
  });

  it("reads the 24 h summary by default and the window the URL names otherwise", async () => {
    const summary = await open();
    expect(summary).toHaveBeenCalledWith(24, expect.anything());
  });

  it("reads 7 days for ?range=7d", async () => {
    const summary = await open("7d");
    expect(summary).toHaveBeenCalledWith(168, expect.anything());
  });

  it("falls back to 24 h for a range it does not offer", async () => {
    const summary = await open("999d");
    expect(summary).toHaveBeenCalledWith(24, expect.anything());
  });

  it("keeps its heading in the loading boundary", () => {
    render(<Loading />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("SIEM load");
    expect(screen.getByRole("status")).toHaveTextContent("Loading the SIEM load");
  });

  it("explains a render failure without the exception, and offers to retry", () => {
    const reset = vi.fn();
    render(
      <SiemLoadError
        error={Object.assign(new Error("secret stack"), { digest: "ref-9" })}
        reset={reset}
      />,
    );
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("SIEM load could not be displayed.");
    expect(alert).toHaveTextContent("Nothing on it was changed.");
    expect(alert).toHaveTextContent("Reference: ref-9.");
    expect(alert).not.toHaveTextContent("secret stack");
    screen.getByRole("button", { name: "Try again" }).click();
    expect(reset).toHaveBeenCalledTimes(1);
  });
});
