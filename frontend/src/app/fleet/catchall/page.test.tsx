import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, catchallApi } from "@/lib/api";

import CatchallError from "./error";
import { sender, sources } from "./fixtures";
import Loading from "./loading";
import CatchallPage from "./page";

vi.mock("./SourceVolume", () => ({ SourceVolume: () => <section aria-label="volume stub" /> }));

async function renderPage() {
  return render(await CatchallPage());
}

beforeEach(() => vi.restoreAllMocks());

describe("CatchallPage", () => {
  it("opens with the queue, then the volume, under one h1", async () => {
    vi.spyOn(catchallApi, "sources").mockResolvedValue(sources());
    const senders = vi
      .spyOn(catchallApi, "senders")
      .mockResolvedValue({ items: [sender()], total: 1, limit: 50, offset: 0 });
    await renderPage();
    expect(senders).toHaveBeenCalledWith(expect.objectContaining({ review_status: "NEW" }));
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    const h2 = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
    expect(h2).toEqual(["Senders queue"]);
    expect(screen.getByRole("region", { name: "volume stub" })).toBeInTheDocument();
    // What a sender is, said once.
    expect(screen.getAllByText(/logsourceidentifier/)).toHaveLength(1);
  });

  it("names the catch-all types when the inventory holds no catch-all source", async () => {
    vi.spyOn(catchallApi, "sources").mockResolvedValue(sources({ items: [] }));
    const senders = vi.spyOn(catchallApi, "senders");
    await renderPage();
    expect(screen.getByText("No catch-all source is in the inventory.")).toBeInTheDocument();
    expect(screen.getByText(/39, 67, 212, 367, 4000/)).toBeInTheDocument();
    expect(senders).not.toHaveBeenCalled();
  });

  it("names the permission on a 403", async () => {
    vi.spyOn(catchallApi, "sources").mockRejectedValue(new ApiError(403, "no"));
    await renderPage();
    expect(screen.getByRole("alert")).toHaveTextContent("read:fleet");
  });

  it("says what failed and what to do on any other error", async () => {
    vi.spyOn(catchallApi, "sources").mockRejectedValue(new Error("boom"));
    await renderPage();
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(/could not be loaded/);
    expect(alert).toHaveTextContent(/reload/);
    expect(alert).not.toHaveTextContent("boom");
  });

  it("has a loading and an error state", () => {
    render(<Loading />);
    expect(screen.getByRole("status")).toHaveTextContent("catch-all senders");
    render(<CatchallError error={new Error("x")} reset={() => {}} />);
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });
});
