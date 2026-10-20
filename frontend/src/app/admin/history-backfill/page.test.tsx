import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import Page from "./page";
import Loading from "./loading";
import HistoryBackfillError from "./error";
import { api } from "@/lib/api";

describe("history backfill route", () => {
  it("has exactly one h1 and states the window source and the time zone as facts", () => {
    vi.spyOn(api, "historyBackfill").mockReturnValue(new Promise(() => {}));
    render(<Page />);
    const h1s = screen.getAllByRole("heading", { level: 1 });
    expect(h1s).toHaveLength(1);
    expect(h1s[0]).toHaveTextContent("History backfill");
    expect(screen.getByText(/Times are UTC\./)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/real-time|\blive\b|!/i);
  });

  it("keeps its heading in the loading boundary", () => {
    render(<Loading />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("History backfill");
    expect(screen.getByRole("status")).toHaveTextContent("Loading the history backfill status");
  });

  it("explains a render failure without the exception, and offers to retry", () => {
    const reset = vi.fn();
    render(
      <HistoryBackfillError
        error={Object.assign(new Error("secret stack"), { digest: "ref-9" })}
        reset={reset}
      />,
    );
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("The history backfill could not be displayed.");
    expect(alert).toHaveTextContent("Nothing on it was changed.");
    expect(alert).toHaveTextContent("Reference: ref-9.");
    expect(alert).not.toHaveTextContent("secret stack");
    screen.getByRole("button", { name: "Try again" }).click();
    expect(reset).toHaveBeenCalledTimes(1);
  });
});
