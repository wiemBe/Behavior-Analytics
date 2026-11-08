import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { StaticSession, type SessionValue } from "@/components/auth/session";
import { ApiError, suppressionApi } from "@/lib/api";
import type { SuppressTarget } from "@/lib/suppression";

import { SuppressButton } from "./SuppressButton";
import { row, session } from "./suppressionFixtures";

const refresh = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh, push: vi.fn() }) }));

const ALERT: SuppressTarget = { via: "alert", alertId: "a-1", family: "log_source", anomalyId: "n-1", alertDetector: null };
const ANOMALY: SuppressTarget = { via: "anomaly", family: "waf", anomalyId: "n-2" };
const PW: SuppressTarget = { via: "anomaly", family: "property_watch", anomalyId: "n-3" };
const CAN = session(["read:alerts", "alert:suppress"]);

function mount(
  target: SuppressTarget = ALERT,
  value: SessionValue = CAN,
  extra: {
    active?: ReturnType<typeof row> | null;
    onSaved?: () => void;
    sourceName?: string | null;
    mismatchWith?: string | null;
  } = {},
) {
  const onSaved = extra.onSaved ?? vi.fn();
  render(
    <StaticSession value={value}>
      <SuppressButton
        target={target}
        detectorLabel="Volume spike"
        sourceName={extra.sourceName === undefined ? "Linux audit fleet" : extra.sourceName}
        mismatchWith={extra.mismatchWith ?? null}
        active={extra.active ?? null}
        onSaved={onSaved}
      />
    </StaticSession>,
  );
  return onSaved;
}

async function open() {
  await userEvent.click(screen.getByRole("button", { name: "Don't alert this again" }));
}
const reasonBox = () => screen.getByLabelText("Reason");
const save = () => screen.getByRole("button", { name: "Quiet this condition" });

beforeEach(() => {
  vi.restoreAllMocks();
  refresh.mockClear();
});

describe("who sees it", () => {
  it("shows the action to a caller with alert:suppress", () => {
    mount();
    expect(screen.getByRole("button", { name: "Don't alert this again" })).toBeEnabled();
  });

  it("names the missing permission instead of the action", () => {
    mount(ALERT, session(["read:alerts"]));
    expect(screen.queryByRole("button", { name: "Don't alert this again" })).toBeNull();
    expect(screen.getByRole("note")).toHaveTextContent("alert:suppress");
  });

  it("says nothing while the session is still loading", () => {
    const { container } = render(
      <StaticSession value={session([], "loading")}>
        <SuppressButton
          target={ALERT}
          detectorLabel={null}
          sourceName={null}
          mismatchWith={null}
          active={null}
          onSaved={vi.fn()}
        />
      </StaticSession>,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("treats the administrator wildcard as the grant", () => {
    mount(ALERT, session(["admin:*"]));
    expect(screen.getByRole("button", { name: "Don't alert this again" })).toBeInTheDocument();
  });
});

describe("the form", () => {
  it("offers 1, 7 and 30 days only, 1 day first, and no way to quiet forever", async () => {
    mount();
    await open();
    const radios = screen.getAllByRole("radio");
    expect(radios.map((r) => r.parentElement?.textContent)).toEqual(["1 day", "7 days", "30 days"]);
    expect(radios[0]).toBeChecked();
    expect(document.body.textContent).not.toMatch(/forever|permanent/i);
  });

  it("counts the reason and starts the owner as the signed-in user", async () => {
    mount();
    await open();
    expect(screen.getByLabelText("Owner")).toHaveValue("k.osei");
    expect(document.body.textContent).toContain("0 of 1000 characters");
    await userEvent.type(reasonBox(), "firewall change");
    expect(document.body.textContent).toContain("15 of 1000 characters");
  });

  it("lets the analyst change the owner and keeps their edit", async () => {
    mount();
    await open();
    await userEvent.clear(screen.getByLabelText("Owner"));
    await userEvent.type(screen.getByLabelText("Owner"), "shift-lead");
    expect(screen.getByLabelText("Owner")).toHaveValue("shift-lead");
  });

  it("offers the whole source, off by default, for a source-anchored family only", async () => {
    mount(ALERT);
    await open();
    expect(screen.getByLabelText("Quiet the whole source (every detector)")).not.toBeChecked();
  });

  it("offers no whole-source choice for a property watch", async () => {
    mount(PW);
    await open();
    expect(screen.queryByLabelText(/whole source/)).toBeNull();
  });

  it("reveals a required reference for a planned change", async () => {
    mount();
    await open();
    expect(screen.queryByLabelText("Change or ticket reference")).toBeNull();
    await userEvent.click(screen.getByLabelText("This is a planned change"));
    expect(screen.getByLabelText("Change or ticket reference")).toBeInTheDocument();
  });

  it("states the effect before saving, following the choices", async () => {
    mount();
    await open();
    const effect = () => screen.getByRole("group", { name: "Before you save" }).textContent;
    expect(effect()).toContain("Alerts for Volume spike on Linux audit fleet are held back for 1 day.");
    await userEvent.click(screen.getByLabelText("7 days"));
    await userEvent.click(screen.getByLabelText("Quiet the whole source (every detector)"));
    expect(effect()).toContain("every detector of Linux audit fleet are held back for 7 days");
    expect(effect()).toContain("Incidents are still recorded");
  });

  it("names no source it does not know: the wording falls back instead of guessing", async () => {
    mount(ALERT, CAN, { sourceName: null });
    await open();
    const effect = () => screen.getByRole("group", { name: "Before you save" }).textContent;
    expect(effect()).toContain("Alerts for Volume spike on this source are held back");
    await userEvent.click(screen.getByLabelText("Quiet the whole source (every detector)"));
    expect(effect()).toContain("every detector of this source");
  });

  it("warns before saving when the alert's detector differs from the condition's", async () => {
    mount(ALERT, CAN, { mismatchWith: "Volume drop" });
    await open();
    const note = screen.getByRole("note");
    expect(note).toHaveTextContent("The alert is for Volume drop.");
    expect(note).toHaveTextContent("Volume spike on Linux audit fleet");
  });

  it("shows no warning when the detectors agree", async () => {
    mount();
    await open();
    expect(screen.queryByRole("note")).toBeNull();
  });

  it("closes on Cancel and returns focus to the action", async () => {
    mount();
    await open();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByLabelText("Reason")).toBeNull();
    expect(screen.getByRole("button", { name: "Don't alert this again" })).toHaveFocus();
  });

  it("says the condition is already quiet and that saving moves the end", async () => {
    mount(ALERT, CAN, { active: row() });
    await userEvent.click(screen.getByRole("button", { name: "Change the quiet period" }));
    expect(document.body.textContent).toContain("already quiet until 2026-11-13 07:00:00 UTC");
  });
});

describe("saving", () => {
  it("sends nothing and says what is missing when the reason is too short", async () => {
    const spy = vi.spyOn(suppressionApi, "fromAlert");
    mount();
    await open();
    await userEvent.type(reasonBox(), "ab");
    await userEvent.click(save());
    expect(spy).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("at least 3 characters");
    expect(reasonBox()).toHaveAttribute("aria-invalid", "true");
  });

  it("sends nothing without an owner", async () => {
    const spy = vi.spyOn(suppressionApi, "fromAlert");
    mount();
    await open();
    await userEvent.type(reasonBox(), "firewall change");
    await userEvent.clear(screen.getByLabelText("Owner"));
    await userEvent.click(save());
    expect(spy).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("Enter an owner");
  });

  it("sends nothing for a planned change without a reference", async () => {
    const spy = vi.spyOn(suppressionApi, "fromAlert");
    mount();
    await open();
    await userEvent.type(reasonBox(), "firewall change");
    await userEvent.click(screen.getByLabelText("This is a planned change"));
    await userEvent.click(save());
    expect(spy).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("reference");
  });

  it("posts an alert's request without a detector, then confirms in place", async () => {
    const spy = vi.spyOn(suppressionApi, "fromAlert").mockResolvedValue(row());
    const onSaved = mount();
    await open();
    await userEvent.type(reasonBox(), "  firewall change  ");
    await userEvent.click(screen.getByLabelText("7 days"));
    await userEvent.click(save());
    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
    expect(spy).toHaveBeenCalledWith("a-1", { days: 7, reason: "firewall change", owner: "k.osei" });
    expect(Object.keys(spy.mock.calls[0][1])).not.toContain("detector_type");
    expect(screen.getByRole("status")).toHaveTextContent(
      "Quiet until 2026-11-13 07:00:00 UTC. Incidents are still recorded.",
    );
    expect(screen.queryByLabelText("Reason")).toBeNull();
  });

  it("posts an anomaly's request to the anomaly route with the whole source and a planned change", async () => {
    const spy = vi.spyOn(suppressionApi, "fromAnomaly").mockResolvedValue(row({ detector_type: null }));
    mount(ANOMALY);
    await open();
    await userEvent.type(reasonBox(), "ticketed change");
    await userEvent.click(screen.getByLabelText("30 days"));
    await userEvent.click(screen.getByLabelText("Quiet the whole source (every detector)"));
    await userEvent.click(screen.getByLabelText("This is a planned change"));
    await userEvent.type(screen.getByLabelText("Change or ticket reference"), "CHG-2026-17");
    await userEvent.click(save());
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    expect(spy).toHaveBeenCalledWith("waf", "n-2", {
      days: 30,
      reason: "ticketed change",
      owner: "k.osei",
      whole_source: true,
      kind: "KNOWN_CHANGE",
      reference: "CHG-2026-17",
    });
  });

  it("never sends the whole source for a property watch", async () => {
    const spy = vi.spyOn(suppressionApi, "fromAnomaly").mockResolvedValue(row({ family: "property_watch" }));
    mount(PW);
    await open();
    await userEvent.type(reasonBox(), "known behaviour");
    await userEvent.click(save());
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    expect(spy.mock.calls[0][2]).not.toHaveProperty("whole_source");
  });

  it("does not send twice while a request is in flight", async () => {
    let finish: (v: ReturnType<typeof row>) => void = () => {};
    const spy = vi
      .spyOn(suppressionApi, "fromAlert")
      .mockReturnValue(new Promise((resolve) => (finish = resolve)));
    mount();
    await open();
    await userEvent.type(reasonBox(), "firewall change");
    await userEvent.click(save());
    expect(screen.getByRole("button", { name: "Saving" })).toBeDisabled();
    expect(spy).toHaveBeenCalledTimes(1);
    finish(row());
    await waitFor(() => expect(screen.queryByLabelText("Reason")).toBeNull());
  });

  it("puts a schema 422 next to its field and keeps the input", async () => {
    vi.spyOn(suppressionApi, "fromAlert").mockRejectedValue(
      new ApiError(422, "String should have at most 1000 characters", [
        { loc: ["body", "reason"], msg: "String should have at most 1000 characters" },
      ]),
    );
    mount();
    await open();
    await userEvent.type(reasonBox(), "firewall change");
    await userEvent.click(save());
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("at most 1000 characters");
    expect(reasonBox()).toHaveAttribute("aria-invalid", "true");
    expect(reasonBox()).toHaveValue("firewall change");
  });

  it("shows a handler 422 sentence in the form, nothing saved", async () => {
    vi.spyOn(suppressionApi, "fromAlert").mockRejectedValue(
      new ApiError(422, "property watch has no whole-source scope", "property watch has no whole-source scope"),
    );
    mount();
    await open();
    await userEvent.type(reasonBox(), "firewall change");
    await userEvent.click(save());
    expect(await screen.findByRole("alert")).toHaveTextContent("property watch has no whole-source scope");
    expect(reasonBox()).toBeInTheDocument();
  });

  it("says a vanished alert is gone, closes the form and refreshes the page", async () => {
    vi.spyOn(suppressionApi, "fromAlert").mockRejectedValue(new ApiError(404, "alert not found"));
    mount();
    await open();
    await userEvent.type(reasonBox(), "firewall change");
    await userEvent.click(save());
    expect(await screen.findByRole("alert")).toHaveTextContent("no longer exists");
    expect(screen.queryByLabelText("Reason")).toBeNull();
    await waitFor(() => expect(refresh).toHaveBeenCalledTimes(1));
  });

  it("names the permission on a 403 and keeps the form open", async () => {
    vi.spyOn(suppressionApi, "fromAlert").mockRejectedValue(
      new ApiError(403, "missing required permission: read:anomalies"),
    );
    mount();
    await open();
    await userEvent.type(reasonBox(), "firewall change");
    await userEvent.click(save());
    expect(await screen.findByRole("alert")).toHaveTextContent("read:anomalies");
    expect(reasonBox()).toBeInTheDocument();
  });

  it("says nothing was changed after a server error", async () => {
    vi.spyOn(suppressionApi, "fromAlert").mockRejectedValue(new ApiError(500, "boom"));
    mount();
    await open();
    await userEvent.type(reasonBox(), "firewall change");
    await userEvent.click(save());
    expect(await screen.findByRole("alert")).toHaveTextContent("Nothing was changed.");
  });
});
