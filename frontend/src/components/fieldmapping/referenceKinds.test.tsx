// The additive, kind-aware paths of the generic components (T-053). The WAF
// paths are covered unchanged by the existing tests; the first test here pins
// that a form given no kinds renders as it always did.

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import { FieldMappingForm } from "@/components/fieldmapping/FieldMappingForm";
import { MappingHistory } from "@/components/fieldmapping/MappingHistory";
import { SCHEMA, catalog, report } from "@/components/fieldmapping/fixtures";
import { ValidationReport } from "@/components/fieldmapping/ValidationReport";
import { formSchema, draftFromDefaults, type IpsDraft } from "@/app/admin/ips-sources/ips";
import { IPS_SCHEMA } from "@/app/admin/ips-sources/fixtures";
import { draftFromDefaults as wafDefaults } from "@/lib/fieldMapping";

function KindedForm({ initial }: { initial: IpsDraft }) {
  const [draft, setDraft] = useState(initial);
  return (
    <FieldMappingForm
      schema={formSchema(IPS_SCHEMA)}
      draft={draft}
      onChange={(next) => setDraft({ ...draft, ...next })}
      catalog={catalog()}
      typeName="Palo Alto PA Series"
      showRequired={false}
      referenceKinds={draft.kinds}
      onKindChange={(key, kind) =>
        setDraft({ ...draft, kinds: { ...draft.kinds, [key]: kind as IpsDraft["kinds"][string] }, mapping: { ...draft.mapping, [key]: "" } })
      }
    />
  );
}

describe("FieldMappingForm without reference kinds", () => {
  it("shows no kind switch and no select, as before", () => {
    render(
      <FieldMappingForm
        schema={SCHEMA}
        draft={wafDefaults(SCHEMA)}
        onChange={() => {}}
        catalog={catalog()}
        typeName="Generic WAF DSM"
        showRequired={false}
      />,
    );
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
    expect(screen.queryByRole("group")).not.toBeInTheDocument();
    expect(screen.getAllByRole("combobox").every((c) => c.tagName === "INPUT")).toBe(true);
  });

  it("shows no kind switch for a schema that offers one kind, even when kinds are passed", () => {
    render(
      <FieldMappingForm
        schema={SCHEMA}
        draft={wafDefaults(SCHEMA)}
        onChange={() => {}}
        catalog={catalog()}
        typeName="Generic WAF DSM"
        showRequired={false}
        referenceKinds={{}}
        onKindChange={() => {}}
      />,
    );
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
  });
});

describe("FieldMappingForm with reference kinds", () => {
  it("shows a switch per field and lets the operator change the kind", async () => {
    const user = userEvent.setup();
    render(<KindedForm initial={draftFromDefaults(IPS_SCHEMA)} />);
    expect(screen.getAllByRole("group", { name: /reference kind/i })).toHaveLength(IPS_SCHEMA.fields.length);
    const group = screen.getByRole("group", { name: /Action reference kind/i });
    // An unmapped field defaults to the first kind, a normalized field.
    expect(within(group).getByRole("radio", { name: "QRadar field" })).toBeChecked();
    await user.click(within(group).getByRole("radio", { name: "Custom property" }));
    expect(screen.getByRole("combobox", { name: /action property/i }).tagName).toBe("INPUT");
  });

  it("offers an optional normalized field an explicit Not mapped choice, a required one none", () => {
    render(<KindedForm initial={draftFromDefaults(IPS_SCHEMA)} />);
    const action = screen.getByRole("combobox", { name: /action qradar field/i });
    expect(within(action).getAllByRole("option")[0]).toHaveTextContent("Not mapped");
    const signature = screen.getByRole("combobox", { name: /signature qradar field/i });
    expect(within(signature).queryByRole("option", { name: "Choose a field" })).not.toBeInTheDocument();
  });

  it("shows a value that is not an allowed normalized field instead of silently dropping it", () => {
    const draft = draftFromDefaults(IPS_SCHEMA);
    render(<KindedForm initial={{ ...draft, mapping: { ...draft.mapping, SIGNATURE: "threatid" } }} />);
    const signature = screen.getByRole("combobox", { name: /signature qradar field/i });
    expect(signature).toHaveValue("threatid");
    expect(within(signature).getByRole("option", { name: /threatid \(not an allowed field\)/ })).toBeInTheDocument();
  });
});

describe("ValidationReport options", () => {
  const props = {
    schema: SCHEMA,
    report: report(),
    profileVersion: 4,
    pending: null,
    requiredCoverage: 0.9,
  };

  it("shows every column by default", () => {
    render(<ValidationReport {...props} />);
    expect(screen.getByRole("columnheader", { name: /Distinct values/ })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: /Plausible/ })).toBeInTheDocument();
  });

  it("leaves out the columns a domain does not measure", () => {
    render(<ValidationReport {...props} hideColumns={["distinct", "plausible"]} />);
    expect(screen.queryByRole("columnheader", { name: /Distinct values/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: /Plausible/ })).not.toBeInTheDocument();
    expect(screen.getAllByRole("columnheader")).toHaveLength(3);
  });

  it("shows notes as warnings above the table", () => {
    render(<ValidationReport {...props} notes={[{ key: "n", text: "A warning, not a failure." }]} />);
    expect(screen.getByText("A warning, not a failure.")).toHaveClass("is-warn");
  });
});

describe("MappingHistory options", () => {
  const entry = {
    at: "2026-10-13T09:00:00Z",
    actor: "system",
    action: "x.profile.validated",
    profile_version: 2,
    mapping_before: null,
    mapping_after: null,
    outcome: "SOURCE_BUSY",
    reasons: null,
  };

  it("uses a domain's action words and outcome sentence when given", () => {
    render(
      <MappingHistory
        schema={SCHEMA}
        entries={[entry]}
        actionLabel={() => "Checked"}
        outcomeNote={() => "It did not run."}
      />,
    );
    expect(screen.getByText("Checked")).toBeInTheDocument();
    expect(screen.getByText("It did not run.")).toBeInTheDocument();
  });

  it("falls back to the plain outcome line when the note has nothing to say", () => {
    render(<MappingHistory schema={SCHEMA} entries={[entry]} outcomeNote={() => null} />);
    expect(screen.getByText("Outcome: source_busy")).toBeInTheDocument();
  });
});
