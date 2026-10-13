import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { FieldMappingForm } from "./FieldMappingForm";
import { SCHEMA, catalog, profile } from "./fixtures";
import { draftFromProfile, type MappingDraft } from "@/lib/fieldMapping";
import type { PropertyCatalogOut } from "@/lib/api";

function Harness({
  initial,
  cat = catalog(),
  showRequired = false,
  fieldErrors,
  onRefresh,
  copy,
}: {
  initial?: MappingDraft;
  cat?: PropertyCatalogOut | null;
  showRequired?: boolean;
  fieldErrors?: Record<string, string>;
  onRefresh?: () => Promise<void>;
  copy?: { options: { id: string; label: string }[]; onCopy: (id: string) => Promise<string> };
}) {
  const [draft, setDraft] = useState(initial ?? draftFromProfile(SCHEMA, profile()));
  return (
    <FieldMappingForm
      schema={SCHEMA}
      draft={draft}
      onChange={setDraft}
      catalog={cat}
      typeName="Generic WAF DSM"
      showRequired={showRequired}
      fieldErrors={fieldErrors}
      onRefreshCatalog={onRefresh}
      copyFrom={copy}
    />
  );
}

const input = (label: RegExp) => screen.getByRole("combobox", { name: label });

describe("FieldMappingForm", () => {
  it("renders one row per schema field with label, requirement and description", () => {
    render(<Harness />);
    for (const f of SCHEMA.fields) {
      expect(screen.getByText(f.label)).toBeInTheDocument();
      expect(screen.getByText(f.description)).toBeInTheDocument();
    }
    expect(screen.getAllByText("Required")).toHaveLength(2);
    expect(screen.getAllByText("Optional")).toHaveLength(3);
    expect(screen.getAllByRole("combobox", { name: /property$/i })).toHaveLength(SCHEMA.fields.length);
  });

  it("shows an unmapped optional field as Not mapped", () => {
    render(<Harness />);
    expect(input(/virtual host/i)).toHaveValue("");
    expect(input(/virtual host/i)).toHaveAttribute("placeholder", "Not mapped");
  });

  it("lets an optional field be set back to not mapped, and never a required one", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: "Do not map Action" }));
    expect(input(/action property/i)).toHaveValue("");
    expect(screen.queryByRole("button", { name: /do not map url path/i })).not.toBeInTheDocument();
  });

  it("suggests catalog names, and picks one with the keyboard", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const vhost = input(/virtual host/i);
    await user.click(vhost);
    const list = screen.getByRole("listbox");
    expect(within(list).getAllByRole("option").length).toBeGreaterThan(0);
    await user.type(vhost, "HTTP");
    expect(within(screen.getByRole("listbox")).getByRole("option", { name: /HTTP_Method/ })).toBeInTheDocument();
    await user.keyboard("{ArrowDown}{Enter}");
    expect(vhost).toHaveValue("HTTP_Method");
  });

  it("shows a name that is not allowed as not selectable, and does not pick it", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const vhost = input(/virtual host/i);
    await user.type(vhost, "bad");
    const option = screen.getByRole("option", { name: /bad;name/ });
    expect(option).toHaveAttribute("aria-disabled", "true");
    expect(option).toHaveTextContent(/not selectable: name not allowed/);
    await user.click(option);
    expect(vhost).toHaveValue("bad");
  });

  it("warns, without blocking, about a name the console does not list", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.type(input(/virtual host/i), "Host_Header");
    expect(
      screen.getByText("Not in this console’s property list. Validation will check it."),
    ).toBeInTheDocument();
  });

  it("warns about a property that exists but not for this source's type", () => {
    render(<Harness initial={draftFromProfile(SCHEMA, profile({ mapping: { URL: "WAF_URL", METHOD: "HTTP_Method" } }))} />);
    expect(
      screen.getByText(
        "This property has no parsing expression for this source’s log-source type (Generic WAF DSM).",
      ),
    ).toBeInTheDocument();
  });

  it("checks the pattern in the browser", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.type(input(/virtual host/i), "a;b");
    expect(screen.getByText(/not an allowed property name/i)).toBeInTheDocument();
    expect(input(/virtual host/i)).toHaveAttribute("aria-invalid", "true");
  });

  it("asks for a required field only after a save attempt", () => {
    const empty = draftFromProfile(SCHEMA, profile({ mapping: { URL: "", METHOD: "HTTP_Method" } }));
    const { rerender } = render(<Harness initial={empty} />);
    expect(screen.queryByText(/collection cannot run without it/i)).not.toBeInTheDocument();
    rerender(<Harness initial={empty} showRequired />);
    expect(screen.getByText(/collection cannot run without it/i)).toBeInTheDocument();
  });

  it("shows the server's message at the field", () => {
    render(<Harness fieldErrors={{ URL: "Property name 'URL' is reserved." }} />);
    expect(screen.getByText("Property name 'URL' is reserved.")).toBeInTheDocument();
    expect(input(/url path/i)).toHaveAttribute("aria-invalid", "true");
  });

  it("degrades to free text and says why when the catalog is unavailable", async () => {
    const user = userEvent.setup();
    render(
      <Harness
        cat={catalog({ status: "UNAVAILABLE", properties: [], detail: "QRadar refused the request (403)." })}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent(
      "The property list is unavailable: QRadar refused the request (403).",
    );
    const vhost = input(/virtual host/i);
    await user.type(vhost, "Anything_At_All");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(screen.queryByText(/not in this console/i)).not.toBeInTheDocument();
  });

  it("treats a catalog that never loaded as unavailable", () => {
    render(<Harness cat={null} />);
    expect(screen.getByRole("status")).toHaveTextContent(/unavailable/i);
  });

  it("refreshes the list on request", async () => {
    const user = userEvent.setup();
    const onRefresh = vi.fn().mockResolvedValue(undefined);
    render(<Harness onRefresh={onRefresh} />);
    await user.click(screen.getByRole("button", { name: "Refresh list" }));
    expect(onRefresh).toHaveBeenCalledOnce();
  });

  it("copies a mapping from another source and says nothing is saved yet", async () => {
    const user = userEvent.setup();
    const onCopy = vi.fn().mockResolvedValue("Copied from WAF b (version 2). Nothing is saved until you save.");
    render(<Harness copy={{ options: [{ id: "ls-2", label: "WAF b (v2)" }], onCopy }} />);
    await user.selectOptions(screen.getByLabelText("Copy mapping from…"), "ls-2");
    await user.click(screen.getByRole("button", { name: "Copy" }));
    expect(onCopy).toHaveBeenCalledWith("ls-2");
    expect(await screen.findByText(/nothing is saved until you save/i)).toBeInTheDocument();
  });

  it("reports a failed copy as an alert", async () => {
    const user = userEvent.setup();
    const onCopy = vi.fn().mockRejectedValue(new Error("Not copied. The source no longer exists."));
    render(<Harness copy={{ options: [{ id: "ls-2", label: "WAF b" }], onCopy }} />);
    await user.selectOptions(screen.getByLabelText("Copy mapping from…"), "ls-2");
    await user.click(screen.getByRole("button", { name: "Copy" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/not copied/i);
  });

  it("disables copy when no other source has a mapping", () => {
    render(<Harness copy={{ options: [], onCopy: vi.fn() }} />);
    expect(screen.getByLabelText("Copy mapping from…")).toBeDisabled();
  });
});
