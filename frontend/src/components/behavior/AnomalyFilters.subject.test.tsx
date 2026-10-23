import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { parseSubjectQuery } from "@/lib/anomalyQuery";

import { AnomalyFilters, chipLabel, withoutFilter } from "./AnomalyFilters";

const empty = {
  active_only: false,
  log_source_id: "",
  instance_id: "",
  anomaly_type: "",
  state: "",
  severity: "",
  evidence_status: "",
  since: "",
  until: "",
};

describe("subject filter", () => {
  it("is a labelled select, with all subjects as the default", () => {
    render(<AnomalyFilters values={empty} sources={[]} />);
    const select = screen.getByLabelText("Subject kind") as HTMLSelectElement;
    expect(select.value).toBe("");
    expect(select.name).toBe("subject_kind");
    expect([...select.options].map((option) => option.textContent)).toEqual([
      "All subjects",
      "Log sources",
      "WAF endpoints",
      "IPS entities",
      "IPS sources",
      "IPS entities and sources",
      "Property watch values",
    ]);
  });

  it("round-trips the combined IPS value through the form and URL parser", () => {
    const { container } = render(
      <AnomalyFilters values={{ ...empty, subject_kind: "IPS" }} sources={[]} />,
    );
    const form = container.querySelector("form")!;
    const query = new URLSearchParams(new FormData(form) as never).toString();
    expect(query).toContain("subject_kind=IPS");
    expect(parseSubjectQuery(Object.fromEntries(new URLSearchParams(query))).subject_kind).toBe(
      "IPS",
    );
    expect(screen.getByRole("link", { name: /IPS entities and sources/ })).toHaveTextContent(
      "Subject: IPS entities and sources",
    );
  });

  it("offers WAF detector types by name, in their own group beside the Phase A ones", () => {
    render(<AnomalyFilters values={empty} sources={[]} />);
    const select = screen.getByLabelText("Detector type");
    const waf = within(select).getByRole("group", { name: "WAF endpoints" });
    const names = within(waf).getAllByRole("option").map((o) => o.textContent);
    expect(names).toContain("Request-rate spike");
    expect(names).toContain("New endpoint access");
    expect(names.some((n) => n?.startsWith("WAF_"))).toBe(false);
    const phaseA = within(select).getByRole("group", { name: "Log sources" });
    expect(within(phaseA).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "Volume spike",
      "Volume drop",
      "No events",
    ]);
  });

  it("labels its chip, and clearing the chip keeps the other filters", () => {
    expect(chipLabel("subject_kind", "WAF_ENDPOINT", [])).toBe("Subject: WAF endpoints");
    expect(chipLabel("subject_kind", "LOG_SOURCE", [])).toBe("Subject: Log sources");
    const url = withoutFilter({ ...empty, subject_kind: "WAF_ENDPOINT", state: "OPEN" }, "subject_kind");
    expect(url).toBe("/anomalies?state=OPEN");
  });

  it("names a WAF detector in its chip as an analyst would", () => {
    expect(chipLabel("anomaly_type", "WAF_NEW_ENDPOINT_ACCESS", [])).toBe("Detector: New endpoint access");
  });

  it("shows no chip when no subject filter is set", () => {
    render(<AnomalyFilters values={empty} sources={[]} />);
    expect(screen.queryByText(/Filtered by/)).not.toBeInTheDocument();
  });
});
