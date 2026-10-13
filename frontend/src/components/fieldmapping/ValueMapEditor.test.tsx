import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { ValueMapEditor } from "./ValueMapEditor";
import type { ActionValueMap, DiscoveredValue } from "@/lib/api";

const DISCOVERED: DiscoveredValue[] = [
  { value: "BLOCK", events: 1800 },
  { value: "ALLOW", events: 7900 },
  { value: "ALERT", events: 120 },
];

function Harness({
  discovered = DISCOVERED,
  truncated = false,
  initial = { BLOCKED: ["BLOCK"], ALLOWED: ["ALLOW"] },
  blockedReason = null,
  note = null,
  onMap,
}: {
  discovered?: DiscoveredValue[] | null;
  truncated?: boolean;
  initial?: ActionValueMap;
  blockedReason?: string | null;
  note?: string | null;
  onMap?: (m: ActionValueMap) => void;
}) {
  const [map, setMap] = useState(initial);
  return (
    <ValueMapEditor
      fieldLabel="Action"
      outcomes={["BLOCKED", "ALLOWED"]}
      discovered={discovered}
      truncated={truncated}
      map={map}
      blockedReason={blockedReason}
      discoveredNote={note}
      onChange={(m) => {
        setMap(m);
        onMap?.(m);
      }}
    />
  );
}

const rowOf = (v: string) => screen.getByRole("row", { name: new RegExp(`^${v}\\b`) });

describe("ValueMapEditor", () => {
  it("shows each discovered value with its event count and its current outcome", () => {
    render(<Harness />);
    expect(within(rowOf("BLOCK")).getByText("1,800")).toBeInTheDocument();
    expect(within(rowOf("BLOCK")).getByRole("radio", { name: "Blocked" })).toBeChecked();
    expect(within(rowOf("ALLOW")).getByRole("radio", { name: "Allowed" })).toBeChecked();
    expect(within(rowOf("ALERT")).getByRole("radio", { name: "Not mapped" })).toBeChecked();
  });

  it("assigns a value to Blocked, Allowed or Not mapped", async () => {
    const user = userEvent.setup();
    const onMap = vi.fn();
    render(<Harness onMap={onMap} />);
    await user.click(within(rowOf("ALERT")).getByRole("radio", { name: "Blocked" }));
    expect(onMap).toHaveBeenLastCalledWith({ BLOCKED: ["BLOCK", "ALERT"], ALLOWED: ["ALLOW"] });
    await user.click(within(rowOf("BLOCK")).getByRole("radio", { name: "Not mapped" }));
    expect(onMap).toHaveBeenLastCalledWith({ BLOCKED: ["ALERT"], ALLOWED: ["ALLOW"] });
  });

  it("lists mapped values the last validation did not see, with no count", () => {
    render(<Harness initial={{ BLOCKED: ["DENY"], ALLOWED: [] }} />);
    const r = rowOf("DENY");
    expect(within(r).getByText("not seen in the last validation")).toBeInTheDocument();
    expect(within(r).getByText("—")).toBeInTheDocument();
    expect(within(r).getByRole("radio", { name: "Blocked" })).toBeChecked();
  });

  it("says a truncated list is truncated and that unlisted values are unmapped, not allowed", () => {
    render(<Harness truncated />);
    const note = screen.getByText(/this list is truncated/i);
    expect(note).toHaveTextContent(/count as unmapped, never as allowed/i);
  });

  it("does not claim truncation for a complete list", () => {
    render(<Harness />);
    expect(screen.queryByText(/truncated/i)).not.toBeInTheDocument();
  });

  it("says no values were discovered rather than showing an empty table", () => {
    render(<Harness discovered={null} initial={{ BLOCKED: [], ALLOWED: [] }} />);
    expect(screen.getByText(/listed no action values/i)).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("explains why values cannot be assigned when the property is unmapped", () => {
    render(<Harness blockedReason="No property is mapped for Action, so values cannot be assigned yet." />);
    expect(screen.getByRole("status")).toHaveTextContent(/no property is mapped for action/i);
  });

  it("shows where stale values came from", () => {
    render(<Harness note="These values were counted under WAF_Action; the mapping now reads Act." />);
    expect(screen.getByText(/counted under WAF_Action/)).toBeInTheDocument();
  });

  it("marks a value in both lists so it is not hidden", () => {
    render(<Harness initial={{ BLOCKED: ["BLOCK"], ALLOWED: ["BLOCK"] }} />);
    const r = rowOf("BLOCK");
    expect(within(r).getByText(/in both lists/i)).toBeInTheDocument();
    for (const radio of within(r).getAllByRole("radio")) expect(radio).not.toBeChecked();
  });

  it("adds a value the sample did not list", async () => {
    const user = userEvent.setup();
    const onMap = vi.fn();
    render(<Harness onMap={onMap} />);
    await user.type(screen.getByLabelText("Add a value that was not listed"), "DROP");
    await user.click(screen.getByRole("button", { name: "Add as blocked" }));
    expect(onMap).toHaveBeenLastCalledWith({ BLOCKED: ["BLOCK", "DROP"], ALLOWED: ["ALLOW"] });
    expect(rowOf("DROP")).toBeInTheDocument();
  });

  it("adds an unlisted value as Allowed too", async () => {
    const user = userEvent.setup();
    const onMap = vi.fn();
    render(<Harness onMap={onMap} />);
    await user.type(screen.getByLabelText("Add a value that was not listed"), "PASS");
    await user.click(screen.getByRole("button", { name: "Add as allowed" }));
    expect(onMap).toHaveBeenLastCalledWith({ BLOCKED: ["BLOCK"], ALLOWED: ["ALLOW", "PASS"] });
  });
});
