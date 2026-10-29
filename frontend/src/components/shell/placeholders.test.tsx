// UI-5.9 point 7: a placeholder must fit the field it sits in at 1024 px, so none of these is
// the long form that ended in an ellipsis. (I18N-12: rendered checks; the old test read the
// component sources and was kept true by comments holding the strings.)
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AnomalyFilters } from "@/components/behavior/AnomalyFilters";
import { TopSearch } from "./TopSearch";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/",
}));

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

describe("placeholders that clipped at 1024 px", () => {
  it("the top search names its scope in a short form", () => {
    render(<TopSearch />);
    const field = screen.getByRole("combobox");
    expect(field).toHaveAttribute("placeholder", "Search sources, rules, anomalies");
    expect(field).toHaveAccessibleName("Search sources, rules, offenses and anomalies");
  });
  it("the anomaly instance filter is short enough for its field", () => {
    render(<AnomalyFilters values={empty} sources={[]} />);
    expect(document.querySelector("#anomaly-instance")).toHaveAttribute("placeholder", "Instance…");
  });
});
