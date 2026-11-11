import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { StaticSession } from "@/components/auth/session";
import { session } from "@/components/alerts/suppressionFixtures";
import { ApiError } from "@/lib/api";

import { ACTED, WRITTEN, body, item, openSource, record, SOURCES_OK } from "./fixtures";
import { HandoverView } from "./HandoverView";
import { WriteLink } from "./WriteLink";

const mocks = vi.hoisted(() => ({ latest: vi.fn(), one: vi.fn(), list: vi.fn() }));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, handoverApi: { ...actual.handoverApi, latest: mocks.latest, one: mocks.one, list: mocks.list } };
});

const READER = session(["read:alerts"]);
const WRITER = session(["read:alerts", "anomaly:triage"]);

function mount(id: string | null = null, who = READER) {
  return render(
    <StaticSession value={who}>
      <HandoverView id={id} />
    </StaticSession>,
  );
}

const summary = (id: string, author: string) => ({
  id,
  author,
  written_at: "2026-11-10T22:00:00Z",
  window_started_at: "2026-11-10T14:00:00Z",
  previous_id: null,
  note_length: 12,
  note_excerpt: "Earlier note",
});

beforeEach(() => {
  mocks.latest.mockReset();
  mocks.one.mockReset();
  mocks.list.mockReset();
  mocks.list.mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0 });
});

describe("loading", () => {
  it("shows a loading line and no figure, table or note", () => {
    mocks.latest.mockReturnValue(new Promise(() => {}));
    mount();
    expect(screen.getByRole("status")).toHaveTextContent("Loading the handover…");
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryByText(/\d/)).toBeNull();
  });
});

describe("a full record", () => {
  beforeEach(() => {
    mocks.latest.mockResolvedValue({ handover: record() });
  });

  it("leads with author, the UTC time and the note", async () => {
    mount();
    expect(await screen.findByText("Written by ada")).toBeInTheDocument();
    const time = document.querySelector("time[datetime='" + WRITTEN + "']");
    expect(time?.textContent).toMatch(/UTC$/);
    expect(screen.getByText(/the gateway volume followed the release/)).toBeInTheDocument();
  });

  it("puts the watched row under Watch only, and the others under Open when this was written", async () => {
    mount();
    const watch = await screen.findByRole("table", { name: "Watch" });
    expect(within(watch).getByRole("link", { name: "Watched gateway drop" })).toHaveAttribute("href", "/alerts/al-1");
    const open = screen.getByRole("table", { name: "Open items frozen with this handover" });
    expect(within(open).queryByText("Watched gateway drop")).toBeNull();
    expect(within(open).getByRole("link", { name: "Offense 4412" })).toHaveAttribute("href", "/offenses/1");
  });

  it("words the waiting age as of the time it was written", async () => {
    mocks.latest.mockResolvedValue({ handover: record({ body: body({ items: [item({ age_seconds: 25200 })] }) }) });
    mount();
    expect(await screen.findByText("7 h when written")).toBeInTheDocument();
    expect(screen.queryByText(/so far/)).toBeNull();
  });

  it("shows the qualifiers Unassigned, Over SLA, New and Updated, and none for an unknown SLA", async () => {
    mount();
    const watch = await screen.findByRole("table", { name: "Watch" });
    expect(within(watch).getByText("Over SLA")).toBeInTheDocument();
    expect(within(watch).getByText("New")).toBeInTheDocument();
    const open = screen.getByRole("table", { name: "Open items frozen with this handover" });
    const rows = within(open).getAllByRole("row").slice(1);
    expect(within(rows[0]).getByText("Unassigned")).toBeInTheDocument();
    expect(within(rows[0]).getByText("Over SLA")).toBeInTheDocument();
    expect(within(rows[1]).getByText("Updated")).toBeInTheDocument();
    expect(within(rows[1]).queryByText("Over SLA")).toBeNull();
  });

  it("lists what the author acted on, each linking to its row, with no clean-source line", async () => {
    mount();
    const acted = await screen.findByRole("table", { name: "Actions taken in this window" });
    expect(within(acted).getByRole("link", { name: "Status" })).toHaveAttribute("href", "/anomalies/an-9");
    expect(within(acted).getByText("In progress → Closed")).toBeInTheDocument();
    expect(within(acted).getByRole("link", { name: "Alert resolved" })).toHaveAttribute("href", "/alerts/al-3");
    expect(screen.queryByText("Sources not read cleanly")).toBeNull();
    expect(screen.getByRole("heading", { name: /Acted on by ada in this window/ })).toBeInTheDocument();
  });

  it("names a watched key that was not frozen", async () => {
    mocks.latest.mockResolvedValue({
      handover: record({ body: body({ watch_dropped: [{ kind: "alert", id: "missing-9" }] }) }),
    });
    mount();
    expect(await screen.findByText(/Not frozen, because they were not in the snapshot: alert missing-9/)).toBeInTheDocument();
  });
});

describe("sources that were not read cleanly", () => {
  it("names a FORBIDDEN offense source with its permission and draws no count for it", async () => {
    const sources = SOURCES_OK.map((s) =>
      s.kind === "offense" ? openSource("offense", { state: "FORBIDDEN", count: null, permission: "read:offenses" }) : s,
    );
    mocks.latest.mockResolvedValue({ handover: record({ body: body({ sources, items: [], total: 0 }) }) });
    mount();
    const section = await screen.findByRole("region", { name: "Sources not read cleanly" });
    expect(
      within(section).getByText(
        "Offenses: not read. The author's account lacks read:offenses, so its rows are not in this record.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/so far|for this preview/)).toBeNull();
    // The empty list is not read as "nothing was waiting".
    expect(screen.getByText(/No open rows were frozen\. Check the source lines below/)).toBeInTheDocument();
    expect(screen.queryByText("No open rows were waiting when this was written.")).toBeNull();
    expect(screen.queryByText(/^0 open$/)).toBeNull();
  });

  it("says a FAILED acted-on source is missing, not an empty list", async () => {
    mocks.latest.mockResolvedValue({
      handover: record({
        body: body({
          acted_on: [],
          acted_on_sources: [
            { kind: "triage", state: "FAILED", count: null, permission: null, reason_code: "LOAD_FAILED" },
            { kind: "alert", state: "OK", count: 0, permission: null, reason_code: null },
          ],
        }),
      }),
    });
    mount();
    expect(await screen.findByText("The actions of this window could not be listed. See the source lines below.")).toBeInTheDocument();
    expect(screen.queryByText(/No recorded action by ada/)).toBeNull();
    const section = screen.getByRole("region", { name: "Sources not read cleanly" });
    expect(within(section).getByText(/Triage actions: not read\. It failed to load/)).toBeInTheDocument();
    expect(within(section).getByText("Reason: LOAD_FAILED.", { exact: false })).toBeInTheDocument();
  });

  it("says an OK window with no action recorded none", async () => {
    mocks.latest.mockResolvedValue({ handover: record({ body: body({ acted_on: [] }) }) });
    mount();
    expect(await screen.findByText("No recorded action by ada in this window.")).toBeInTheDocument();
  });
});

describe("a cut-off list", () => {
  const many = Array.from({ length: 50 }, (_, i) => item({ id: `a${i}`, title: `Row ${i}`, link: `/alerts/a${i}` }));

  it("says 50 of N with a link to the queue when the total is exact", async () => {
    mocks.latest.mockResolvedValue({ handover: record({ body: body({ items: many, total: 120, truncated: true }) }) });
    mount();
    expect(await screen.findByText("50 of 120")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "See all in the queue" })).toHaveAttribute("href", "/queue");
  });

  it("says 50 of at least N when the total is a lower bound", async () => {
    mocks.latest.mockResolvedValue({
      handover: record({ body: body({ items: many, total: 120, truncated: true, total_exact: false }) }),
    });
    mount();
    expect(await screen.findByText("50 of at least 120")).toBeInTheDocument();
  });

  it("offers no queue link and no cut-off when nothing was cut", async () => {
    mocks.latest.mockResolvedValue({ handover: record() });
    mount();
    await screen.findByText("Written by ada");
    expect(screen.queryByRole("link", { name: "See all in the queue" })).toBeNull();
    expect(screen.getByText("3 open")).toBeInTheDocument();
  });
});

describe("empty", () => {
  beforeEach(() => mocks.latest.mockResolvedValue({ handover: null }));

  it("says there is no handover and links to the queue; Write is not offered without the permission", async () => {
    mount();
    expect(await screen.findByText("No handover yet. The queue lists what is waiting.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open the queue" })).toHaveAttribute("href", "/queue");
    expect(screen.queryByRole("link", { name: "Write the first handover" })).toBeNull();
  });

  it("offers Write with anomaly:triage", async () => {
    mount(null, WRITER);
    expect(await screen.findByRole("link", { name: "Write the first handover" })).toHaveAttribute(
      "href",
      "/handover?compose=1",
    );
  });
});

describe("error", () => {
  it("says what failed, offers Retry and recovers", async () => {
    mocks.latest.mockRejectedValueOnce(new ApiError(500, "boom")).mockResolvedValueOnce({ handover: record() });
    mount();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The handover could not be loaded.");
    expect(alert).not.toHaveTextContent(/something went wrong|boom/i);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Written by ada")).toBeInTheDocument();
  });

  it("names a missing record for a 404 and offers the way back", async () => {
    mocks.one.mockRejectedValue(new ApiError(404, "handover was not found"));
    mount("gone");
    expect(await screen.findByText(/This handover does not exist/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to the latest handover" })).toHaveAttribute("href", "/handover");
  });

  it("says the account may not read when the backend answers 403", async () => {
    mocks.latest.mockRejectedValue(new ApiError(403, "no"));
    mount();
    expect(await screen.findByText(/may not read the handover log/)).toBeInTheDocument();
  });
});

describe("a chosen record and the earlier list", () => {
  it("reads ?id= through the detail route and links back to the latest", async () => {
    mocks.one.mockResolvedValue(record({ id: "old-1", author: "sam" }));
    mount("old-1");
    expect(await screen.findByText("Written by sam")).toBeInTheDocument();
    expect(mocks.one).toHaveBeenCalledWith("old-1");
    expect(mocks.latest).not.toHaveBeenCalled();
    expect(screen.getAllByRole("link", { name: "Back to the latest handover" })[0]).toHaveAttribute("href", "/handover");
  });

  it("lists earlier handovers without the one on screen, each linking to its id", async () => {
    mocks.latest.mockResolvedValue({ handover: record({ id: "now-1" }) });
    mocks.list.mockResolvedValue({
      items: [summary("now-1", "ada"), summary("old-1", "sam")],
      total: 2,
      limit: 20,
      offset: 0,
    });
    mount();
    const table = await screen.findByRole("table", { name: "Earlier handovers" });
    const links = within(table).getAllByRole("link");
    expect(links.map((l) => l.getAttribute("href"))).toEqual(["/handover?id=old-1"]);
    expect(within(table).getByText("sam")).toBeInTheDocument();
  });

  it("pages with Show earlier", async () => {
    mocks.latest.mockResolvedValue({ handover: record({ id: "now-1" }) });
    mocks.list
      .mockResolvedValueOnce({ items: [summary("now-1", "ada"), summary("o1", "sam")], total: 3, limit: 20, offset: 0 })
      .mockResolvedValueOnce({ items: [summary("o2", "lee")], total: 3, limit: 20, offset: 2 });
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Show earlier" }));
    await waitFor(() => expect(screen.getByText("lee")).toBeInTheDocument());
    expect(mocks.list).toHaveBeenLastCalledWith(20, 2);
    expect(screen.queryByRole("button", { name: "Show earlier" })).toBeNull();
  });

  it("says the list failed without hiding the record", async () => {
    mocks.latest.mockResolvedValue({ handover: record() });
    mocks.list.mockRejectedValue(new ApiError(500, "x"));
    mount();
    expect(await screen.findByText("The earlier handovers could not be loaded.")).toBeInTheDocument();
    expect(screen.getByText("Written by ada")).toBeInTheDocument();
  });
});

describe("the Write action", () => {
  it("is absent without anomaly:triage and present with it", () => {
    const { unmount } = render(
      <StaticSession value={READER}>
        <WriteLink />
      </StaticSession>,
    );
    expect(screen.queryByRole("link", { name: "Write handover" })).toBeNull();
    unmount();
    render(
      <StaticSession value={WRITER}>
        <WriteLink />
      </StaticSession>,
    );
    expect(screen.getByRole("link", { name: "Write handover" })).toHaveAttribute("href", "/handover?compose=1");
  });

  it("is absent while the permissions are still being read", () => {
    render(
      <StaticSession value={session(["anomaly:triage"], "loading")}>
        <WriteLink />
      </StaticSession>,
    );
    expect(screen.queryByRole("link", { name: "Write handover" })).toBeNull();
  });
});

void ACTED;
