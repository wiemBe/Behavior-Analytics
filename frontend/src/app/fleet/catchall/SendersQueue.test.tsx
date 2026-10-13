import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, catchallApi, type CatchallSender } from "@/lib/api";

import { SEARCH_DEBOUNCE_MS, SendersQueue } from "./SendersQueue";
import { sender, sources } from "./fixtures";

function page(items: CatchallSender[], total = items.length) {
  return { items, total, limit: 50, offset: 0 };
}

function renderQueue(items: CatchallSender[], src = sources()) {
  return render(<SendersQueue sources={src} initial={page(items)} />);
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(catchallApi, "sources").mockResolvedValue(sources());
});

describe("the queue", () => {
  it("keeps the order the API sent and shows unknown, never 0", () => {
    renderQueue([
      sender({ id: "a", identifier: "zz-last-alpha", events_24h: 5 }),
      sender({ id: "b", identifier: "aa-first-alpha", events_24h: null }),
    ]);
    const rows = screen.getAllByRole("row").slice(1);
    expect(within(rows[0]).getByText("zz-last-alpha")).toBeInTheDocument();
    expect(within(rows[1]).getByText("aa-first-alpha")).toBeInTheDocument();
    expect(within(rows[1]).getByText("unknown")).toBeInTheDocument();
    expect(within(rows[1]).queryByText("0")).toBeNull();
  });

  it("gives a long sender its full value as a tooltip and a lower-bound note on the figure", () => {
    const long = "x".repeat(120) + ".example.internal";
    renderQueue([sender({ identifier: long })]);
    expect(screen.getByText(long)).toHaveAttribute("title", long);
    expect(screen.getAllByTitle(/lower bound/i).length).toBeGreaterThan(0);
  });

  it("shows UTC times and who reviewed it", () => {
    renderQueue([
      sender({
        review_status: "REVIEWED",
        reviewed_by: "s.okafor",
        reviewed_at: "2026-10-13T08:02:00Z",
      }),
    ]);
    expect(screen.getByRole("columnheader", { name: "Seen (UTC)" })).toBeInTheDocument();
    // The zone is in the header and the tooltip, not repeated in every Seen cell.
    const seen = screen.getByText("10-13 15:00");
    expect(seen).toHaveAttribute("title", "2026-10-13 15:00:00 UTC");
    expect(seen.textContent).not.toMatch(/UTC/);
    expect(screen.getByText(/by s\.okafor/)).toBeInTheDocument();
  });

  it("offers New first, with the count the API gave, as the default filter", () => {
    renderQueue([sender()]);
    expect(screen.getByRole("combobox", { name: "Review status" })).toHaveValue("NEW");
    expect(screen.getByRole("option", { name: "New (2)" })).toBeInTheDocument();
    expect(catchallApi.sources).not.toHaveBeenCalled();
  });
});

describe("sender names", () => {
  it("offers a line break after . and - only, and keeps the whole name as text and tooltip", () => {
    const { container } = renderQueue([sender({ identifier: "sw-core-a.lab.local" })]);
    const code = container.querySelector("code.sender-id")!;
    expect(code.textContent).toBe("sw-core-a.lab.local");
    expect(code).toHaveAttribute("title", "sw-core-a.lab.local");
    expect(code.querySelectorAll("wbr")).toHaveLength(4);
    expect(code.innerHTML).toBe("sw-<wbr>core-<wbr>a.<wbr>lab.<wbr>local");
  });

  it("says nothing under the New pill, and names the reviewer for a decided sender", () => {
    renderQueue([
      sender({ id: "n" }),
      sender({ id: "d", identifier: "d-host", review_status: "DISMISSED", reviewed_by: "m.keller", reviewed_at: "2026-10-13T08:02:00Z" }),
    ]);
    const rows = screen.getAllByRole("row").slice(1);
    expect(within(rows[0]).queryByText(/by |Awaiting|Reviewer/)).toBeNull();
    expect(within(rows[1]).getByText(/by m\.keller/)).toBeInTheDocument();
  });
});

describe("filters", () => {
  it("asks the API again for a status and a source, and keeps the rows it answers", async () => {
    const spy = vi
      .spyOn(catchallApi, "senders")
      .mockResolvedValue(page([sender({ id: "r", identifier: "reviewed-one", review_status: "REVIEWED" })]));
    renderQueue([sender()]);
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Review status" }), "REVIEWED");
    await screen.findByText("reviewed-one");
    expect(spy).toHaveBeenLastCalledWith(expect.objectContaining({ review_status: "REVIEWED" }));
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Catch-all source" }),
      "Universal LEEF :: leef-gw-eu",
    );
    await waitFor(() =>
      expect(spy).toHaveBeenLastCalledWith(
        expect.objectContaining({ catchall_log_source_id: "5a1f0000-0000-4000-8000-00000000b002" }),
      ),
    );
  });

  it("sends no status for All statuses", async () => {
    const spy = vi.spyOn(catchallApi, "senders").mockResolvedValue(page([sender()]));
    renderQueue([sender()]);
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Review status" }), "ALL");
    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(spy.mock.calls[0][0]?.review_status).toBeUndefined();
  });

  it("sends q once, after the typing pauses", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const spy = vi.spyOn(catchallApi, "senders").mockResolvedValue(page([sender()]));
      renderQueue([sender()]);
      const box = screen.getByRole("searchbox", { name: "Search sender" });
      fireEvent.change(box, { target: { value: "fw" } });
      fireEvent.change(box, { target: { value: "fw-edge" } });
      expect(spy).not.toHaveBeenCalled();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(SEARCH_DEBOUNCE_MS + 20);
      });
      expect(spy).toHaveBeenCalledTimes(1);
      expect(spy).toHaveBeenCalledWith(expect.objectContaining({ q: "fw-edge" }));
    } finally {
      vi.useRealTimers();
    }
  });

  it("shows loading while it waits", async () => {
    let release: (p: ReturnType<typeof page>) => void = () => {};
    vi.spyOn(catchallApi, "senders").mockReturnValue(new Promise((r) => (release = r)));
    renderQueue([sender()]);
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Review status" }), "ALL");
    expect(await screen.findByText(/Loading senders/)).toBeInTheDocument();
    release(page([sender()]));
    await waitFor(() => expect(screen.queryByText(/Loading senders/)).toBeNull());
  });

  it("says what failed and keeps the filters, with a retry", async () => {
    const spy = vi
      .spyOn(catchallApi, "senders")
      .mockRejectedValueOnce(new ApiError(500, "x"))
      .mockResolvedValue(page([sender({ identifier: "back-again" })]));
    renderQueue([sender()]);
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Review status" }), "ALL");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/could not be loaded/);
    expect(screen.getByRole("combobox", { name: "Review status" })).toHaveValue("ALL");
    await userEvent.click(within(alert).getByRole("button", { name: "Try again" }));
    await screen.findByText("back-again");
    expect(spy).toHaveBeenCalledTimes(2);
  });

  it("names the permission on a 403 read and offers no retry", async () => {
    vi.spyOn(catchallApi, "senders").mockRejectedValue(new ApiError(403, "no"));
    renderQueue([sender()]);
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Review status" }), "ALL");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("read:fleet");
    expect(within(alert).queryByRole("button")).toBeNull();
  });
});

describe("empty queue", () => {
  it("says nothing was collected when no bucket was ever measured", () => {
    const never = sources();
    never.items.forEach((s) => (s.latest_bucket_start = null));
    renderQueue([], never);
    expect(screen.getByText("Nothing has been collected yet.")).toBeInTheDocument();
    expect(screen.queryByText("The queue is clear.")).toBeNull();
  });

  it("says the queue is clear when buckets were measured", () => {
    renderQueue([]);
    expect(screen.getByText("The queue is clear.")).toBeInTheDocument();
    expect(screen.queryByText("Nothing has been collected yet.")).toBeNull();
  });

  it("says no sender matches when a search finds nothing", async () => {
    vi.spyOn(catchallApi, "senders").mockResolvedValue(page([]));
    renderQueue([sender()]);
    await userEvent.type(screen.getByRole("searchbox", { name: "Search sender" }), "nope");
    expect(await screen.findByText("No sender matches these filters.")).toBeInTheDocument();
  });
});

describe("review and dismiss", () => {
  it("reviews a sender and shows the API's answer, then refreshes the count", async () => {
    const spy = vi
      .spyOn(catchallApi, "review")
      .mockResolvedValue(
        sender({ review_status: "REVIEWED", reviewed_by: "a.demir", reviewed_at: "2026-10-13T18:01:00Z" }),
      );
    renderQueue([sender()]);
    await userEvent.click(screen.getByRole("button", { name: "Review fw-edge-03" }));
    expect(spy).toHaveBeenCalledWith("s1");
    expect(await screen.findByText(/by a\.demir/)).toBeInTheDocument();
    await waitFor(() => expect(catchallApi.sources).toHaveBeenCalled());
    expect(screen.queryByRole("button", { name: "Review fw-edge-03" })).toBeNull();
  });

  it("dismisses a sender", async () => {
    const spy = vi.spyOn(catchallApi, "dismiss").mockResolvedValue(sender({ review_status: "DISMISSED", reviewed_by: "a.demir", reviewed_at: "2026-10-13T18:01:00Z" }));
    renderQueue([sender()]);
    await userEvent.click(screen.getByRole("button", { name: "Dismiss fw-edge-03" }));
    expect(spy).toHaveBeenCalledWith("s1");
    expect((await screen.findAllByText("Dismissed")).length).toBeGreaterThan(0);
  });

  it("sends one request however often it is clicked while in flight", async () => {
    let release: (s: CatchallSender) => void = () => {};
    const spy = vi.spyOn(catchallApi, "review").mockReturnValue(new Promise((r) => (release = r)));
    renderQueue([sender()]);
    const button = screen.getByRole("button", { name: "Review fw-edge-03" });
    fireEvent.click(button);
    fireEvent.click(button);
    expect(spy).toHaveBeenCalledTimes(1);
    expect(await screen.findByText("Saving…")).toBeInTheDocument();
    expect(button).toBeDisabled();
    expect(screen.getByRole("button", { name: "Dismiss fw-edge-03" })).toBeDisabled();
    release(sender({ review_status: "REVIEWED", reviewed_by: "x", reviewed_at: "2026-10-13T18:01:00Z" }));
    await waitFor(() => expect(screen.queryByText("Saving…")).toBeNull());
  });

  it("rolls back visibly on a failure: the row is unchanged and the reason is shown", async () => {
    vi.spyOn(catchallApi, "review").mockRejectedValue(new ApiError(500, "database is unavailable"));
    renderQueue([sender()]);
    await userEvent.click(screen.getByRole("button", { name: "Review fw-edge-03" }));
    expect(await screen.findByText(/^Not changed\./)).toBeInTheDocument();
    expect(screen.queryByText("Awaiting review")).toBeNull();
    expect(screen.getByRole("button", { name: "Review fw-edge-03" })).toBeEnabled();
  });

  it("disables every action with the reason after a 403, and does not send again", async () => {
    const spy = vi.spyOn(catchallApi, "review").mockRejectedValue(new ApiError(403, "no"));
    renderQueue([sender(), sender({ id: "s2", identifier: "other-host" })]);
    await userEvent.click(screen.getByRole("button", { name: "Review fw-edge-03" }));
    expect(await screen.findAllByText(/log_source:write/)).not.toHaveLength(0);
    const buttons = screen.getAllByRole("button", { name: /^(Review|Dismiss) / });
    expect(buttons).toHaveLength(4);
    buttons.forEach((b) => {
      expect(b).toBeDisabled();
      expect(b).toHaveAttribute("title", expect.stringContaining("log_source:write"));
    });
    fireEvent.click(buttons[2]);
    expect(spy).toHaveBeenCalledTimes(1);
  });
});
