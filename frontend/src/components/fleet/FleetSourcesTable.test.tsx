import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { FleetSourcesTable } from "./FleetSourcesTable";
import { ApiError, fleetApi, type FleetSource } from "@/lib/api";

function source(over: Partial<FleetSource> = {}): FleetSource {
  return {
    id: "s1",
    instance_id: "i1",
    qradar_id: 7,
    name: "dmz-ips-05",
    type_name: "Linux OS",
    enabled: true,
    auto_discovered: true,
    first_seen_at: "2026-10-13T02:40:00Z",
    source_class: "IPS",
    basis: "NAME_REGEX",
    rule_id: "r1",
    override_class: null,
    override_by: null,
    override_at: null,
    conflicts: [{ basis: "TYPE_NAME_REGEX", rule_id: "r2", class: "LINUX" }],
    classified_at: null,
    review_status: "NEW",
    reviewed_at: null,
    reviewed_by: null,
    ...over,
  };
}

const rules = [
  {
    id: "r1",
    instance_id: null,
    kind: "NAME_REGEX",
    pattern: "ips-rule",
    target_class: "IPS",
    priority: 100,
    enabled: true,
    built_in: true,
    description: null,
    created_by: null,
    created_at: "",
    updated_at: "",
  },
] as const;

beforeEach(() => vi.restoreAllMocks());

describe("rows", () => {
  it("shows a NEW source with its conflict in words and the matching rule", () => {
    render(<FleetSourcesTable items={[source()]} rules={[...rules]} />);
    expect(screen.getByText("Linux OS")).toBeInTheDocument();
    expect(screen.getByText("ips-rule")).toBeInTheDocument();
    expect(screen.getByText(/name says IPS, type says Linux/)).toBeInTheDocument();
    expect(screen.getByText("New")).toBeInTheDocument();
    expect(screen.getByText("10-13 02:40 UTC")).toBeInTheDocument();
    expect(screen.getByText("Auto-discovered")).toBeInTheDocument();
  });

  it("does not offer mark reviewed on a reviewed source", () => {
    render(<FleetSourcesTable items={[source({ review_status: "REVIEWED", conflicts: [] })]} />);
    expect(screen.queryByRole("button", { name: /reviewed/i })).toBeNull();
  });

  it("shows unknown origin as not recorded", () => {
    render(<FleetSourcesTable items={[source({ auto_discovered: null })]} />);
    expect(screen.getByText(/origin not recorded/i)).toBeInTheDocument();
  });
});

describe("override", () => {
  async function openEditor() {
    await userEvent.click(screen.getByRole("button", { name: "Set class for dmz-ips-05" }));
  }

  it("sets the class and shows the API's answer", async () => {
    const spy = vi
      .spyOn(fleetApi, "setOverride")
      .mockResolvedValue(
        source({ source_class: "LINUX", basis: "OVERRIDE", override_class: "LINUX", rule_id: null, conflicts: [] }),
      );
    render(<FleetSourcesTable items={[source()]} />);
    await openEditor();
    await userEvent.selectOptions(screen.getByRole("combobox"), "LINUX");
    await userEvent.click(screen.getByRole("button", { name: "Apply" }));
    expect(spy).toHaveBeenCalledWith("s1", "LINUX");
    expect(await screen.findByText(/Class is now Linux/)).toBeInTheDocument();
    expect(screen.getByText("Operator override")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Clear override for dmz-ips-05" })).toBeInTheDocument();
  });

  it("clears the override with null", async () => {
    const spy = vi
      .spyOn(fleetApi, "setOverride")
      .mockResolvedValue(source({ source_class: "UNCLASSIFIED", basis: "UNCLASSIFIED", conflicts: [] }));
    render(
      <FleetSourcesTable items={[source({ override_class: "IPS", basis: "OVERRIDE", override_by: "efe" })]} />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Clear override for dmz-ips-05" }));
    expect(spy).toHaveBeenCalledWith("s1", null);
    expect(await screen.findByText(/Override cleared/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Clear override/ })).toBeNull();
  });

  it("names the permission on 403 and keeps the class", async () => {
    vi.spyOn(fleetApi, "setOverride").mockRejectedValue(new ApiError(403, "no"));
    render(<FleetSourcesTable items={[source()]} />);
    await openEditor();
    await userEvent.click(screen.getByRole("button", { name: "Apply" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("log_source:write");
    expect(alert).toHaveTextContent("Nothing was changed");
    expect(screen.getByText("Name rule")).toBeInTheDocument();
  });

  it("says the source is gone on 404", async () => {
    vi.spyOn(fleetApi, "setOverride").mockRejectedValue(new ApiError(404, "gone"));
    render(<FleetSourcesTable items={[source()]} />);
    await openEditor();
    await userEvent.click(screen.getByRole("button", { name: "Apply" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/no longer exists/i);
  });

  it("reports a server error as not changed", async () => {
    vi.spyOn(fleetApi, "setOverride").mockRejectedValue(new ApiError(500, "boom"));
    render(<FleetSourcesTable items={[source()]} />);
    await openEditor();
    await userEvent.click(screen.getByRole("button", { name: "Apply" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/not changed/i);
  });
});

describe("mark reviewed", () => {
  it("marks reviewed and drops the button", async () => {
    const spy = vi
      .spyOn(fleetApi, "markReviewed")
      .mockResolvedValue(source({ review_status: "REVIEWED" }));
    render(<FleetSourcesTable items={[source()]} />);
    await userEvent.click(screen.getByRole("button", { name: "Mark dmz-ips-05 reviewed" }));
    expect(await screen.findByText("Reviewed")).toBeInTheDocument();
    expect(spy).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", { name: /Mark dmz-ips-05 reviewed/ })).toBeNull();
  });

  it("sends one request for a double click", async () => {
    let resolve!: (s: FleetSource) => void;
    const spy = vi.spyOn(fleetApi, "markReviewed").mockReturnValue(new Promise((r) => (resolve = r)));
    render(<FleetSourcesTable items={[source()]} />);
    const btn = screen.getByRole("button", { name: "Mark dmz-ips-05 reviewed" });
    await userEvent.dblClick(btn);
    expect(spy).toHaveBeenCalledTimes(1);
    resolve(source({ review_status: "REVIEWED" }));
    await waitFor(() => expect(screen.getByText("Reviewed")).toBeInTheDocument());
  });

  it("names the permission on 403", async () => {
    vi.spyOn(fleetApi, "markReviewed").mockRejectedValue(new ApiError(403, "no"));
    render(<FleetSourcesTable items={[source()]} />);
    await userEvent.click(screen.getByRole("button", { name: "Mark dmz-ips-05 reviewed" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("log_source:write");
    expect(screen.getByText("New")).toBeInTheDocument();
  });
});
