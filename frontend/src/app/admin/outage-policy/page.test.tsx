import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import Loading from "./loading";
import OutagePolicyPage from "./page";
import { ApiError, outagePolicyApi } from "@/lib/api";
import { SUMMARY, row, sourceRow, GONE, INSTANCE_B } from "./fixtures";

beforeEach(() => vi.restoreAllMocks());

function classRowFor(name: string) {
  return screen.getByRole("row", { name: new RegExp(`^${name}\\b`) });
}

describe("outage policy page", () => {
  it("has one h1, states what record only does once, and leads with the classes", async () => {
    vi.spyOn(outagePolicyApi, "list").mockResolvedValue([row()]);
    vi.spyOn(outagePolicyApi, "summary").mockResolvedValue(SUMMARY);
    render(await OutagePolicyPage());
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getAllByText(/still records the outage/)).toHaveLength(1);
    const text = document.body.textContent ?? "";
    expect(text).toMatch(/\/behavior/);
    expect(text).toMatch(/already open resolves normally/);
    expect(text).toMatch(/NO_EVENTS/);
    const headings = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
    expect(headings).toEqual(["Classes", "Source overrides"]);
  });

  it("empty: says every monitored source alerts by default, and still lists the classes", async () => {
    vi.spyOn(outagePolicyApi, "list").mockResolvedValue([]);
    vi.spyOn(outagePolicyApi, "summary").mockResolvedValue(SUMMARY);
    render(await OutagePolicyPage());
    expect(screen.getByRole("status")).toHaveTextContent(/No outage policy is set/);
    expect(screen.getByRole("status")).toHaveTextContent(/Every monitored source raises an alert/);
    // Three classes with an enabled source; DNS has none and no row.
    expect(screen.getAllByRole("button", { name: /^Set policy for / })).toHaveLength(3);
    expect(screen.getAllByText("From the default")).toHaveLength(3);
    expect(screen.queryByRole("button", { name: /^Edit / })).toBeNull();
  });

  it("shows the class-row basis against the default basis", async () => {
    vi.spyOn(outagePolicyApi, "list").mockResolvedValue([row({ silence_grace_buckets: 6 })]);
    vi.spyOn(outagePolicyApi, "summary").mockResolvedValue(SUMMARY);
    render(await OutagePolicyPage());
    const fw = classRowFor("Firewall");
    expect(within(fw).getByText("Record only")).toBeInTheDocument();
    expect(within(fw).getByText("From the class row")).toBeInTheDocument();
    expect(within(fw).getByText("6 buckets")).toBeInTheDocument();
    expect(within(fw).getByText("30 min")).toBeInTheDocument();
    expect(within(fw).getByText("12")).toBeInTheDocument();
    const linux = classRowFor("Linux");
    expect(within(linux).getByText("Alert")).toBeInTheDocument();
    expect(within(linux).getByText("From the default")).toBeInTheDocument();
    expect(within(linux).getByText("Default · 1 bucket (5 min)")).toBeInTheDocument();
    // Monitored sources, as in every row: the unit is in the column header.
    expect(within(linux).getByText("8")).toBeInTheDocument();
    expect(within(linux).queryByText("enabled")).toBeNull();
    expect(within(fw).queryByText("monitored")).toBeNull();
  });

  it("names what the buttons do in their visible words", async () => {
    vi.spyOn(outagePolicyApi, "list").mockResolvedValue([row(), sourceRow()]);
    vi.spyOn(outagePolicyApi, "summary").mockResolvedValue(SUMMARY);
    render(await OutagePolicyPage());
    const classBtn = screen.getByRole("button", { name: "Use the default for Firewall" });
    expect(classBtn).toHaveTextContent(/^Use default$/);
    const sourceBtn = screen.getByRole("button", { name: "Remove the override of PaSeries @ pa-fw-02" });
    expect(sourceBtn).toHaveTextContent(/^Remove override$/);
    expect(screen.queryByText("Remove row")).toBeNull();
  });

  it("takes the minutes from the API's bucket width", async () => {
    vi.spyOn(outagePolicyApi, "list").mockResolvedValue([row({ silence_grace_buckets: 6 })]);
    vi.spyOn(outagePolicyApi, "summary").mockResolvedValue({ ...SUMMARY, bucket_seconds: 60 });
    render(await OutagePolicyPage());
    expect(within(classRowFor("Firewall")).getByText("6 min")).toBeInTheDocument();
  });

  it("right-aligns the counts", async () => {
    vi.spyOn(outagePolicyApi, "list").mockResolvedValue([row()]);
    vi.spyOn(outagePolicyApi, "summary").mockResolvedValue(SUMMARY);
    render(await OutagePolicyPage());
    const fw = classRowFor("Firewall");
    expect(within(fw).getByText("12").closest("td")).toHaveClass("num");
    expect(screen.getByRole("columnheader", { name: "Monitored sources covered" })).toHaveClass("num");
  });

  it("an instance-scoped row carries a badge, and no edit button", async () => {
    vi.spyOn(outagePolicyApi, "list").mockResolvedValue([
      row({
        scope_value: "LINUX",
        instance_id: INSTANCE_B,
        instance_name: "qradar-lab-eu",
        governed_monitored_sources: 5,
      }),
    ]);
    vi.spyOn(outagePolicyApi, "summary").mockResolvedValue(SUMMARY);
    render(await OutagePolicyPage());
    const badge = screen.getByText("Instance: qradar-lab-eu");
    const line = badge.closest("tr") as HTMLElement;
    expect(within(line).getByText("A row for one instance")).toBeInTheDocument();
    expect(within(line).queryByRole("button", { name: /^Edit/ })).toBeNull();
    expect(within(line).getByRole("button", { name: /^Use the default for Linux/ })).toBeInTheDocument();
    // No count per instance, and the text says why rather than pointing at a class row.
    expect(within(line).getByText("Not counted per instance")).toBeInTheDocument();
    expect(within(line).getAllByLabelText("Not available")).toHaveLength(2);
    // The default line for the class is still there.
    expect(screen.getAllByRole("button", { name: "Set policy for Linux" })).toHaveLength(1);
  });

  it("lists source overrides by name, who and when in UTC", async () => {
    vi.spyOn(outagePolicyApi, "list").mockResolvedValue([row(), sourceRow()]);
    vi.spyOn(outagePolicyApi, "summary").mockResolvedValue(SUMMARY);
    render(await OutagePolicyPage());
    const r = screen.getByRole("row", { name: /PaSeries @ pa-fw-02/ });
    expect(within(r).getByText("Firewall")).toBeInTheDocument();
    expect(within(r).getByText("Alert")).toBeInTheDocument();
    expect(within(r).getByText("s.okafor")).toBeInTheDocument();
    expect(within(r).getByText("10-09 14:22 UTC")).toBeInTheDocument();
    expect(screen.queryByText(/5a1f0000/)).toBeNull();
  });

  it("a removed source reads as such, never as its id, and offers only removal", async () => {
    vi.spyOn(outagePolicyApi, "list").mockResolvedValue([
      row(),
      sourceRow({ scope_value: GONE, source_name: null, source_class: null, mode: "RECORD_ONLY" }),
    ]);
    vi.spyOn(outagePolicyApi, "summary").mockResolvedValue(SUMMARY);
    render(await OutagePolicyPage());
    const r = screen.getByRole("row", { name: /Source no longer in inventory/ });
    expect(within(r).queryByRole("button", { name: /^Edit/ })).toBeNull();
    expect(within(r).getByRole("button", { name: /^Remove the override of Source no longer/ })).toBeInTheDocument();
    // The id is only a short reference, not the name.
    expect(within(r).getByText(/Log source id 5a1f0000/)).toBeInTheDocument();
    expect(screen.queryByText(GONE)).toBeNull();
  });

  it("403: names the permission and shows no controls", async () => {
    vi.spyOn(outagePolicyApi, "list").mockRejectedValue(new ApiError(403, "no"));
    vi.spyOn(outagePolicyApi, "summary").mockResolvedValue(SUMMARY);
    render(await OutagePolicyPage());
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(/cannot be shown to your role/);
    expect(alert).toHaveTextContent("admin:outage");
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("error: says what failed and what to do, without the raw error", async () => {
    vi.spyOn(outagePolicyApi, "list").mockRejectedValue(new Error("ECONNREFUSED 10.0.0.9"));
    vi.spyOn(outagePolicyApi, "summary").mockRejectedValue(new Error("down"));
    render(await OutagePolicyPage());
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(/could not be shown/);
    expect(alert).toHaveTextContent(/reload the page/i);
    expect(alert).not.toHaveTextContent("ECONNREFUSED");
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("a missing fleet summary keeps the policy, says why, and shows buckets only", async () => {
    vi.spyOn(outagePolicyApi, "list").mockResolvedValue([row({ silence_grace_buckets: 6 })]);
    vi.spyOn(outagePolicyApi, "summary").mockRejectedValue(new ApiError(403, "no"));
    render(await OutagePolicyPage());
    expect(screen.getByText(/read:fleet/)).toBeInTheDocument();
    const fw = classRowFor("Firewall");
    expect(within(fw).getByText("6 buckets")).toBeInTheDocument();
    expect(within(fw).queryByText(/min$/)).toBeNull();
    // The override count was not measured: an em dash, not zero.
    expect(within(fw).getByLabelText("Not available")).toBeInTheDocument();
  });

  it("has a loading state that names what is loading", () => {
    render(<Loading />);
    expect(screen.getByRole("status")).toHaveTextContent(/Loading the outage alert policy/);
  });
});
