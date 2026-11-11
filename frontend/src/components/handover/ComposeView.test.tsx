import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { StaticSession } from "@/components/auth/session";
import { session } from "@/components/alerts/suppressionFixtures";
import { ApiError } from "@/lib/api";

import { ComposeView } from "./ComposeView";
import { body, item, openSource, record, SOURCES_OK } from "./fixtures";

const mocks = vi.hoisted(() => ({ preview: vi.fn(), create: vi.fn() }));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, handoverApi: { ...actual.handoverApi, preview: mocks.preview, create: mocks.create } };
});

const WRITER = session(["read:alerts", "anomaly:triage"]);

function mount(who = WRITER) {
  return render(
    <StaticSession value={who}>
      <ComposeView />
    </StaticSession>,
  );
}

const rows = (n: number) =>
  Array.from({ length: n }, (_, i) => item({ id: `r${i}`, title: `Row ${i}`, link: `/alerts/r${i}` }));

const note = () => screen.getByLabelText("Note") as HTMLTextAreaElement;
const type = (text: string) => fireEvent.change(note(), { target: { value: text } });

beforeEach(() => {
  mocks.preview.mockReset();
  mocks.create.mockReset();
  mocks.preview.mockResolvedValue(body({ items: rows(3), total: 3 }));
});

describe("permission", () => {
  it("shows the forbidden state naming anomaly:triage, saves nothing and reads no preview", () => {
    mount(session(["read:alerts"]));
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("anomaly:triage");
    expect(alert).toHaveTextContent("Nothing was saved");
    expect(mocks.preview).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "Save handover" })).toBeNull();
  });

  it("does not guess while the session is still being read", () => {
    mount(session(["anomaly:triage"], "loading"));
    expect(screen.getByRole("status")).toHaveTextContent("Checking your permissions");
    expect(mocks.preview).not.toHaveBeenCalled();
  });

  it("is open to the administrator wildcard", async () => {
    mount(session(["admin:*"]));
    expect(await screen.findByRole("button", { name: "Save handover" })).toBeInTheDocument();
  });
});

describe("the preview", () => {
  it("is loading text first, then the rows with a Watch checkbox each", async () => {
    mocks.preview.mockReturnValue(new Promise(() => {}));
    mount();
    expect(screen.getByText("Reading the rows that would be frozen…")).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("lists the rows to freeze", async () => {
    mount();
    const table = await screen.findByRole("table", { name: "Rows that will be frozen" });
    expect(within(table).getAllByRole("checkbox")).toHaveLength(3);
    expect(screen.getByText("0 of 20 watched")).toBeInTheDocument();
  });

  it("says what failed, keeps the form and recovers on Retry", async () => {
    mocks.preview.mockRejectedValueOnce(new ApiError(500, "x")).mockResolvedValueOnce(body({ items: rows(1), total: 1 }));
    mount();
    expect(await screen.findByText(/The rows to freeze could not be loaded/)).toBeInTheDocument();
    expect(note()).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("table", { name: "Rows that will be frozen" })).toBeInTheDocument();
  });

  it("names a source that was not read, and is not a zero", async () => {
    const sources = SOURCES_OK.map((s) =>
      s.kind === "offense" ? openSource("offense", { state: "FORBIDDEN", count: null, permission: "read:offenses" }) : s,
    );
    mocks.preview.mockResolvedValue(body({ sources, items: rows(1), total: 1 }));
    mount();
    expect(await screen.findByText(/Some sources could not be read for this preview/)).toBeInTheDocument();
    expect(screen.getByText(/Offenses: not read.*read:offenses/)).toBeInTheDocument();
  });

  it("words the sources and the age for a preview, not as a written record", async () => {
    const sources = SOURCES_OK.map((s) =>
      s.kind === "offense"
        ? openSource("offense", { state: "FORBIDDEN", count: null, permission: "read:offenses" })
        : s.kind === "alert"
          ? openSource("alert", { state: "FAILED", count: null, reason_code: "LOAD_FAILED" })
          : s,
    );
    mocks.preview.mockResolvedValue(
      body({ sources, items: [item({ id: "r0", title: "Row 0", link: "/alerts/r0", age_seconds: 25200 })], total: 1 }),
    );
    mount();
    expect(
      await screen.findByText(
        "Offenses: not read. Your account lacks read:offenses, so its rows would not be in this handover.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/failed to load for this preview, so its rows would be missing, not absent/),
    ).toBeInTheDocument();
    expect(screen.getByText("7 h so far")).toBeInTheDocument();
    const text = document.body.textContent ?? "";
    expect(text).not.toMatch(/when written|when this was written|author's account/);
  });

  it("says nothing would be frozen when the queue is empty and every source read", async () => {
    mocks.preview.mockResolvedValue(body({ items: [], total: 0 }));
    mount();
    expect(await screen.findByText("No open rows would be frozen right now.")).toBeInTheDocument();
  });
});

describe("the note", () => {
  it("counts characters and refuses an empty note without calling the backend", async () => {
    mount();
    await screen.findByRole("table", { name: "Rows that will be frozen" });
    expect(screen.getByText("0 of 4,000 characters")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save handover" }));
    expect(await screen.findByText(/Write a note before saving/)).toBeInTheDocument();
    expect(mocks.create).not.toHaveBeenCalled();
  });

  it("blocks a note over 4000 characters and says by how many", async () => {
    mount();
    await screen.findByRole("table", { name: "Rows that will be frozen" });
    type("x".repeat(4001));
    expect(screen.getByText("4,001 of 4,000 characters: 1 too many")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save handover" })).toBeDisabled();
    expect(mocks.create).not.toHaveBeenCalled();
  });

  it("accepts exactly 4000 characters", async () => {
    mocks.create.mockResolvedValue(record());
    mount();
    await screen.findByRole("table", { name: "Rows that will be frozen" });
    type("x".repeat(4000));
    fireEvent.click(screen.getByRole("button", { name: "Save handover" }));
    await waitFor(() => expect(mocks.create).toHaveBeenCalledTimes(1));
  });
});

describe("watching", () => {
  it("posts the note and exactly the checked keys", async () => {
    mocks.create.mockResolvedValue(record());
    mount();
    await screen.findByRole("table", { name: "Rows that will be frozen" });
    fireEvent.click(screen.getByRole("checkbox", { name: "Watch Row 0" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Watch Row 2" }));
    expect(screen.getByText("2 of 20 watched")).toBeInTheDocument();
    type("Watch these two");
    fireEvent.click(screen.getByRole("button", { name: "Save handover" }));
    await waitFor(() => expect(mocks.create).toHaveBeenCalledTimes(1));
    expect(mocks.create).toHaveBeenCalledWith({
      note: "Watch these two",
      watch: [
        { kind: "alert", id: "r0" },
        { kind: "alert", id: "r2" },
      ],
    });
  });

  it("stops at 20: the other boxes are disabled and the limit is said", async () => {
    mocks.preview.mockResolvedValue(body({ items: rows(22), total: 22 }));
    mount();
    await screen.findByRole("table", { name: "Rows that will be frozen" });
    for (let i = 0; i < 20; i += 1) fireEvent.click(screen.getByRole("checkbox", { name: `Watch Row ${i}` }));
    expect(screen.getByText("20 of 20 watched")).toBeInTheDocument();
    expect(screen.getByText(/20 rows are watched, the most a handover holds/)).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Watch Row 20" })).toBeDisabled();
    // A checked box stays enabled so one can be cleared, which frees a place.
    const first = screen.getByRole("checkbox", { name: "Watch Row 0" });
    expect(first).toBeEnabled();
    fireEvent.click(first);
    expect(screen.getByRole("checkbox", { name: "Watch Row 20" })).toBeEnabled();
  });

  it("drops a watched row that left the snapshot when the preview is read again", async () => {
    mocks.preview
      .mockResolvedValueOnce(body({ items: rows(2), total: 2 }))
      .mockRejectedValueOnce(new ApiError(500, "x"))
      .mockResolvedValueOnce(body({ items: rows(1), total: 1 }));
    mount();
    await screen.findByRole("table", { name: "Rows that will be frozen" });
    fireEvent.click(screen.getByRole("checkbox", { name: "Watch Row 1" }));
    expect(screen.getByText("1 of 20 watched")).toBeInTheDocument();
  });
});

describe("saving", () => {
  async function fill() {
    mount();
    await screen.findByRole("table", { name: "Rows that will be frozen" });
    type("Quiet night");
  }

  it("shows the saved record and names the watch keys that were dropped", async () => {
    mocks.create.mockResolvedValue(
      record({ body: body({ watch_dropped: [{ kind: "alert", id: "gone-1" }] }) }),
    );
    await fill();
    fireEvent.click(screen.getByRole("button", { name: "Save handover" }));
    expect(
      await screen.findByText(/1 watched rows were not in the snapshot and were not frozen: alert gone-1/),
    ).toBeInTheDocument();
    expect(screen.getByText("Written by ada")).toBeInTheDocument();
  });

  it("confirms a clean save", async () => {
    mocks.create.mockResolvedValue(record());
    await fill();
    fireEvent.click(screen.getByRole("button", { name: "Save handover" }));
    expect(await screen.findByText("Handover saved. It cannot be edited.")).toBeInTheDocument();
  });

  it("renders a coded backend refusal and keeps the note", async () => {
    mocks.create.mockRejectedValue(
      new ApiError(
        422,
        "handover note is too long (max 4000)",
        "handover note is too long (max 4000)",
        { detail: "handover note is too long (max 4000)", detail_code: "handover.note_too_long", detail_params: { max: 4000 } },
      ),
    );
    await fill();
    fireEvent.click(screen.getByRole("button", { name: "Save handover" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The handover was not saved. handover note is too long (max 4000)");
    expect(note().value).toBe("Quiet night");
  });

  it("says a 403 needs anomaly:triage and that nothing was saved", async () => {
    mocks.create.mockRejectedValue(new ApiError(403, "forbidden"));
    await fill();
    fireEvent.click(screen.getByRole("button", { name: "Save handover" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The handover was not saved. You need the anomaly:triage permission.");
  });

  it("says a network failure kept the note", async () => {
    mocks.create.mockRejectedValue(new TypeError("fetch failed"));
    await fill();
    fireEvent.click(screen.getByRole("button", { name: "Save handover" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Your note is still here");
    expect(screen.getByRole("button", { name: "Save handover" })).toBeEnabled();
  });

  it("does not post twice while a save is in flight", async () => {
    let resolve: (value: unknown) => void = () => {};
    mocks.create.mockReturnValue(new Promise((r) => (resolve = r)));
    await fill();
    const save = screen.getByRole("button", { name: "Save handover" });
    fireEvent.click(save);
    fireEvent.click(screen.getByRole("button", { name: "Saving…" }));
    expect(mocks.create).toHaveBeenCalledTimes(1);
    resolve(record());
    await screen.findByText("Handover saved. It cannot be edited.");
  });
});
