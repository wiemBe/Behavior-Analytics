import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AddIpsSourcePicker } from "./AddIpsSourcePicker";
import { candidate } from "./fixtures";
import { ApiError, ipsApi } from "@/lib/api";

const CANDIDATES = [
  candidate(),
  candidate({ log_source_id: "ls-413", qradar_id: 413, name: "CiscoFirepowerThreatDefense @ ftd-01", type_name: "Cisco Firepower Threat Defense" }),
  candidate({ log_source_id: "ls-9", qradar_id: 9, name: "linux-ids-02", type_name: "Linux OS", source_class: null }),
];

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("AddIpsSourcePicker", () => {
  it("is closed until asked", () => {
    render(<AddIpsSourcePicker instanceId="inst-1" />);
    expect(screen.queryByRole("region", { name: "Choose a log source" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add IPS source" })).toHaveAttribute("aria-expanded", "false");
  });

  it("lists candidates in the order served, each linking to its editor", async () => {
    const spy = vi.spyOn(ipsApi, "candidates").mockResolvedValue(CANDIDATES);
    const user = userEvent.setup();
    render(<AddIpsSourcePicker instanceId="inst-1" />);
    await user.click(screen.getByRole("button", { name: "Add IPS source" }));
    const links = await screen.findAllByRole("link");
    expect(links.map((l) => l.textContent)).toEqual([
      "PaSeries @ pa-fw-01",
      "CiscoFirepowerThreatDefense @ ftd-01",
      "linux-ids-02",
    ]);
    expect(links[0]).toHaveAttribute("href", "/admin/ips-sources/ls-412");
    expect(screen.getAllByText("Classed as IPS")).toHaveLength(2);
    expect(spy).toHaveBeenCalledWith({ instance_id: "inst-1", q: undefined, limit: 50 });
  });

  it("searches the one text the label names", async () => {
    const spy = vi.spyOn(ipsApi, "candidates").mockResolvedValue(CANDIDATES);
    const user = userEvent.setup();
    render(<AddIpsSourcePicker instanceId="inst-1" defaultOpen />);
    await screen.findAllByRole("link");
    await user.type(screen.getByLabelText("Name, type, identifier or QRadar ID"), "linux");
    await vi.waitFor(() =>
      expect(spy).toHaveBeenLastCalledWith({ instance_id: "inst-1", q: "linux", limit: 50 }),
    );
    await screen.findAllByRole("link");
  });

  it("says when nothing matches the search", async () => {
    vi.spyOn(ipsApi, "candidates").mockResolvedValue([]);
    const user = userEvent.setup();
    render(<AddIpsSourcePicker instanceId="inst-1" defaultOpen />);
    await user.type(screen.getByLabelText("Name, type, identifier or QRadar ID"), "zzz");
    const empty = await screen.findByText(/matches “zzz” in its name, type, identifier or QRadar ID/);
    expect(empty).toHaveTextContent(/not synced yet appears after the next inventory/);
    expect(screen.getByRole("link", { name: "Open the log source inventory" })).toHaveAttribute(
      "href",
      "/log-sources",
    );
  });

  it("says when every log source already has a profile", async () => {
    vi.spyOn(ipsApi, "candidates").mockResolvedValue([]);
    render(<AddIpsSourcePicker instanceId="inst-1" defaultOpen />);
    expect(await screen.findByText(/Every known log source already has an IPS profile\./)).toBeInTheDocument();
  });

  it("shows name, type, identifier and QRadar ID as columns, ids right-aligned", async () => {
    vi.spyOn(ipsApi, "candidates").mockResolvedValue([
      candidate({ identifier: "10.4.0.21" }),
      candidate({ log_source_id: "ls-9", qradar_id: 9, name: "linux-ids-02", type_name: null, identifier: null, source_class: null }),
    ]);
    render(<AddIpsSourcePicker instanceId="inst-1" defaultOpen />);
    const table = await screen.findByRole("table", { name: "Log sources without an IPS profile" });
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual([
      "Log source",
      "Type",
      "Identifier",
      "QRadar ID",
    ]);
    const [, first, second] = within(table).getAllByRole("row");
    const cells = within(first).getAllByRole("cell");
    expect(cells.map((c) => c.textContent)).toEqual([
      "PaSeries @ pa-fw-01 Classed as IPS",
      "Palo Alto PA Series",
      "10.4.0.21",
      "412",
    ]);
    expect(cells[3]).toHaveClass("num");
    expect(within(second).getAllByRole("cell").map((c) => c.textContent)).toEqual(["linux-ids-02", "", "", "9"]);
  });

  it("notes the cap when a full page came back", async () => {
    const full = Array.from({ length: 50 }, (_, i) =>
      candidate({ log_source_id: `ls-${i}`, qradar_id: i + 1, name: `src-${i}` }),
    );
    vi.spyOn(ipsApi, "candidates").mockResolvedValue(full);
    render(<AddIpsSourcePicker instanceId="inst-1" defaultOpen />);
    expect(await screen.findByText("Showing the first 50. Narrow the search to see others.")).toBeInTheDocument();
  });

  it("does not note a cap below a full page", async () => {
    vi.spyOn(ipsApi, "candidates").mockResolvedValue(CANDIDATES);
    render(<AddIpsSourcePicker instanceId="inst-1" defaultOpen />);
    await screen.findAllByRole("link");
    expect(screen.queryByText(/Showing the first/)).not.toBeInTheDocument();
  });

  it("names admin:ips when the lookup is forbidden", async () => {
    vi.spyOn(ipsApi, "candidates").mockRejectedValue(new ApiError(403, "forbidden"));
    render(<AddIpsSourcePicker instanceId="inst-1" defaultOpen />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/could not be listed.*admin:ips/i);
  });

  it("explains a failed lookup without leaking the error", async () => {
    vi.spyOn(ipsApi, "candidates").mockRejectedValue(new ApiError(500, "psycopg2 trace"));
    render(<AddIpsSourcePicker instanceId="inst-1" defaultOpen />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/could not be listed/i);
    expect(alert).not.toHaveTextContent("psycopg2");
  });

  it("shows a searching state while it waits", () => {
    vi.spyOn(ipsApi, "candidates").mockReturnValue(new Promise(() => {}));
    render(<AddIpsSourcePicker instanceId="inst-1" defaultOpen />);
    expect(screen.getByRole("status")).toHaveTextContent(/searching/i);
  });

  it("says so, and asks nothing, when no QRadar instance is known", () => {
    const spy = vi.spyOn(ipsApi, "candidates");
    render(<AddIpsSourcePicker instanceId={null} defaultOpen />);
    expect(screen.getByRole("alert")).toHaveTextContent(/no QRadar instance is known/);
    expect(screen.queryByLabelText("Name, type, identifier or QRadar ID")).not.toBeInTheDocument();
    expect(spy).not.toHaveBeenCalled();
  });
});
