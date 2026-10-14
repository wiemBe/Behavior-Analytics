import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AnomalyFilters, chipLabel, toInputValue } from "./AnomalyFilters";

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

describe("toInputValue", () => {
  it("shows a normalised ISO instant in a datetime-local field", () => {
    // The page passes `2026-10-12T09:15:00.000Z`; a datetime-local input shows
    // nothing for that, so the field used to come back blank after filtering.
    expect(toInputValue("2026-10-12T09:15:00.000Z")).toBe("2026-10-12T09:15");
  });

  it("gives an empty or unparseable value as empty", () => {
    expect(toInputValue("")).toBe("");
    expect(toInputValue("not-a-date")).toBe("");
  });
});

describe("time filter fields", () => {
  it("labels both fields visibly and names the timezone", () => {
    render(<AnomalyFilters values={empty} sources={[]} />);
    expect(screen.getByLabelText("Start time (UTC)")).toHaveAttribute("type", "datetime-local");
    expect(screen.getByLabelText("End time (UTC)")).toHaveAttribute("type", "datetime-local");
    // Visible, not screen-reader-only.
    expect(screen.getByText("Start time (UTC)", { selector: "label" }).className).not.toContain(
      "sr-only",
    );
  });

  it("keeps an applied time filter in its field", () => {
    render(
      <AnomalyFilters values={{ ...empty, since: "2026-10-12T09:15:00.000Z" }} sources={[]} />,
    );
    expect(screen.getByLabelText("Start time (UTC)")).toHaveValue("2026-10-12T09:15");
  });
});

describe("filter chips", () => {
  it("names a detector as an analyst would, and a time with its timezone", () => {
    expect(chipLabel("anomaly_type", "VOLUME_DROP", [])).toBe("Detector: Volume drop");
    expect(chipLabel("since", "2026-10-12T09:15:00.000Z", [])).toBe(
      "From: 2026-10-12 09:15:00 UTC",
    );
  });

  it("shows active only as a visible clearable control", () => {
    const first = render(<AnomalyFilters values={empty} sources={[]} />);
    expect(screen.getByLabelText("Active only")).not.toBeChecked();
    expect(screen.getByText("Active only", { selector: "label" })).toBeVisible();
    first.unmount();

    render(
      <AnomalyFilters values={{ ...empty, active_only: true }} sources={[]} />,
    );
    expect(screen.getByLabelText("Active only")).toBeChecked();
    expect(screen.getByRole("link", { name: /Remove filter Active only/ })).toHaveAttribute(
      "href",
      "/anomalies",
    );
  });
});
