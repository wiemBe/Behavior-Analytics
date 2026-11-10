import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { admission, ipsSource, latestBucket } from "@/components/ips/behaviorFixtures";
import { ApiError, api, type IpsSourceBehavior, type PagedIpsSources } from "@/lib/api";

import IpsBehaviorError from "./error";
import Loading from "./loading";
import IpsBehaviorPage from "./page";

function paged(items: IpsSourceBehavior[], total = items.length): PagedIpsSources {
  return { items, total, limit: 50, offset: 0 };
}

beforeEach(() => {
  vi.restoreAllMocks();
});

async function renderPage(
  items: IpsSourceBehavior[] = [ipsSource()],
  params: Record<string, string | undefined> = {},
  total = items.length,
) {
  const call = vi.spyOn(api, "ipsBehaviorSources").mockResolvedValue(paged(items, total));
  const view = render(await IpsBehaviorPage({ searchParams: Promise.resolve(params) }));
  return { ...view, call };
}

describe("IPS behavior: composition", () => {
  it("has one h1 and leads with how many sources are judged", async () => {
    const { container } = await renderPage([
      ipsSource(),
      ipsSource({ log_source_id: "b", log_source_name: "second", enabled: false }),
    ]);
    expect(container.querySelectorAll("h1")).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("IPS behavior");
    expect(screen.getByText("1 of 2")).toBeInTheDocument();
    expect(screen.getByText(/the others produce no verdicts/)).toBeInTheDocument();
    expect(screen.getByText("Times are UTC.")).toBeInTheDocument();
  });

  it("takes no time range: no range selector, and the request carries only paging", async () => {
    const { call } = await renderPage();
    expect(screen.queryByRole("navigation", { name: /range/i })).toBeNull();
    expect(call).toHaveBeenCalledWith({ limit: 50, offset: 0 });
  });

  it("lists sources that need attention first", async () => {
    await renderPage([
      ipsSource({ log_source_id: "a", log_source_name: "alpha-ok" }),
      ipsSource({ log_source_id: "b", log_source_name: "bravo-invalid", validation_status: "INVALID", validation_current: false }),
    ]);
    const links = screen.getAllByRole("link", { name: /alpha-ok|bravo-invalid/ }).map((a) => a.textContent);
    expect(links).toEqual(["bravo-invalid", "alpha-ok"]);
  });

  it("does not present a one-page judged count as a share of a longer list", async () => {
    const { container } = await renderPage(
      [ipsSource(), ipsSource({ log_source_id: "b", log_source_name: "second", enabled: false })],
      {},
      120,
    );
    const lead = container.querySelector(".ips-lead") as HTMLElement;
    expect(lead).toHaveTextContent("1 of 2 sources are listed on this page and collected and judged");
    expect(lead).toHaveTextContent("The list has 120 sources in all");
    expect(lead).not.toHaveTextContent("of 120");
  });

  it("keeps the plain wording when every source is on the page", async () => {
    const { container } = await renderPage();
    const lead = container.querySelector(".ips-lead") as HTMLElement;
    expect(lead).not.toHaveTextContent("listed on this page");
    expect(lead).not.toHaveTextContent("in all");
  });

  it("pages at 50 and keeps the offset in the link", async () => {
    const { call } = await renderPage([ipsSource()], { offset: "50" }, 120);
    expect(call).toHaveBeenCalledWith({ limit: 50, offset: 50 });
    expect(screen.getByRole("link", { name: "Next" }).getAttribute("href")).toContain("offset=100");
  });

  it("treats a bad offset as the first page", async () => {
    const { call } = await renderPage([ipsSource()], { offset: "-3" });
    expect(call).toHaveBeenCalledWith({ limit: 50, offset: 0 });
  });
});

describe("IPS behavior: source states", () => {
  it("does not count a profile valid for an older version as judged", async () => {
    await renderPage([ipsSource({ profile_version: 5, validated_profile_version: 4, validation_current: false })]);
    expect(screen.getByText("0 of 1")).toBeInTheDocument();
    expect(screen.getByText("STALE")).toBeInTheDocument();
    expect(screen.queryByText("VALID")).toBeNull();
  });

  it("extends the lead with the number of judged sources that have active anomalies", async () => {
    const { container } = await renderPage([
      ipsSource({ log_source_id: "a", log_source_name: "a", active_anomaly_count: 2, worst_active_severity: "HIGH" }),
      ipsSource({ log_source_id: "b", log_source_name: "b", active_anomaly_count: 1, worst_active_severity: "LOW" }),
      ipsSource({ log_source_id: "c", log_source_name: "c", active_anomaly_count: 0 }),
      ipsSource({ log_source_id: "d", log_source_name: "d", active_anomaly_count: null, validation_current: false }),
    ]);
    const lead = container.querySelector(".ips-lead") as HTMLElement;
    expect(lead).toHaveTextContent("3 of 4 sources are collected and judged");
    expect(lead).toHaveTextContent("2 judged sources have active anomalies.");
  });

  it("says nothing about active anomalies in the lead when no judged source has one", async () => {
    const { container } = await renderPage([
      ipsSource({ active_anomaly_count: 0 }),
      ipsSource({ log_source_id: "n", log_source_name: "n", active_anomaly_count: null, validation_current: false }),
    ]);
    expect(container.querySelector(".ips-lead")).not.toHaveTextContent("active anomalies");
  });

  it("lists the source with the worst active anomalies right after the sources that are not judged", async () => {
    await renderPage([
      ipsSource({ log_source_id: "q", log_source_name: "a-quiet", active_anomaly_count: 0 }),
      ipsSource({ log_source_id: "h", log_source_name: "z-busy", active_anomaly_count: 2, worst_active_severity: "HIGH" }),
    ]);
    const names = screen.getAllByRole("link", { name: /a-quiet|z-busy/ }).map((a) => a.textContent);
    expect(names).toEqual(["z-busy", "a-quiet"]);
  });

  it("does not count a disabled source as judged", async () => {
    await renderPage([ipsSource({ enabled: false })]);
    expect(screen.getByText("0 of 1")).toBeInTheDocument();
    expect(screen.getByText("Disabled")).toBeInTheDocument();
  });

  it("shows a source with no latest bucket as not collected yet, not as 0", async () => {
    await renderPage([ipsSource({ latest_bucket: null })]);
    const row = within(screen.getByRole("link", { name: "lab-ips-01" }).closest("tr")!);
    expect(row.getAllByText("Not collected yet").length).toBeGreaterThan(0);
    expect(row.queryByText(/events$/)).toBeNull();
  });

  it("calls out admission overflow of the latest bucket", async () => {
    await renderPage([
      ipsSource({
        latest_bucket: latestBucket({
          admission: [admission("DESTINATION_IP", { rejected_over_quota: 12, untracked_entities: 3 })],
        }),
      }),
    ]);
    expect(screen.getByText("Overflowing")).toBeInTheDocument();
    expect(screen.getByText("Destination: 12 refused over quota, 3 untracked entities")).toBeInTheDocument();
  });

  it("does not call out admission when nothing overflowed", async () => {
    await renderPage();
    expect(screen.queryByText("Overflowing")).toBeNull();
  });
});

describe("IPS behavior: empty and failed", () => {
  it("says what is empty and why, and that empty is not normal", async () => {
    await renderPage([]);
    expect(screen.getByRole("status")).toHaveTextContent(/No IPS source has a behavior profile/);
    expect(screen.getByRole("status")).toHaveTextContent(/not a statement that IPS traffic is normal/);
  });

  it("says what failed and what to do on an outage, without the exception text", async () => {
    vi.spyOn(api, "ipsBehaviorSources").mockRejectedValue(new Error("connect ECONNREFUSED 10.0.0.9:8000"));
    render(await IpsBehaviorPage({ searchParams: Promise.resolve({}) }));
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("IPS behavior could not be shown.");
    expect(alert).toHaveTextContent(/backend may be unreachable/);
    expect(alert).not.toHaveTextContent("ECONNREFUSED");
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("names the missing permission on 403", async () => {
    vi.spyOn(api, "ipsBehaviorSources").mockRejectedValue(new ApiError(403, "forbidden"));
    render(await IpsBehaviorPage({ searchParams: Promise.resolve({}) }));
    expect(screen.getByRole("alert")).toHaveTextContent(/read:anomalies/);
  });

  it("asks the analyst to sign in again on 401", async () => {
    vi.spyOn(api, "ipsBehaviorSources").mockRejectedValue(new ApiError(401, "expired"));
    render(await IpsBehaviorPage({ searchParams: Promise.resolve({}) }));
    expect(screen.getByRole("alert")).toHaveTextContent(/Your session has expired/);
  });

  it("reports a rate limit with its own wording", async () => {
    vi.spyOn(api, "ipsBehaviorSources").mockRejectedValue(new ApiError(429, "slow down", undefined, undefined, 30));
    render(await IpsBehaviorPage({ searchParams: Promise.resolve({}) }));
    const text = screen.getByRole("alert").textContent ?? "";
    expect(text).not.toMatch(/unreachable/);
    expect(text).toMatch(/30/);
  });
});

describe("IPS behavior: boundaries", () => {
  it("announces loading politely and names what is loading", () => {
    render(<Loading />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading IPS behavior");
  });

  it("offers a retry from the error boundary and shows no exception text", async () => {
    const reset = vi.fn();
    render(<IpsBehaviorError error={Object.assign(new Error("secret stack"), { digest: "abc123" })} reset={reset} />);
    expect(screen.getByRole("alert")).toHaveTextContent("IPS behavior could not be displayed");
    expect(screen.getByRole("alert").textContent).not.toContain("secret stack");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(reset).toHaveBeenCalled();
  });

  it("links each source to its page", async () => {
    await renderPage();
    expect(screen.getByRole("link", { name: "lab-ips-01" })).toHaveAttribute(
      "href",
      "/behavior/ips/sources/1b5c0000-0000-4000-8000-000000000501",
    );
  });
});
