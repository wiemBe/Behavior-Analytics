import { render, screen } from "@testing-library/react";
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

  it("searches by name", async () => {
    const spy = vi.spyOn(ipsApi, "candidates").mockResolvedValue(CANDIDATES);
    const user = userEvent.setup();
    render(<AddIpsSourcePicker instanceId="inst-1" defaultOpen />);
    await screen.findAllByRole("link");
    await user.type(screen.getByLabelText("Search log sources by name"), "linux");
    await vi.waitFor(() =>
      expect(spy).toHaveBeenLastCalledWith({ instance_id: "inst-1", q: "linux", limit: 50 }),
    );
    await screen.findAllByRole("link");
  });

  it("says when nothing matches the search", async () => {
    vi.spyOn(ipsApi, "candidates").mockResolvedValue([]);
    const user = userEvent.setup();
    render(<AddIpsSourcePicker instanceId="inst-1" defaultOpen />);
    await user.type(screen.getByLabelText("Search log sources by name"), "zzz");
    expect(await screen.findByText(/name containing “zzz”/)).toBeInTheDocument();
  });

  it("says when every log source already has a profile", async () => {
    vi.spyOn(ipsApi, "candidates").mockResolvedValue([]);
    render(<AddIpsSourcePicker instanceId="inst-1" defaultOpen />);
    expect(await screen.findByText("Every known log source already has an IPS profile.")).toBeInTheDocument();
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
    expect(screen.queryByLabelText("Search log sources by name")).not.toBeInTheDocument();
    expect(spy).not.toHaveBeenCalled();
  });
});
