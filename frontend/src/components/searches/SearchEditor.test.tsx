import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SearchEditor } from "./SearchEditor";
import { SEARCH_LATENCY_STATEMENT } from "@/components/searches/authoring";
import { ApiError, api, type ScheduledSearch } from "@/lib/api";

const push = vi.fn();
const refresh = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push, refresh }) }));

function search(over: Partial<ScheduledSearch> = {}): ScheduledSearch {
  return {
    id: "s-1",
    name: "Repeated authentication failures",
    description: "Counts failures per source",
    owner: "SOC",
    category: "Credential Access",
    mitre_techniques: ["T1110"],
    aql_query: "SELECT count(*) FROM events WHERE username IS NOT NULL",
    query_version: 2,
    schedule_cron: "17 3 * * 1",
    schedule_timezone: "UTC",
    severity: "HIGH",
    threshold_value: 10,
    threshold_operator: "GT",
    timeout_seconds: 300,
    max_time_range_hours: 24,
    max_result_rows: 500,
    visualization_type: "PIE",
    enabled: false,
    last_run_at: null,
    next_run_at: null,
    consecutive_failures: 0,
    ...over,
  };
}

beforeEach(() => {
  push.mockClear();
  refresh.mockClear();
  vi.restoreAllMocks();
});

async function fillNewSearch(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByRole("textbox", { name: "Name" }), "Nightly count");
  await user.type(screen.getByRole("textbox", { name: "AQL" }), "SELECT COUNT(*) FROM events");
}

describe("new search editor", () => {
  it("states the latency once and offers the seven presets", () => {
    render(<SearchEditor mode="create" />);
    expect(screen.getAllByText(SEARCH_LATENCY_STATEMENT)).toHaveLength(1);
    expect(screen.queryByText(/Current:/)).toBeNull();
    expect(screen.getByText(/Default:/)).toHaveTextContent("Every 5 min");
    const schedule = screen.getByRole("combobox", { name: "Schedule" });
    expect(within(schedule).getAllByRole("option").map((option) => option.textContent)).toEqual([
      "Every 5 min",
      "Every 15 min",
      "Every 30 min",
      "Every 1 h",
      "Every 2 h",
      "Every 4 h",
      "Every 8 h",
    ]);
    expect((schedule as HTMLSelectElement).value).toBe("*/5 * * * *");
  });

  it("saves a disabled definition and does not run the unsaved text", async () => {
    const user = userEvent.setup();
    const create = vi.spyOn(api, "createSearch").mockResolvedValue(search({ id: "new-1", enabled: false }));
    const run = vi.spyOn(api, "runSearch");
    render(<SearchEditor mode="create" />);
    await fillNewSearch(user);
    await user.click(screen.getByRole("button", { name: "Save as disabled" }));

    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    const body = create.mock.calls[0][0];
    expect(body.enabled).toBe(false);
    expect(body.name).toBe("Nightly count");
    expect(body.aql_query).toBe("SELECT COUNT(*) FROM events");
    expect(body.schedule_cron).toBe("*/5 * * * *");
    expect(body.threshold_value).toBeNull();
    expect(body.visualization_type).toBe("TABLE");
    expect(body).not.toHaveProperty("owner");
    expect(push).toHaveBeenCalledWith("/searches/new-1");
    expect(run).not.toHaveBeenCalled();
  });

  it("refuses a blank query and a timeout above the platform cap before any request", async () => {
    const user = userEvent.setup();
    const create = vi.spyOn(api, "createSearch");
    render(<SearchEditor mode="create" />);
    await user.type(screen.getByRole("textbox", { name: "Name" }), "Nightly count");
    await user.click(screen.getByRole("button", { name: "Save as disabled" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("AQL is required.");
    expect(create).not.toHaveBeenCalled();

    await user.type(screen.getByRole("textbox", { name: "AQL" }), "SELECT 1");
    const timeout = screen.getByRole("textbox", { name: /Timeout/ });
    await user.clear(timeout);
    await user.type(timeout, "901");
    await user.click(screen.getByRole("button", { name: "Save as disabled" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Enter a whole number of seconds, from 1 to 900.",
    );
    expect(create).not.toHaveBeenCalled();
  });

  it("lists each 422 reason under the query, verbatim", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "createSearch").mockRejectedValue(
      new ApiError(422, "Unprocessable Entity", {
        message: "unsafe AQL: first reason, as sent; second reason, as sent",
        reasons: ["first reason, as sent", "second reason, as sent"],
      }),
    );
    render(<SearchEditor mode="create" />);
    await fillNewSearch(user);
    await user.click(screen.getByRole("button", { name: "Save as disabled" }));

    const list = await screen.findByRole("list", { name: "Validation reasons" });
    const items = within(list).getAllByRole("listitem").map((item) => item.textContent);
    expect(items).toEqual(["first reason, as sent", "second reason, as sent"]);
    expect(screen.queryByText(/unsafe AQL:/)).toBeNull();
  });

  it("puts the cron-floor refusal beside the schedule", async () => {
    const user = userEvent.setup();
    const floor =
      "schedule_cron '*/1 * * * *' (UTC) fires 60 seconds apart (a then b); a search must fire at least 300 seconds apart";
    vi.spyOn(api, "createSearch").mockRejectedValue(new ApiError(422, floor, floor));
    render(<SearchEditor mode="create" />);
    await fillNewSearch(user);
    await user.selectOptions(screen.getByRole("combobox", { name: "Schedule" }), "*/5 * * * *");
    await user.click(screen.getByRole("button", { name: "Save as disabled" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(floor);
    expect(screen.getByRole("combobox", { name: "Schedule" })).toHaveAttribute("aria-invalid", "true");
    expect(screen.queryByRole("list", { name: "Validation reasons" })).toBeNull();
  });

  it("names the enabled-search limit and what to do on a 409", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "createSearch").mockRejectedValue(
      new ApiError(409, "the limit is 25 enabled searches", "the limit is 25 enabled searches"),
    );
    render(<SearchEditor mode="create" />);
    await fillNewSearch(user);
    await user.click(screen.getByRole("button", { name: "Save as disabled" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(
      "Not enabled: the limit is 25 enabled searches. Disable another search, then enable this one.",
    );
  });
});

describe("edit search editor", () => {
  it("sends a custom cron back verbatim after leaving the preset and returning", async () => {
    const user = userEvent.setup();
    const update = vi.spyOn(api, "updateSearch").mockImplementation(async (_id, body) =>
      search({ schedule_cron: body.schedule_cron, aql_query: body.aql_query }),
    );
    render(<SearchEditor mode="edit" search={search()} />);

    expect(screen.getAllByText("17 3 * * 1").length).toBeGreaterThan(0);
    expect(screen.getByText(/is kept as stored/)).toBeInTheDocument();
    const schedule = screen.getByRole("combobox", { name: "Schedule" });
    expect((schedule as HTMLSelectElement).value).toBe("custom");

    await user.selectOptions(schedule, "0 * * * *");
    expect((schedule as HTMLSelectElement).value).toBe("0 * * * *");
    expect(screen.getByText(/Current:/)).toHaveTextContent("Every 1 h");
    await user.selectOptions(schedule, "custom");
    expect((schedule as HTMLSelectElement).value).toBe("custom");
    expect(screen.getByText(/is kept as stored/)).toHaveTextContent("17 3 * * 1");

    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(update).toHaveBeenCalledTimes(1));
    const body = update.mock.calls[0][1];
    expect(update.mock.calls[0][0]).toBe("s-1");
    expect(body.schedule_cron).toBe("17 3 * * 1");
    expect(body.visualization_type).toBe("PIE");
    expect(body).not.toHaveProperty("enabled");
    expect(body).not.toHaveProperty("name");
    expect(screen.getByText(/Stored as PIE/)).toBeInTheDocument();
  });

  it("says when a save minted a version and when the query text stayed the same", async () => {
    const user = userEvent.setup();
    const update = vi.spyOn(api, "updateSearch").mockResolvedValue(search({ query_version: 3 }));
    render(<SearchEditor mode="edit" search={search()} />);
    const aql = screen.getByRole("textbox", { name: "AQL" });
    await user.clear(aql);
    await user.type(aql, "SELECT 2");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByText("Saved as v3. Run now executes this stored version.")).toBeInTheDocument();

    update.mockResolvedValue(search({ query_version: 2, aql_query: "SELECT 2" }));
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    expect(
      await screen.findByText("Saved. The query text is unchanged, so the version stayed the same."),
    ).toBeInTheDocument();
  });
});
