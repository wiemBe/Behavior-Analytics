import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AddSourcePicker } from "./AddSourcePicker";
import { ApiError, api, type WafCandidateOut } from "@/lib/api";

const CANDIDATES: WafCandidateOut[] = [
  { log_source_id: "w1", qradar_id: 212, name: "WAF edge - new", type_name: "Generic WAF DSM", source_class: "WAF", instance_id: "inst-1" },
  { log_source_id: "f1", qradar_id: 9, name: "Firewall core", type_name: "Palo Alto", source_class: null, instance_id: "inst-1" },
];

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("AddSourcePicker", () => {
  it("is closed until asked", () => {
    render(<AddSourcePicker />);
    expect(screen.queryByRole("region", { name: "Choose a log source" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add WAF source" })).toHaveAttribute("aria-expanded", "false");
  });

  it("lists candidates in the order served, WAF-classed first, each linking to its editor", async () => {
    const spy = vi.spyOn(api, "wafCandidates").mockResolvedValue(CANDIDATES);
    const user = userEvent.setup();
    render(<AddSourcePicker />);
    await user.click(screen.getByRole("button", { name: "Add WAF source" }));
    const links = await screen.findAllByRole("link");
    expect(links.map((l) => l.textContent)).toEqual(["WAF edge - new", "Firewall core"]);
    expect(links[0]).toHaveAttribute("href", "/admin/waf-sources/w1?instance=inst-1");
    expect(screen.getByText("Classed as WAF")).toBeInTheDocument();
    expect(spy).toHaveBeenCalledWith({ q: undefined, limit: 50 });
  });

  it("searches by name", async () => {
    const spy = vi.spyOn(api, "wafCandidates").mockResolvedValue(CANDIDATES);
    const user = userEvent.setup();
    render(<AddSourcePicker defaultOpen />);
    await screen.findAllByRole("link");
    await user.type(screen.getByLabelText("Search log sources by name"), "edge");
    await vi.waitFor(() => expect(spy).toHaveBeenLastCalledWith({ q: "edge", limit: 50 }));
    await screen.findAllByRole("link");
  });

  it("says when nothing matches the search", async () => {
    vi.spyOn(api, "wafCandidates").mockResolvedValue([]);
    const user = userEvent.setup();
    render(<AddSourcePicker defaultOpen />);
    await user.type(screen.getByLabelText("Search log sources by name"), "zzz");
    expect(await screen.findByText(/name containing “zzz”/)).toBeInTheDocument();
  });

  it("says when every log source already has a profile", async () => {
    vi.spyOn(api, "wafCandidates").mockResolvedValue([]);
    render(<AddSourcePicker defaultOpen />);
    expect(await screen.findByText("Every known log source already has a WAF profile.")).toBeInTheDocument();
  });

  it("explains a failed lookup", async () => {
    vi.spyOn(api, "wafCandidates").mockRejectedValue(new ApiError(403, "forbidden"));
    render(<AddSourcePicker defaultOpen />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/could not be listed.*permission/i);
  });

  it("shows a searching state while it waits", () => {
    vi.spyOn(api, "wafCandidates").mockReturnValue(new Promise(() => {}));
    render(<AddSourcePicker defaultOpen />);
    expect(screen.getByRole("status")).toHaveTextContent(/searching/i);
  });
});
