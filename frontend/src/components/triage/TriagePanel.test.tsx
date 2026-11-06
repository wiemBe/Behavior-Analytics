import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TriagePanel, TriageUnavailable, type TriagePanelProps } from "./TriagePanel";
import { ApiError, api, type Triage, type TriageEvent } from "@/lib/api";

function event(over: Partial<TriageEvent> = {}): TriageEvent {
  return {
    id: "e-1",
    kind: "COMMENT",
    actor: "k.osei",
    created_at: "2026-11-05T09:40:12Z",
    from_value: null,
    to_value: null,
    comment: "Checked with the network team.",
    triage_version: 1,
    ...over,
  };
}

function triage(over: Partial<Triage> = {}): Triage {
  return {
    family: "log_source",
    anomaly_id: "a-1",
    status: "NEW",
    owner: null,
    disposition: null,
    disposition_note: null,
    updated_by: null,
    updated_at: null,
    version: 0,
    events: { items: [], total: 0, limit: 50, offset: 0 },
    ...over,
  };
}

function panel(over: Partial<TriagePanelProps> = {}) {
  return render(
    <TriagePanel
      family="log_source"
      anomalyId="a-1"
      initial={triage()}
      access="write"
      me="a.rivera"
      {...over}
    />,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("the state", () => {
  it("says nobody has acted on an untouched anomaly and shows the owner as unassigned", () => {
    panel();
    expect(screen.getByRole("heading", { name: "Triage" })).toBeInTheDocument();
    expect(screen.getByText("New")).toBeInTheDocument();
    expect(screen.getByText("Unassigned")).toBeInTheDocument();
    expect(screen.getByText("Nobody has acted on this anomaly yet.")).toBeInTheDocument();
    expect(screen.getByText(/No activity yet/)).toBeInTheDocument();
  });

  it("keeps the analyst's status apart from the detector's state", () => {
    panel();
    expect(screen.getByText(/separate from the detector's state/)).toBeInTheDocument();
    expect(screen.getByText("Triage status")).toBeInTheDocument();
  });

  it("shows owner, disposition, note and who changed it at what UTC time", () => {
    panel({
      initial: triage({
        status: "CLOSED",
        owner: "k.osei",
        disposition: "KNOWN_CHANGE",
        disposition_note: "Planned change CHG-1042.",
        updated_by: "k.osei",
        updated_at: "2026-11-05T09:41:00Z",
        version: 3,
      }),
    });
    expect(screen.getByText("Closed")).toBeInTheDocument();
    expect(screen.getByText("k.osei", { selector: "dd" })).toBeInTheDocument();
    expect(screen.getByText("Known change")).toBeInTheDocument();
    expect(screen.getByText("Planned change CHG-1042.")).toBeInTheDocument();
    expect(screen.getByText(/Updated by k\.osei,/)).toHaveTextContent("2026-11-05 09:41:00 UTC");
  });

  it("lists the activity newest first with each event in words", () => {
    panel({
      initial: triage({
        status: "IN_PROGRESS",
        owner: "a.rivera",
        version: 1,
        events: {
          items: [
            event({ id: "e-3" }),
            event({ id: "e-2", kind: "OWNER", actor: "a.rivera", from_value: null, to_value: "a.rivera", comment: null }),
            event({ id: "e-1", kind: "STATUS", actor: "a.rivera", from_value: "NEW", to_value: "IN_PROGRESS", comment: null }),
          ],
          total: 3,
          limit: 50,
          offset: 0,
        },
      }),
    });
    const items = within(screen.getByRole("list", { name: "Activity" })).getAllByRole("listitem");
    expect(items).toHaveLength(3);
    expect(items[0]).toHaveTextContent("Checked with the network team.");
    expect(items[1]).toHaveTextContent("Owner set to a.rivera");
    expect(items[2]).toHaveTextContent("Status changed from New to In progress");
  });
});

describe("take it", () => {
  it("sends owner = me, status IN_PROGRESS and the version it read", async () => {
    const patch = vi
      .spyOn(api, "patchTriage")
      .mockResolvedValue(triage({ status: "IN_PROGRESS", owner: "a.rivera", version: 4 }));
    const user = userEvent.setup();
    panel({ initial: triage({ version: 3 }) });
    await user.click(screen.getByRole("button", { name: "Take it" }));
    await waitFor(() =>
      expect(patch).toHaveBeenCalledWith("log_source", "a-1", {
        owner: "a.rivera",
        status: "IN_PROGRESS",
        expected_version: 3,
      }),
    );
    expect(await screen.findByText("In progress")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Saved.");
  });

  it("is off when the caller's name is unknown", () => {
    panel({ me: null });
    expect(screen.getByRole("button", { name: "Take it" })).toBeDisabled();
  });
});

describe("assign", () => {
  it("sends the typed owner, trimmed", async () => {
    const patch = vi.spyOn(api, "patchTriage").mockResolvedValue(triage({ owner: "m.chen", version: 1 }));
    const user = userEvent.setup();
    panel();
    await user.click(screen.getByRole("button", { name: "Assign" }));
    await user.type(screen.getByLabelText(/Owner \(any name or sign-in/), "  m.chen ");
    await user.click(screen.getByRole("button", { name: "Save owner" }));
    await waitFor(() =>
      expect(patch).toHaveBeenCalledWith("log_source", "a-1", { owner: "m.chen", expected_version: 0 }),
    );
  });

  it("sends an explicit null to remove the owner", async () => {
    const patch = vi.spyOn(api, "patchTriage").mockResolvedValue(triage({ version: 2 }));
    const user = userEvent.setup();
    panel({ initial: triage({ status: "IN_PROGRESS", owner: "k.osei", version: 1 }) });
    await user.click(screen.getByRole("button", { name: "Assign" }));
    await user.clear(screen.getByLabelText(/Owner \(any name or sign-in/));
    await user.click(screen.getByRole("button", { name: "Save owner" }));
    await waitFor(() =>
      expect(patch).toHaveBeenCalledWith("log_source", "a-1", { owner: null, expected_version: 1 }),
    );
  });
});

describe("close with a disposition", () => {
  async function openClose() {
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Close with a disposition" }));
    return user;
  }

  it("is blocked client-side without a disposition and sends nothing", async () => {
    const patch = vi.spyOn(api, "patchTriage");
    panel();
    const user = await openClose();
    await user.click(screen.getByRole("button", { name: "Close triage" }));
    expect(patch).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("Choose a disposition before closing.");
    expect(screen.getByLabelText("Disposition (required)")).toHaveAttribute("aria-invalid", "true");
  });

  it("offers the five dispositions", async () => {
    panel();
    await openClose();
    const options = within(screen.getByLabelText("Disposition (required)")).getAllByRole("option");
    expect(options.map((o) => o.textContent)).toEqual([
      "Choose a disposition",
      "True positive",
      "Benign true positive",
      "Noise",
      "Collection problem",
      "Known change",
    ]);
  });

  it("sends CLOSED, the disposition and the note", async () => {
    const patch = vi.spyOn(api, "patchTriage").mockResolvedValue(
      triage({ status: "CLOSED", disposition: "NOISE", disposition_note: "QA load test", version: 2 }),
    );
    panel({ initial: triage({ status: "IN_PROGRESS", owner: "a.rivera", version: 1 }) });
    const user = await openClose();
    await user.selectOptions(screen.getByLabelText("Disposition (required)"), "NOISE");
    await user.type(screen.getByLabelText(/Note \(optional/), "  QA load test ");
    await user.click(screen.getByRole("button", { name: "Close triage" }));
    await waitFor(() =>
      expect(patch).toHaveBeenCalledWith("log_source", "a-1", {
        status: "CLOSED",
        disposition: "NOISE",
        disposition_note: "QA load test",
        expected_version: 1,
      }),
    );
    expect(await screen.findByText("Closed")).toBeInTheDocument();
  });

  it("leaves the note out when it is empty", async () => {
    const patch = vi.spyOn(api, "patchTriage").mockResolvedValue(triage({ status: "CLOSED", version: 1 }));
    panel();
    const user = await openClose();
    await user.selectOptions(screen.getByLabelText("Disposition (required)"), "TRUE_POSITIVE");
    await user.click(screen.getByRole("button", { name: "Close triage" }));
    await waitFor(() =>
      expect(patch).toHaveBeenCalledWith("log_source", "a-1", {
        status: "CLOSED",
        disposition: "TRUE_POSITIVE",
        expected_version: 0,
      }),
    );
  });

  it("bounds the note and shows its length", async () => {
    panel();
    const user = await openClose();
    const note = screen.getByLabelText(/Note \(optional, up to 1000 characters\)/);
    expect(note).toHaveAttribute("maxlength", "1000");
    await user.type(note, "abc");
    expect(screen.getByText("3 / 1000")).toBeInTheDocument();
  });

  it("shows the server's 422 in the dialog", async () => {
    vi.spyOn(api, "patchTriage").mockRejectedValue(
      new ApiError(422, "closing an anomaly requires a disposition"),
    );
    panel();
    const user = await openClose();
    await user.selectOptions(screen.getByLabelText("Disposition (required)"), "NOISE");
    await user.click(screen.getByRole("button", { name: "Close triage" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/requires a disposition/);
    // The dialog stays open so the analyst can correct it.
    expect(screen.getByRole("button", { name: "Close triage" })).toBeInTheDocument();
  });

  it("closes the dialog on Escape and gives focus back to its button", async () => {
    panel();
    const user = await openClose();
    await user.keyboard("{Escape}");
    expect(screen.queryByLabelText("Disposition (required)")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Close with a disposition" })).toHaveFocus();
  });
});

describe("reopen", () => {
  it("shows only Reopen on a closed item and clears the disposition", async () => {
    const patch = vi.spyOn(api, "patchTriage").mockResolvedValue(
      triage({ status: "IN_PROGRESS", owner: "k.osei", version: 4 }),
    );
    const user = userEvent.setup();
    panel({
      initial: triage({ status: "CLOSED", owner: "k.osei", disposition: "NOISE", version: 3 }),
    });
    expect(screen.queryByRole("button", { name: "Take it" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Close with a disposition" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Reopen" }));
    await waitFor(() =>
      expect(patch).toHaveBeenCalledWith("log_source", "a-1", {
        status: "IN_PROGRESS",
        disposition: null,
        disposition_note: null,
        expected_version: 3,
      }),
    );
  });
});

describe("a 409 conflict", () => {
  const theirs = triage({
    status: "IN_PROGRESS",
    owner: "k.osei",
    updated_by: "k.osei",
    updated_at: "2026-11-05T19:04:00Z",
    version: 2,
  });

  it("says who changed it, switches the controls off and does not overwrite", async () => {
    const patch = vi.spyOn(api, "patchTriage").mockRejectedValue(new ApiError(409, "stale"));
    const read = vi.spyOn(api, "triage").mockResolvedValue(theirs);
    const user = userEvent.setup();
    panel({ initial: triage({ version: 1 }) });
    await user.click(screen.getByRole("button", { name: "Take it" }));

    const box = await screen.findByRole("alert");
    expect(box).toHaveTextContent("Someone else changed this triage.");
    expect(box).toHaveTextContent("k.osei last changed it, 2026-11-05 19:04:00 UTC");
    expect(box).toHaveTextContent("It is now In progress, owned by k.osei. Your change was not saved.");
    expect(patch).toHaveBeenCalledTimes(1);
    expect(read).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Take it" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Assign" })).toBeDisabled();
    // Still showing what the analyst loaded until they ask for the other version.
    expect(screen.getByText("New")).toBeInTheDocument();
  });

  it("applies their version only when asked, then sends the new version", async () => {
    const patch = vi
      .spyOn(api, "patchTriage")
      .mockRejectedValueOnce(new ApiError(409, "stale"))
      .mockResolvedValueOnce(triage({ status: "CLOSED", version: 3 }));
    vi.spyOn(api, "triage").mockResolvedValue(theirs);
    const user = userEvent.setup();
    panel({ initial: triage({ version: 1 }) });
    await user.click(screen.getByRole("button", { name: "Take it" }));
    await user.click(await screen.findByRole("button", { name: "Load their version" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getAllByText("In progress").length).toBeGreaterThan(0);
    await user.click(screen.getByRole("button", { name: "Assign" }));
    await user.type(screen.getByLabelText(/Owner \(any name or sign-in/), "x");
    await user.click(screen.getByRole("button", { name: "Save owner" }));
    await waitFor(() => expect(patch).toHaveBeenCalledTimes(2));
    expect(patch.mock.calls[1][2]).toMatchObject({ expected_version: 2 });
  });

  it("says so when their version cannot be read", async () => {
    vi.spyOn(api, "patchTriage").mockRejectedValue(new ApiError(409, "stale"));
    vi.spyOn(api, "triage").mockRejectedValue(new ApiError(500, "boom"));
    const user = userEvent.setup();
    panel();
    await user.click(screen.getByRole("button", { name: "Take it" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /their version could not be read\. Your change was not saved\. Reload the page/,
    );
    expect(screen.queryByRole("button", { name: "Load their version" })).not.toBeInTheDocument();
  });
});

describe("without anomaly:triage", () => {
  it("is read-only and names the permission", () => {
    panel({
      access: "read",
      initial: triage({ status: "IN_PROGRESS", owner: "k.osei", version: 1 }),
    });
    expect(screen.getByText(/Changing it needs the anomaly:triage permission/)).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/Add a comment/)).not.toBeInTheDocument();
    expect(screen.getByText("k.osei", { selector: "dd" })).toBeInTheDocument();
  });

  it("switches changes off, and says why, when the grants could not be read", () => {
    panel({ access: "unknown" });
    expect(screen.getByText(/permissions could not be checked/)).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});

describe("comments", () => {
  it("rejects an empty comment without a request", async () => {
    const post = vi.spyOn(api, "commentTriage");
    const user = userEvent.setup();
    panel();
    await user.click(screen.getByRole("button", { name: "Add comment" }));
    expect(post).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("Write a comment first.");
  });

  it("posts the trimmed text and puts it first", async () => {
    const post = vi
      .spyOn(api, "commentTriage")
      .mockResolvedValue(event({ id: "new", comment: "Patched at 09:40." }));
    const user = userEvent.setup();
    panel({
      initial: triage({
        version: 1,
        events: { items: [event({ id: "old", comment: "Older." })], total: 1, limit: 50, offset: 0 },
      }),
    });
    const box = screen.getByLabelText("Add a comment (up to 2000 characters)");
    expect(box).toHaveAttribute("maxlength", "2000");
    await user.type(box, "  Patched at 09:40. ");
    await user.click(screen.getByRole("button", { name: "Add comment" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith("log_source", "a-1", "Patched at 09:40."));
    const items = within(screen.getByRole("list", { name: "Activity" })).getAllByRole("listitem");
    expect(items[0]).toHaveTextContent("Patched at 09:40.");
    expect(items).toHaveLength(2);
    expect(box).toHaveValue("");
  });

  it("shows the server's refusal and keeps the text", async () => {
    vi.spyOn(api, "commentTriage").mockRejectedValue(new ApiError(403, "no"));
    const user = userEvent.setup();
    panel();
    const box = screen.getByLabelText(/Add a comment/);
    await user.type(box, "keep me");
    await user.click(screen.getByRole("button", { name: "Add comment" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/permission/i);
    expect(box).toHaveValue("keep me");
  });
});

describe("paging the activity", () => {
  const many = (offset: number) =>
    triage({
      version: 1,
      events: {
        items: [event({ id: `e-${offset}`, comment: `page at ${offset}` })],
        total: 120,
        limit: 50,
        offset,
      },
    });

  it("reads the next page and keeps the fields and the version", async () => {
    const read = vi.spyOn(api, "triage").mockResolvedValue(many(50));
    const user = userEvent.setup();
    panel({ initial: many(0) });
    expect(screen.getByText("1–1 of 120")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Older" }));
    await waitFor(() => expect(read).toHaveBeenCalledWith("log_source", "a-1", { limit: 50, offset: 50 }));
    expect(await screen.findByText("page at 50")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Newer" })).toBeEnabled();
  });

  it("says the page could not be loaded", async () => {
    vi.spyOn(api, "triage").mockRejectedValue(new ApiError(500, "boom"));
    const user = userEvent.setup();
    panel({ initial: many(0) });
    await user.click(screen.getByRole("button", { name: "Older" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The activity could not be loaded. Try again.");
  });

  it("shows no pager for a short history", () => {
    panel();
    expect(screen.queryByRole("button", { name: "Older" })).not.toBeInTheDocument();
  });
});

describe("the alert beside the anomaly", () => {
  it("links the alert that names this anomaly", () => {
    panel({ alert: { id: "al-1", title: "Volume spike on Core", severity: "HIGH", status: "OPEN" } });
    expect(screen.getByRole("link", { name: "Volume spike on Core" })).toHaveAttribute("href", "/alerts/al-1");
  });
  it("says none references it when the lookup was complete", () => {
    panel({ alert: null });
    expect(screen.getByText("No alert references this anomaly.")).toBeInTheDocument();
  });
  it("says nothing about alerts when the lookup was not complete", () => {
    panel({ alert: undefined });
    expect(screen.queryByText(/alert/i, { selector: ".triage-alert" })).not.toBeInTheDocument();
  });
});

describe("states", () => {
  it("shows an error, not a guessed New, when the read failed, and retries", async () => {
    const read = vi.spyOn(api, "triage").mockResolvedValue(triage({ status: "IN_PROGRESS", version: 1 }));
    const user = userEvent.setup();
    panel({ initial: null });
    expect(screen.getByRole("status")).toHaveTextContent(/triage of this anomaly could not be loaded/);
    expect(screen.queryByText("New")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(read).toHaveBeenCalled());
    expect(await screen.findByText("In progress")).toBeInTheDocument();
  });

  it("says triage is not available for a kind without it", () => {
    render(<TriageUnavailable />);
    expect(screen.getByText(/Triage is not available for this kind of anomaly/)).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
