import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { StaticSession, type SessionValue } from "@/components/auth/session";
import { ApiError, api, type CallerInfo, type NoisePreview } from "@/lib/api";

import { NoiseTuning } from "./NoiseTuning";
import {
  INSUFFICIENT_PREVIEW,
  PARTIAL_PREVIEW,
  SRC,
  TOO_LARGE_PREVIEW,
  current,
  detail,
  preview,
} from "./noiseFixtures";

const refresh = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), refresh }) }));

function session(permissions: string[], status: SessionValue["status"] = "ready"): SessionValue {
  const me: CallerInfo = {
    subject: "analyst",
    email: null,
    permissions,
    effective_permissions: permissions,
  } as CallerInfo;
  return {
    status,
    me: status === "ready" ? me : null,
    error: null,
    reload: async () => {},
    signOut: async () => null,
  };
}

const WRITER = session(["read:anomalies", "log_source:write"]);
const READER = session(["read:anomalies"]);

function renderTuning(over: Parameters<typeof current>[0] = {}, value: SessionValue = WRITER) {
  return render(
    <StaticSession value={value}>
      <NoiseTuning logSourceId={SRC} initial={current(over)} />
    </StaticSession>,
  );
}

const input = (name: RegExp) => screen.getByRole("spinbutton", { name }) as HTMLInputElement;
const previewButton = () => screen.getByRole("button", { name: /^Preview \d+ days$/ });
const saveButton = () => screen.getByRole("button", { name: "Save" });

async function runPreview(result: NoisePreview = preview()) {
  vi.spyOn(api, "noisePreview").mockResolvedValue(result);
  fireEvent.click(previewButton());
  await screen.findByRole("heading", { name: "Replay result" });
}

beforeEach(() => {
  vi.restoreAllMocks();
  refresh.mockClear();
});

describe("the tuning form", () => {
  it("is filled from the values in force and says where each one comes from", () => {
    renderTuning({
      criticality: "HIGH",
      effective_thresholds: {
        ...current().effective_thresholds,
        spike_ratio: { value: 3, source: "criticality" },
        open_after: { value: 1, source: "lab_mode" },
      },
    });
    expect(input(/^Deviation$/).value).toBe("3.5");
    expect(input(/^Spike ratio$/).value).toBe("3");
    expect(input(/^Open after$/).value).toBe("1");
    expect(screen.getByText("from criticality High")).toBeInTheDocument();
    expect(screen.getByText("LAB_MODE")).toBeInTheDocument();
    expect(screen.getAllByText("default").length).toBeGreaterThan(0);
    expect(screen.getByRole("combobox", { name: "Direction" })).toHaveValue("both");
    expect(screen.getByRole("combobox", { name: "Criticality" })).toHaveValue("HIGH");
  });

  it("marks a custom value as custom", () => {
    renderTuning();
    expect(screen.getByText("custom")).toBeInTheDocument();
  });

  it("uses the backend's bounds on every number control, with units", () => {
    renderTuning();
    const bounds: [RegExp, string, string][] = [
      [/^Deviation$/, "0.5", "20"],
      [/^Spike ratio$/, "1.01", "100"],
      [/^Drop ratio$/, "0.01", "0.99"],
      [/^Minimum change$/, "0", "1000000"],
      [/^Open after$/, "1", "10"],
    ];
    for (const [name, min, max] of bounds) {
      expect(input(name)).toHaveAttribute("min", min);
      expect(input(name)).toHaveAttribute("max", max);
    }
    expect(screen.getByText("events / bucket")).toBeInTheDocument();
    expect(screen.getByText("buckets")).toBeInTheDocument();
    expect(screen.getAllByText("× expected").length).toBe(2);
    expect(screen.getAllByRole("option", { name: /Spikes only|Drops only|Both/ })).toHaveLength(3);
  });

  it("keeps a stored value that is not a number out of the field and says so", () => {
    renderTuning({
      effective_thresholds: {
        ...current().effective_thresholds,
        deviation_z: { value: "high", source: "custom" },
      },
    });
    expect(input(/^Deviation$/).value).toBe("");
    expect(screen.getByText(/stored value "high" is not usable/)).toBeInTheDocument();
    expect(previewButton()).toBeEnabled();
  });

  it("refuses an impossible value before any request and names the bound", () => {
    const spy = vi.spyOn(api, "noisePreview");
    renderTuning();
    fireEvent.change(input(/^Drop ratio$/), { target: { value: "1.5" } });
    expect(screen.getByRole("alert")).toHaveTextContent("The value must be from 0.01 to 0.99.");
    expect(input(/^Drop ratio$/)).toHaveAttribute("aria-invalid", "true");
    expect(previewButton()).toBeDisabled();
    fireEvent.change(input(/^Open after$/), { target: { value: "2.5" } });
    expect(screen.getByText("Enter a whole number from 1 to 10.")).toBeInTheDocument();
    expect(spy).not.toHaveBeenCalled();
  });

  it("says what the page can and cannot do in LAB_MODE and with per-type overrides", () => {
    renderTuning({ lab_mode: true, has_by_type: true });
    expect(screen.getByText("In LAB_MODE criticality changes no detection threshold.")).toBeInTheDocument();
    expect(screen.getByText(/per-type overrides/)).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Criticality" })).toBeEnabled();
  });

  it("says nothing about LAB_MODE or per-type overrides when they do not apply", () => {
    renderTuning();
    expect(screen.queryByText(/LAB_MODE criticality/)).toBeNull();
    expect(screen.queryByText(/per-type overrides/)).toBeNull();
  });

  it("warns that a narrower direction closes open incidents, only when it does", () => {
    renderTuning();
    expect(screen.queryByText(/closes this source's open incidents/)).toBeNull();
    fireEvent.change(screen.getByRole("combobox", { name: "Direction" }), { target: { value: "spike" } });
    expect(screen.getByText(/closes this source's open incidents of the disabled direction/)).toBeInTheDocument();
    fireEvent.change(screen.getByRole("combobox", { name: "Direction" }), { target: { value: "both" } });
    expect(screen.queryByText(/closes this source's open incidents/)).toBeNull();
  });

  it("no longer claims a criticality change is not replayed", () => {
    renderTuning();
    fireEvent.change(screen.getByRole("combobox", { name: "Criticality" }), { target: { value: "LOW" } });
    expect(screen.queryByText(/not replayed/)).toBeNull();
  });
});

describe("the preview and criticality", () => {
  const choose = (v: string) =>
    fireEvent.change(screen.getByRole("combobox", { name: "Criticality" }), { target: { value: v } });

  it("omits criticality when it equals the stored value", async () => {
    renderTuning();
    const spy = vi.spyOn(api, "noisePreview").mockResolvedValue(preview());
    fireEvent.change(input(/^Deviation$/), { target: { value: "4.5" } });
    fireEvent.click(previewButton());
    await screen.findByRole("heading", { name: "Replay result" });
    expect(spy.mock.calls[0][1]).not.toHaveProperty("criticality");
  });

  it("sends criticality when it changed and states what the replay used", async () => {
    renderTuning();
    const spy = vi.spyOn(api, "noisePreview").mockResolvedValue(
      preview({ effective: { criticality: "LOW", lab_mode: false } }),
    );
    choose("LOW");
    fireEvent.click(previewButton());
    await screen.findByRole("heading", { name: "Replay result" });
    expect(spy.mock.calls[0][1]).toMatchObject({ criticality: "LOW" });
    expect(screen.getByText(/^Replayed with criticality Low \(stored: High\)\.$/)).toBeInTheDocument();
    expect(saveButton()).toBeEnabled();
  });

  it("does not claim an effect under LAB_MODE", async () => {
    renderTuning({ lab_mode: true });
    vi.spyOn(api, "noisePreview").mockResolvedValue(
      preview({ effective: { criticality: "LOW", lab_mode: true } }),
    );
    choose("LOW");
    fireEvent.click(previewButton());
    await screen.findByRole("heading", { name: "Replay result" });
    expect(screen.queryByText(/Replayed with criticality/)).toBeNull();
    expect(screen.getByText(/counts do not depend on the criticality change/)).toBeInTheDocument();
  });

  it("says nothing about criticality when the answer has no effective block", async () => {
    renderTuning();
    vi.spyOn(api, "noisePreview").mockResolvedValue(preview());
    choose("LOW");
    fireEvent.click(previewButton());
    await screen.findByRole("heading", { name: "Replay result" });
    expect(screen.queryByText(/Replayed with criticality/)).toBeNull();
    expect(screen.queryByText(/do not depend on the criticality/)).toBeNull();
  });

  it("keeps Save off until a preview of exactly these values exists", async () => {
    renderTuning();
    await runPreview(preview({ effective: { criticality: "HIGH", lab_mode: false } }));
    expect(saveButton()).toBeDisabled();
    choose("LOW");
    expect(saveButton()).toBeDisabled();
    await runPreview(preview({ effective: { criticality: "LOW", lab_mode: false } }));
    expect(saveButton()).toBeEnabled();
    choose("MEDIUM");
    expect(saveButton()).toBeDisabled();
  });
});

describe("the preview", () => {
  it("sends only the knobs that differ and the chosen window", async () => {
    renderTuning();
    const spy = vi.spyOn(api, "noisePreview").mockResolvedValue(preview());
    fireEvent.change(input(/^Deviation$/), { target: { value: "4.5" } });
    fireEvent.change(screen.getByRole("combobox", { name: "Direction" }), { target: { value: "spike" } });
    fireEvent.click(screen.getByRole("radio", { name: "7 days" }));
    fireEvent.click(screen.getByRole("button", { name: "Preview 7 days" }));
    await screen.findByRole("heading", { name: "Replay result" });
    expect(spy).toHaveBeenCalledWith(SRC, {
      window_days: 7,
      thresholds: { deviation_z: 4.5, direction: "spike" },
    });
  });

  it("shows current M against proposed N, a total, the coverage line and the one M/N sentence", async () => {
    renderTuning();
    await runPreview();
    const table = screen.getByRole("table", { name: /Incidents the replay would open/ });
    expect(within(table).getByRole("columnheader", { name: /Current values/ })).toHaveTextContent("M");
    expect(within(table).getByRole("columnheader", { name: /Proposed values/ })).toHaveTextContent("N");
    const total = within(table).getByRole("row", { name: /All detectors/ });
    expect(total).toHaveTextContent("39");
    expect(total).toHaveTextContent("5");
    expect(total).toHaveTextContent("−34");
    expect(screen.getByText(/Replay of 28 days of stored buckets/)).toHaveTextContent("28 of 28 days are stored");
    expect(screen.getAllByText(/not a forecast/)).toHaveLength(1);
    expect(screen.getByText(/Baseline version 42/)).toBeInTheDocument();
  });

  it("writes 'not run (direction)' for an excluded detector and never a zero or a bare dash", async () => {
    renderTuning();
    await runPreview();
    const row = screen.getByRole("row", { name: /Volume drop/ });
    const cells = within(row).getAllByRole("cell");
    expect(cells[0]).toHaveTextContent("9");
    expect(cells[1]).toHaveTextContent("not run (direction)");
    expect(cells[1].textContent).not.toMatch(/\b0\b/);
    expect(cells[2].textContent).not.toMatch(/[0-9]/);
    // A detector that ran and found nothing is a real zero.
    expect(within(screen.getByRole("row", { name: /No events/ })).getAllByRole("cell")[1]).toHaveTextContent("0");
  });

  it("keeps the recorded history on its own labelled line, not as M", async () => {
    renderTuning();
    await runPreview();
    const line = screen.getByText(/Recorded in the same window/);
    expect(line).toHaveTextContent("41 incidents, 12 opened, 0 suppressed");
    expect(line).toHaveTextContent("not a replay count");
    expect(within(screen.getByRole("table", { name: /Incidents the replay/ })).queryByText("41")).toBeNull();
  });

  it("lists the detectors that cannot be replayed with their reasons, without the direction duplicate", async () => {
    renderTuning();
    await runPreview();
    expect(screen.getByRole("heading", { name: "Not replayed" })).toBeInTheDocument();
    expect(screen.getByText(/did not measure PARSE_RATIOS/)).toBeInTheDocument();
    expect(screen.queryByText(/current direction disables/)).toBeNull();
  });

  it("labels a PARTIAL result with the span it covers", async () => {
    renderTuning();
    await runPreview(PARTIAL_PREVIEW);
    expect(screen.getByText("Partial window.")).toBeInTheDocument();
    expect(screen.getByText(/Counts cover 14 of 28 days\./)).toBeInTheDocument();
    expect(screen.getByText(/14 of 28 days are stored/)).toBeInTheDocument();
    expect(screen.getByRole("table", { name: /Incidents the replay/ })).toBeInTheDocument();
  });

  it("shows INSUFFICIENT_DATA with its reason and no number from the replay", async () => {
    renderTuning();
    await runPreview(INSUFFICIENT_PREVIEW);
    expect(screen.getByText("Only 3 of 28 days are stored; a replay needs at least 7.")).toBeInTheDocument();
    expect(screen.queryByRole("table", { name: /Incidents the replay/ })).toBeNull();
    expect(screen.queryByText("All detectors")).toBeNull();
    expect(screen.queryByText(/not a forecast/)).toBeNull();
    expect(screen.getByText(/Recorded in the same window: 5 incidents/)).toBeInTheDocument();
  });

  it("shows a maintenance refusal as the backend words it", async () => {
    renderTuning();
    await runPreview(
      preview({
        ...INSUFFICIENT_PREVIEW,
        reason: {
          code: "noise.preview.maintenance",
          params: {},
          text: "The source is in maintenance; the current rule would not judge these buckets.",
        },
      }),
    );
    expect(screen.getByText(/The source is in maintenance/)).toBeInTheDocument();
    expect(screen.queryByRole("table", { name: /Incidents the replay/ })).toBeNull();
  });

  it("shows TOO_LARGE with no count and offers 7 days, which runs the replay", async () => {
    renderTuning();
    const spy = vi.spyOn(api, "noisePreview").mockResolvedValueOnce(TOO_LARGE_PREVIEW);
    fireEvent.click(previewButton());
    await screen.findByText("This window is too large to replay");
    expect(screen.queryByRole("table", { name: /Incidents the replay/ })).toBeNull();
    expect(screen.queryByText(/Recorded in the same window/)).toBeNull();
    spy.mockResolvedValueOnce(preview({ window: { ...preview().window, days: 7 } }));
    fireEvent.click(screen.getByRole("button", { name: "Use 7 days" }));
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2));
    expect(spy).toHaveBeenLastCalledWith(SRC, { window_days: 7, thresholds: {} });
    await screen.findByRole("table", { name: /Incidents the replay/ });
    expect(screen.getByRole("radio", { name: "7 days" })).toBeChecked();
  });

  it("surfaces the maintenance and unjudged counts only when they are above zero", async () => {
    renderTuning();
    await runPreview(preview({ not_judged_buckets: 3, maintenance_buckets: 2 }));
    expect(screen.getByText(/3 buckets were not judged/)).toBeInTheDocument();
    expect(screen.getByText(/2 buckets were recorded under a maintenance window/)).toBeInTheDocument();
  });

  it("prints a backend warning", async () => {
    renderTuning();
    await runPreview(
      preview({
        warnings: [{ code: "noise.preview.maintenance_history", params: {}, text: "Over 10 % of buckets were suppressed." }],
      }),
    );
    expect(screen.getByText("Over 10 % of buckets were suppressed.")).toBeInTheDocument();
  });

  it("says what failed and that nothing changed when the replay cannot run", async () => {
    renderTuning();
    vi.spyOn(api, "noisePreview").mockRejectedValue(new ApiError(500, "boom"));
    fireEvent.click(previewButton());
    expect(await screen.findByRole("alert")).toHaveTextContent(/could not run, and nothing was changed/);
    expect(screen.queryByRole("heading", { name: "Replay result" })).toBeNull();
  });

  it("names read:anomalies on a 403 from the replay", async () => {
    renderTuning();
    vi.spyOn(api, "noisePreview").mockRejectedValue(new ApiError(403, "forbidden"));
    fireEvent.click(previewButton());
    expect(await screen.findByRole("alert")).toHaveTextContent("read:anomalies");
  });

  it("puts a 422 from the replay next to its field", async () => {
    renderTuning();
    vi.spyOn(api, "noisePreview").mockRejectedValue(
      new ApiError(422, "x", [
        { loc: ["body", "thresholds"], msg: "Value error, custom_thresholds.spike_ratio: Input should be greater than or equal to 1.01" },
      ]),
    );
    fireEvent.click(previewButton());
    await waitFor(() => expect(screen.getByText(/Input should be greater than or equal to 1\.01/)).toBeInTheDocument());
    expect(document.getElementById(input(/^Spike ratio$/).getAttribute("aria-describedby")!.split(" ")[1])).toHaveTextContent(
      "greater than or equal to 1.01",
    );
  });
});

describe("a preview that no longer matches the form", () => {
  it("dims, says the values changed, and takes Save away", async () => {
    renderTuning();
    await runPreview();
    fireEvent.change(input(/^Deviation$/), { target: { value: "5" } });
    expect(screen.getByText(/Values changed\. Preview again before saving/)).toBeInTheDocument();
    expect(document.querySelector(".noise-result.stale")).not.toBeNull();
    expect(saveButton()).toBeDisabled();
  });

  it("goes stale on a window change and on a criticality change, and recovers by running again", async () => {
    renderTuning();
    await runPreview();
    fireEvent.change(input(/^Deviation$/), { target: { value: "5" } });
    await runPreview();
    expect(screen.queryByText(/Values changed/)).toBeNull();
    expect(saveButton()).toBeEnabled();
    fireEvent.click(screen.getByRole("radio", { name: "7 days" }));
    expect(screen.getByText(/Values changed/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: "28 days" }));
    expect(screen.queryByText(/Values changed/)).toBeNull();
    fireEvent.change(screen.getByRole("combobox", { name: "Criticality" }), { target: { value: "LOW" } });
    expect(screen.getByText(/Values changed/)).toBeInTheDocument();
  });

  it("ignores a late answer for values the form has already left", async () => {
    renderTuning();
    let release: (p: NoisePreview) => void = () => {};
    vi.spyOn(api, "noisePreview").mockImplementation(() => new Promise((r) => (release = r)));
    fireEvent.change(input(/^Deviation$/), { target: { value: "5" } });
    fireEvent.click(previewButton());
    fireEvent.change(input(/^Deviation$/), { target: { value: "6" } });
    release(preview());
    await screen.findByText(/Values changed\. Preview again before saving/);
    expect(saveButton()).toBeDisabled();
  });
});

describe("Save", () => {
  it("is off until a preview ran for exactly these values, and says why", async () => {
    renderTuning();
    expect(saveButton()).toBeDisabled();
    expect(screen.getByText("Nothing differs from the saved values.")).toBeInTheDocument();
    fireEvent.change(input(/^Deviation$/), { target: { value: "5" } });
    expect(saveButton()).toBeDisabled();
    expect(screen.getByText("Run a preview for these values to enable Save.")).toBeInTheDocument();
    await runPreview();
    expect(saveButton()).toBeEnabled();
  });

  it("stays off when nothing differs, even after a preview", async () => {
    renderTuning();
    await runPreview();
    expect(saveButton()).toBeDisabled();
  });

  it("is off for an account without log_source:write, names the permission, and Preview still works", async () => {
    renderTuning({}, READER);
    expect(screen.getByText(/saving needs the log_source:write permission/)).toBeInTheDocument();
    fireEvent.change(input(/^Deviation$/), { target: { value: "5" } });
    await runPreview();
    expect(saveButton()).toBeDisabled();
  });

  it("does not claim a missing permission while the session is still loading", () => {
    renderTuning({}, session([], "loading"));
    expect(screen.queryByText(/log_source:write/)).toBeNull();
    expect(saveButton()).toBeDisabled();
  });

  it("accepts admin:* as a writer", async () => {
    renderTuning({}, session(["admin:*"]));
    fireEvent.change(input(/^Deviation$/), { target: { value: "5" } });
    await runPreview();
    expect(saveButton()).toBeEnabled();
  });

  it("sends the changed knobs over the stored custom ones, with the preview the analyst saw", async () => {
    const reread = detail({
      current: current({
        custom_thresholds: { open_after: 4, deviation_z: 4.5, direction: "spike" },
        effective_thresholds: {
          ...current().effective_thresholds,
          deviation_z: { value: 4.5, source: "custom" },
          direction: { value: "spike", source: "custom" },
        },
      }),
    });
    const patch = vi.spyOn(api, "tuneLogSource").mockResolvedValue({} as never);
    const get = vi.spyOn(api, "noiseSource").mockResolvedValue(reread);
    renderTuning({ custom_thresholds: { open_after: 4, by_type: { VOLUME_SPIKE: { deviation_z: 9 } } } });
    fireEvent.change(input(/^Deviation$/), { target: { value: "4.5" } });
    fireEvent.change(screen.getByRole("combobox", { name: "Direction" }), { target: { value: "spike" } });
    await runPreview();
    fireEvent.click(saveButton());
    await screen.findByText("Saved.");
    expect(patch).toHaveBeenCalledTimes(1);
    const [id, body] = patch.mock.calls[0];
    expect(id).toBe(SRC);
    expect(body.custom_thresholds).toEqual({
      open_after: 4,
      by_type: { VOLUME_SPIKE: { deviation_z: 9 } },
      deviation_z: 4.5,
      direction: "spike",
    });
    expect(body).not.toHaveProperty("criticality");
    expect(body.preview).toMatchObject({ window: 28, current: 39, proposed: 5, state: "COMPLETE" });
    expect(typeof body.preview.window).toBe("number");
    expect(Number.isNaN(Date.parse(body.preview.at))).toBe(false);
    expect(get).toHaveBeenCalledWith(SRC);
    expect(refresh).toHaveBeenCalled();
  });

  it("sends criticality only when it moved, and a null count when the preview had none", async () => {
    const patch = vi.spyOn(api, "tuneLogSource").mockResolvedValue({} as never);
    vi.spyOn(api, "noiseSource").mockResolvedValue(detail());
    renderTuning();
    fireEvent.change(screen.getByRole("combobox", { name: "Criticality" }), { target: { value: "LOW" } });
    await runPreview(INSUFFICIENT_PREVIEW);
    fireEvent.click(saveButton());
    await screen.findByText("Saved.");
    const body = patch.mock.calls[0][1];
    expect(body.criticality).toBe("LOW");
    expect(body.custom_thresholds).toEqual({ open_after: 4 });
    expect(body.preview).toMatchObject({ current: null, proposed: null, state: "INSUFFICIENT_DATA" });
  });

  it("says what was saved, reloads the values as custom and drops the old preview", async () => {
    vi.spyOn(api, "tuneLogSource").mockResolvedValue({} as never);
    vi.spyOn(api, "noiseSource").mockResolvedValue(
      detail({
        current: current({
          effective_thresholds: {
            ...current().effective_thresholds,
            deviation_z: { value: 4.5, source: "custom" },
          },
        }),
      }),
    );
    renderTuning();
    fireEvent.change(input(/^Deviation$/), { target: { value: "4.5" } });
    await runPreview();
    fireEvent.click(saveButton());
    const banner = await screen.findByText("Saved.");
    expect(banner.closest(".banner")).toHaveTextContent("deviation 3.5 to 4.5");
    expect(banner.closest(".banner")).toHaveTextContent("The audit log keeps the preview you saw.");
    expect(banner.closest(".banner")).not.toHaveTextContent("closed");
    expect(screen.queryByRole("heading", { name: "Replay result" })).toBeNull();
    expect(input(/^Deviation$/).value).toBe("4.5");
    expect(screen.getAllByText("custom")).toHaveLength(2);
  });

  it("says open incidents of the disabled direction were closed, without inventing a number", async () => {
    vi.spyOn(api, "tuneLogSource").mockResolvedValue({} as never);
    vi.spyOn(api, "noiseSource").mockResolvedValue(detail());
    renderTuning();
    fireEvent.change(screen.getByRole("combobox", { name: "Direction" }), { target: { value: "drop" } });
    await runPreview();
    fireEvent.click(saveButton());
    const banner = (await screen.findByText("Saved.")).closest(".banner")!;
    expect(banner).toHaveTextContent("Open incidents of the disabled direction were closed.");
    expect(banner.textContent).not.toMatch(/\d+ (open )?incidents/);
  });

  it("still reports the save when the source cannot be re-read", async () => {
    vi.spyOn(api, "tuneLogSource").mockResolvedValue({} as never);
    vi.spyOn(api, "noiseSource").mockRejectedValue(new ApiError(500, "x"));
    renderTuning();
    fireEvent.change(input(/^Deviation$/), { target: { value: "4.5" } });
    await runPreview();
    fireEvent.click(saveButton());
    const banner = (await screen.findByText("Saved.")).closest(".banner")!;
    expect(banner).toHaveTextContent("could not re-read the source");
  });

  it("puts a 422 next to the field it names and keeps the preview", async () => {
    vi.spyOn(api, "tuneLogSource").mockRejectedValue(
      new ApiError(422, "x", [
        { loc: ["body", "custom_thresholds"], msg: "Value error, custom_thresholds.deviation_z: Input should be less than or equal to 20" },
      ]),
    );
    renderTuning();
    fireEvent.change(input(/^Deviation$/), { target: { value: "5" } });
    await runPreview();
    fireEvent.click(saveButton());
    await waitFor(() => expect(screen.getByText("Input should be less than or equal to 20")).toBeInTheDocument());
    expect(input(/^Deviation$/)).toHaveAttribute("aria-invalid", "true");
    expect(screen.queryByText("Saved.")).toBeNull();
    expect(screen.getByRole("heading", { name: "Replay result" })).toBeInTheDocument();
  });

  it("says a general 422 and a 403 in words, and that nothing changed", async () => {
    const patch = vi.spyOn(api, "tuneLogSource").mockRejectedValueOnce(new ApiError(422, "x", "criticality is locked"));
    renderTuning();
    fireEvent.change(input(/^Deviation$/), { target: { value: "5" } });
    await runPreview();
    fireEvent.click(saveButton());
    expect(await screen.findByText(/criticality is locked/)).toBeInTheDocument();
    patch.mockRejectedValueOnce(new ApiError(403, "no"));
    fireEvent.click(saveButton());
    expect(await screen.findByText(/Saving needs the log_source:write permission\. Nothing was changed/)).toBeInTheDocument();
    patch.mockRejectedValueOnce(new ApiError(500, "no"));
    fireEvent.click(saveButton());
    expect(await screen.findByText("The values were not saved and nothing was changed.")).toBeInTheDocument();
  });
});
