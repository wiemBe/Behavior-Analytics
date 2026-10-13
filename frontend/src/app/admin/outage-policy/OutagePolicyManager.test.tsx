import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { OutagePolicyManager } from "./OutagePolicyManager";
import { ApiError, fleetApi, outagePolicyApi } from "@/lib/api";
import { SUMMARY, row, sourceRow, fleetSource, FW_02 } from "./fixtures";

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(outagePolicyApi, "summary").mockResolvedValue(SUMMARY);
});

function mount(rows = [row(), sourceRow()]) {
  vi.spyOn(outagePolicyApi, "list").mockResolvedValue(rows);
  return render(
    <OutagePolicyManager initialRows={rows} initialSummary={SUMMARY} summaryNote={null} />,
  );
}

const grace = () => screen.getByLabelText("Grace, in buckets");

describe("impact before saving", () => {
  it("states the effect of a class switch in numbers, with the kept overrides", async () => {
    const user = userEvent.setup();
    mount([row({ mode: "ALERT" }), sourceRow({ mode: "ALERT" })]);
    await user.click(screen.getByRole("button", { name: "Edit Firewall" }));
    await user.click(screen.getByRole("radio", { name: "Record only" }));
    const impact = screen.getByRole("group", { name: "Before you save" });
    expect(impact).toHaveTextContent(
      "12 monitored sources in Firewall will no longer raise outage alerts.",
    );
    expect(impact).toHaveTextContent("1 source keeps alerting through its own override.");
  });

  it("states the opposite when switching back to ALERT", async () => {
    const user = userEvent.setup();
    mount();
    await user.click(screen.getByRole("button", { name: "Edit Firewall" }));
    await user.click(screen.getByRole("radio", { name: "Alert" }));
    expect(screen.getByRole("group", { name: "Before you save" })).toHaveTextContent(
      "12 monitored sources in Firewall will raise outage alerts again.",
    );
  });

  it("counts a class with no row by its monitored sources", async () => {
    const user = userEvent.setup();
    mount();
    await user.click(screen.getByRole("button", { name: "Set policy for Linux" }));
    await user.click(screen.getByRole("radio", { name: "Record only" }));
    expect(screen.getByRole("group", { name: "Before you save" })).toHaveTextContent(
      "8 monitored sources in Linux will no longer raise outage alerts.",
    );
  });

  it("will not save what is already in effect", async () => {
    const user = userEvent.setup();
    mount();
    await user.click(screen.getByRole("button", { name: "Set policy for Linux" }));
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    await user.click(screen.getByRole("radio", { name: "Record only" }));
    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
  });

  it("states what removing a class row does", async () => {
    const user = userEvent.setup();
    mount();
    await user.click(screen.getByRole("button", { name: "Use the default for Firewall" }));
    expect(screen.getByRole("group", { name: "Before you use the default" })).toHaveTextContent(
      "12 monitored sources in Firewall will raise outage alerts again.",
    );
  });
});

describe("saving a class", () => {
  it("sends the mode and the grace, then reloads and says what was saved", async () => {
    const user = userEvent.setup();
    mount();
    const put = vi.spyOn(outagePolicyApi, "putClass").mockResolvedValue(row());
    await user.click(screen.getByRole("button", { name: "Set policy for Linux" }));
    await user.click(screen.getByRole("radio", { name: "Record only" }));
    await user.type(grace(), "12");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(put).toHaveBeenCalledWith("LINUX", { mode: "RECORD_ONLY", silence_grace_buckets: 12 });
    expect(await screen.findByText(/Saved\. Linux is now Record only, grace 12 buckets · 60 min/)).toBeInTheDocument();
    expect(outagePolicyApi.list).toHaveBeenCalled();
  });

  it("sends a blank grace as null, meaning the default", async () => {
    const user = userEvent.setup();
    mount();
    const put = vi.spyOn(outagePolicyApi, "putClass").mockResolvedValue(row());
    await user.click(screen.getByRole("button", { name: "Edit Firewall" }));
    await user.type(grace(), "6");
    await user.clear(grace());
    await user.click(screen.getByRole("radio", { name: "Alert" }));
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(put).toHaveBeenCalledWith("FIREWALL", { mode: "ALERT", silence_grace_buckets: null });
  });

  it("cannot be submitted twice while the save is in flight", async () => {
    const user = userEvent.setup();
    mount();
    let release: (r: ReturnType<typeof row>) => void = () => {};
    const put = vi.spyOn(outagePolicyApi, "putClass").mockReturnValue(
      new Promise((resolve) => {
        release = resolve;
      }),
    );
    await user.click(screen.getByRole("button", { name: "Set policy for Linux" }));
    await user.click(screen.getByRole("radio", { name: "Record only" }));
    await user.click(screen.getByRole("button", { name: "Save" }));
    const saving = screen.getByRole("button", { name: "Saving…" });
    expect(saving).toBeDisabled();
    await user.click(saving);
    // A second submit that bypasses the disabled button (Enter in the field).
    fireEvent.submit(screen.getByRole("form", { name: "Set policy for Linux" }));
    expect(put).toHaveBeenCalledTimes(1);
    release(row());
    await screen.findByText(/Saved\./);
  });

  it("keeps the operator's input and says why when the save fails", async () => {
    const user = userEvent.setup();
    mount();
    vi.spyOn(outagePolicyApi, "putClass").mockRejectedValue(new ApiError(500, "boom"));
    await user.click(screen.getByRole("button", { name: "Set policy for Linux" }));
    await user.click(screen.getByRole("radio", { name: "Record only" }));
    await user.type(grace(), "12");
    await user.click(screen.getByRole("button", { name: "Save" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/Not saved\./);
    expect(alert).toHaveTextContent(/try again/i);
    expect(grace()).toHaveValue("12");
    expect(screen.getByRole("radio", { name: "Record only" })).toBeChecked();
    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
  });

  it("names the permission when the save is refused", async () => {
    const user = userEvent.setup();
    mount();
    vi.spyOn(outagePolicyApi, "putClass").mockRejectedValue(new ApiError(403, "no"));
    await user.click(screen.getByRole("button", { name: "Set policy for Linux" }));
    await user.click(screen.getByRole("radio", { name: "Record only" }));
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("admin:outage");
  });

  it("shows a 422 at the grace field and keeps the input", async () => {
    const user = userEvent.setup();
    mount();
    vi.spyOn(outagePolicyApi, "putClass").mockRejectedValue(
      new ApiError(422, "Input should be less than or equal to 48"),
    );
    await user.click(screen.getByRole("button", { name: "Set policy for Linux" }));
    await user.type(grace(), "99");
    await user.click(screen.getByRole("button", { name: "Save" }));
    const err = await screen.findByText("Input should be less than or equal to 48");
    expect(err).toHaveAttribute("role", "alert");
    expect(grace()).toHaveAttribute("aria-invalid", "true");
    expect(grace().getAttribute("aria-describedby")).toContain(err.id);
    expect(grace()).toHaveValue("99");
  });

  it("rejects a non-integer grace before sending anything", async () => {
    const user = userEvent.setup();
    mount();
    const put = vi.spyOn(outagePolicyApi, "putClass");
    await user.click(screen.getByRole("button", { name: "Set policy for Linux" }));
    await user.click(screen.getByRole("radio", { name: "Record only" }));
    await user.type(grace(), "2.5");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(screen.getByText(/whole number/)).toBeInTheDocument();
    expect(put).not.toHaveBeenCalled();
  });
});

describe("removing", () => {
  it("removes a class row, reloads, and says what happened", async () => {
    const user = userEvent.setup();
    mount();
    const del = vi.spyOn(outagePolicyApi, "deleteClass").mockResolvedValue(undefined);
    await user.click(screen.getByRole("button", { name: "Use the default for Firewall" }));
    await user.click(screen.getByRole("button", { name: "Use default" }));
    expect(del).toHaveBeenCalledWith("FIREWALL", null);
    expect(await screen.findByText(/Firewall uses the default again/)).toBeInTheDocument();
  });

  it("a 404 means someone else removed it: says so and reloads", async () => {
    const user = userEvent.setup();
    mount();
    vi.spyOn(outagePolicyApi, "deleteSource").mockRejectedValue(new ApiError(404, "gone"));
    await user.click(screen.getByRole("button", { name: "Remove the override of PaSeries @ pa-fw-02" }));
    await user.click(screen.getByRole("button", { name: "Remove override" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/already removed by someone else/);
    expect(outagePolicyApi.list).toHaveBeenCalled();
    // The editor is closed: the stale confirmation is gone.
    expect(screen.queryByRole("button", { name: "Remove override" })).toBeNull();
  });

  it("removing an instance row passes the instance", async () => {
    const user = userEvent.setup();
    mount([row({ scope_value: "LINUX", instance_id: "7c1e0000-0000-4000-8000-0000000000b2" })]);
    const del = vi.spyOn(outagePolicyApi, "deleteClass").mockResolvedValue(undefined);
    await user.click(screen.getByRole("button", { name: /^Use the default for Linux/ }));
    await user.click(screen.getByRole("button", { name: "Use default" }));
    expect(del).toHaveBeenCalledWith("LINUX", "7c1e0000-0000-4000-8000-0000000000b2");
  });
});

describe("source override picker", () => {
  const INVENTORY = [
    fleetSource(),
    fleetSource({ id: "s2", name: "CiscoFirepowerThreatDefense @ ftd-01", type_name: "Cisco Firepower Threat Defense", source_class: "IPS" }),
    fleetSource({ id: "s3", name: "LinuxServer @ lnx-app-01", type_name: "Linux OS", source_class: "LINUX" }),
  ];
  const page = (items = INVENTORY, total = items.length) => ({ items, total, limit: 25, offset: 0 });

  it("searches on the server with the typed text, once the typing pauses", async () => {
    const user = userEvent.setup();
    mount();
    const list = vi
      .spyOn(fleetApi, "sources")
      .mockImplementation(async (p) =>
        page(INVENTORY.filter((s) => !p?.q || s.name.toLowerCase().includes(p.q.toLowerCase()))),
      );
    await user.click(screen.getByRole("button", { name: "Add override" }));
    expect(await screen.findAllByRole("button", { name: /^Override / })).toHaveLength(2);
    expect(list).toHaveBeenLastCalledWith({ q: undefined, limit: 25 });
    await user.type(screen.getByLabelText("Search by name or QRadar type"), "ftd");
    await waitFor(() => expect(list).toHaveBeenLastCalledWith({ q: "ftd", limit: 25 }));
    // One request for the three keystrokes, not one each.
    expect(list.mock.calls.filter(([p]) => p?.q !== undefined)).toHaveLength(1);
    expect(await screen.findByRole("button", { name: "Override CiscoFirepowerThreatDefense @ ftd-01" })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /^Override / })).toHaveLength(1);
    // The class and the QRadar type are columns of the table.
    const r = screen.getByRole("row", { name: /ftd-01/ });
    expect(within(r).getByText("IPS")).toBeInTheDocument();
    expect(within(r).getByText("Cisco Firepower Threat Defense")).toBeInTheDocument();
  });

  it("says nothing matched", async () => {
    const user = userEvent.setup();
    mount();
    vi.spyOn(fleetApi, "sources").mockImplementation(async (p) => page(p?.q ? [] : INVENTORY));
    await user.click(screen.getByRole("button", { name: "Add override" }));
    await screen.findAllByRole("row");
    await user.type(screen.getByLabelText("Search by name or QRadar type"), "zzz");
    expect(await screen.findByText(/name or type containing “zzz”/)).toBeInTheDocument();
  });

  it("puts the action in the last column of every row", async () => {
    const user = userEvent.setup();
    mount();
    vi.spyOn(fleetApi, "sources").mockResolvedValue(page());
    await user.click(screen.getByRole("button", { name: "Add override" }));
    await screen.findAllByRole("row");
    const region = screen.getByRole("region", { name: "Choose a log source" });
    for (const r of within(region).getAllByRole("row").slice(1)) {
      const cells = within(r).getAllByRole("cell");
      expect(cells[cells.length - 1]).toHaveClass("cell-end");
    }
    expect(within(region).getByRole("columnheader", { name: "Override" })).toHaveClass("cell-end");
  });

  it("offers Has override, which opens that override's editor, instead of Override", async () => {
    const user = userEvent.setup();
    mount();
    vi.spyOn(fleetApi, "sources").mockResolvedValue(page());
    await user.click(screen.getByRole("button", { name: "Add override" }));
    await screen.findAllByRole("row");
    expect(screen.queryByRole("button", { name: "Override PaSeries @ pa-fw-02" })).toBeNull();
    await user.click(screen.getByRole("link", { name: "Has override: edit PaSeries @ pa-fw-02" }));
    expect(screen.queryByRole("region", { name: "Choose a log source" })).toBeNull();
    expect(screen.getByRole("form", { name: "Edit the override of PaSeries @ pa-fw-02" })).toBeInTheDocument();
  });

  it("says how many matched when the page is shorter", async () => {
    const user = userEvent.setup();
    mount();
    vi.spyOn(fleetApi, "sources").mockResolvedValue(page(INVENTORY, 2500));
    await user.click(screen.getByRole("button", { name: "Add override" }));
    expect(await screen.findByText(/2500 log sources match; showing the first 3/)).toBeInTheDocument();
  });

  it("explains a failed search", async () => {
    const user = userEvent.setup();
    mount();
    vi.spyOn(fleetApi, "sources").mockRejectedValue(new ApiError(403, "no"));
    await user.click(screen.getByRole("button", { name: "Add override" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/could not be listed/);
  });

  it("creates a global source row after the impact is shown", async () => {
    const user = userEvent.setup();
    mount();
    vi.spyOn(fleetApi, "sources").mockResolvedValue(page());
    const put = vi.spyOn(outagePolicyApi, "putSource").mockResolvedValue(sourceRow());
    await user.click(screen.getByRole("button", { name: "Add override" }));
    await user.click(await screen.findByRole("button", { name: "Override LinuxServer @ lnx-app-01" }));
    // The title comes first, then the context line.
    const form = screen.getByRole("form", { name: "Override for LinuxServer @ lnx-app-01" });
    const paragraphs = within(form).getAllByText(/./, { selector: "p" });
    expect(paragraphs[0]).toHaveTextContent("Override for LinuxServer @ lnx-app-01");
    expect(paragraphs[1]).toHaveTextContent(/Its class resolves to Alert/);
    expect(screen.getByRole("radio", { name: "Record only" })).toBeChecked();
    expect(screen.getByRole("group", { name: "Before you save" })).toHaveTextContent(
      "LinuxServer @ lnx-app-01 will no longer raise outage alerts.",
    );
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(put).toHaveBeenCalledWith("s3", { mode: "RECORD_ONLY", silence_grace_buckets: null });
    expect(await screen.findByText(/Saved\. LinuxServer @ lnx-app-01 is now Record only/)).toBeInTheDocument();
  });
});

describe("source overrides", () => {
  it("edits an override and states what it changes", async () => {
    const user = userEvent.setup();
    mount([row(), sourceRow({ mode: "ALERT" })]);
    const put = vi.spyOn(outagePolicyApi, "putSource").mockResolvedValue(sourceRow());
    await user.click(screen.getByRole("button", { name: "Edit the override of PaSeries @ pa-fw-02" }));
    await user.click(screen.getByRole("radio", { name: "Record only" }));
    expect(screen.getByRole("group", { name: "Before you save" })).toHaveTextContent(
      "PaSeries @ pa-fw-02 will no longer raise outage alerts.",
    );
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(put).toHaveBeenCalledWith(FW_02, { mode: "RECORD_ONLY", silence_grace_buckets: null });
  });

  it("removing the override of a removed source says it changes nothing", async () => {
    const user = userEvent.setup();
    mount([sourceRow({ source_name: null, source_class: null })]);
    await user.click(screen.getByRole("button", { name: /^Remove the override of Source no longer in inventory/ }));
    const note = screen.getByRole("group", { name: "Before you remove" });
    expect(within(note).getByText(/governs no source/)).toBeInTheDocument();
  });

  it("with no overrides, says so and still offers to add one", () => {
    mount([row()]);
    expect(screen.getByText(/No source is overridden/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add override" })).toBeInTheDocument();
  });
});

describe("reload after a change", () => {
  it("warns that counts may be stale when the reload fails", async () => {
    const user = userEvent.setup();
    mount();
    vi.spyOn(outagePolicyApi, "putClass").mockResolvedValue(row());
    vi.mocked(outagePolicyApi.list).mockRejectedValue(new Error("down"));
    await user.click(screen.getByRole("button", { name: "Set policy for Linux" }));
    await user.click(screen.getByRole("radio", { name: "Record only" }));
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/counts may be out of date/));
  });
});
