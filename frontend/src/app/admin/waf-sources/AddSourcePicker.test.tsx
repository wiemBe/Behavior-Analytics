import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AddSourcePicker } from "./AddSourcePicker";
import { ApiError, api, type WafCandidateOut } from "@/lib/api";

const CANDIDATES: WafCandidateOut[] = [
  { log_source_id: "w1", qradar_id: 212, name: "WAF edge - new", type_name: "Generic WAF DSM", identifier: "10.1.2.3", source_class: "WAF", instance_id: "inst-1" },
  { log_source_id: "f1", qradar_id: 9, name: "Firewall core", type_name: "Palo Alto", identifier: null, source_class: null, instance_id: "inst-1" },
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

  it("searches the one text the label names", async () => {
    const spy = vi.spyOn(api, "wafCandidates").mockResolvedValue(CANDIDATES);
    const user = userEvent.setup();
    render(<AddSourcePicker defaultOpen />);
    await screen.findAllByRole("link");
    await user.type(screen.getByLabelText("Name, type, identifier or QRadar ID"), "edge");
    await vi.waitFor(() => expect(spy).toHaveBeenLastCalledWith({ q: "edge", limit: 50 }));
    await screen.findAllByRole("link");
  });

  it("says when nothing matches the search", async () => {
    vi.spyOn(api, "wafCandidates").mockResolvedValue([]);
    const user = userEvent.setup();
    render(<AddSourcePicker defaultOpen />);
    await user.type(screen.getByLabelText("Name, type, identifier or QRadar ID"), "zzz");
    const empty = await screen.findByText(/matches “zzz” in its name, type, identifier or QRadar ID/);
    expect(empty).toHaveTextContent(/not synced yet appears after the next inventory/);
    expect(screen.getByRole("link", { name: "Open the log source inventory" })).toHaveAttribute(
      "href",
      "/log-sources",
    );
  });

  it("says when every log source already has a profile", async () => {
    vi.spyOn(api, "wafCandidates").mockResolvedValue([]);
    render(<AddSourcePicker defaultOpen />);
    expect(await screen.findByText(/Every known log source already has a WAF profile\./)).toBeInTheDocument();
  });

  it("shows name, type, identifier and QRadar ID as columns, ids right-aligned", async () => {
    vi.spyOn(api, "wafCandidates").mockResolvedValue(CANDIDATES);
    render(<AddSourcePicker defaultOpen />);
    const table = await screen.findByRole("table", { name: "Log sources without a WAF profile" });
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual([
      "Log source",
      "Type",
      "Identifier",
      "QRadar ID",
    ]);
    const [, first, second] = within(table).getAllByRole("row");
    const cells = within(first).getAllByRole("cell");
    expect(cells.map((c) => c.textContent)).toEqual([
      "WAF edge - new Classed as WAF",
      "Generic WAF DSM",
      "10.1.2.3",
      "212",
    ]);
    expect(cells[3]).toHaveClass("num");
    expect(within(second).getAllByRole("cell").map((c) => c.textContent)).toEqual(["Firewall core", "Palo Alto", "", "9"]);
  });

  it("notes the cap when a full page came back, and not below it", async () => {
    const full = Array.from({ length: 50 }, (_, i) => ({ ...CANDIDATES[1], log_source_id: `f${i}`, name: `src-${i}` }));
    const spy = vi.spyOn(api, "wafCandidates").mockResolvedValue(full);
    const { unmount } = render(<AddSourcePicker defaultOpen />);
    expect(await screen.findByText("Showing the first 50. Narrow the search to see others.")).toBeInTheDocument();
    unmount();
    spy.mockResolvedValue(CANDIDATES);
    render(<AddSourcePicker defaultOpen />);
    await screen.findAllByRole("link");
    expect(screen.queryByText(/Showing the first/)).not.toBeInTheDocument();
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
