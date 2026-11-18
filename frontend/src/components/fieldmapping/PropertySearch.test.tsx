import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { PropertyInput } from "./PropertyInput";
import type { CatalogProperty } from "@/lib/api";

function Harness({ search }: { search: (q: string) => Promise<CatalogProperty[]> }) {
  const [value, setValue] = useState("");
  return <PropertyInput id="property" label="Property" value={value} onChange={setValue} suggestions={[]} search={search} />;
}

describe("server property search", () => {
  it("debounces edits, loads suggestions, and permits an unlisted name", async () => {
    const search = vi.fn().mockResolvedValue([{ name: "Late_property", property_type: "string", name_allowed: true, applies_to_source_type: null }]);
    render(<Harness search={search} />);
    const input = screen.getByRole("combobox");
    fireEvent.change(input, { target: { value: "L" } });
    fireEvent.change(input, { target: { value: "Late" } });
    expect(screen.getByText("Searching properties…")).toBeInTheDocument();
    expect(await screen.findByRole("option", { name: /Late_property/ })).toBeInTheDocument();
    expect(search).toHaveBeenCalledExactlyOnceWith("Late");
    expect(screen.getByText("1 suggestion")).toBeInTheDocument();
    fireEvent.change(input, { target: { value: "Unlisted" } });
    expect(input).toHaveValue("Unlisted");
  });
  it("shows a failed search and keeps text entry enabled", async () => {
    render(<Harness search={vi.fn().mockRejectedValue(new Error("failed"))} />);
    const input = screen.getByRole("combobox");
    fireEvent.change(input, { target: { value: "Anything" } });
    expect(await screen.findByText("Property search failed — names can still be typed")).toBeInTheDocument();
    expect(input).toHaveValue("Anything");
    expect(input).not.toBeDisabled();
  });
  it("ignores a stale response after the query changes", async () => {
    let resolve: (items: CatalogProperty[]) => void = () => {};
    const search = vi.fn().mockImplementationOnce(() => new Promise<CatalogProperty[]>(r => { resolve = r; })).mockResolvedValue([]);
    render(<Harness search={search} />);
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "first" } });
    await waitFor(() => expect(search).toHaveBeenCalledOnce());
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "second" } });
    resolve([{ name: "Stale", name_allowed: true, applies_to_source_type: null, property_type: "string" }]);
    await waitFor(() => expect(search).toHaveBeenCalledTimes(2));
    expect(screen.queryByRole("option", { name: /Stale/ })).not.toBeInTheDocument();
  });
});
