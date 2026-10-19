import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { SubjectPicker } from "./SubjectPicker";
import { catalog } from "./fixtures";
import { api } from "@/lib/api";
import { paged, value, watch } from "@/components/propertywatch/fixtures";

describe("custom-rule subject picker", () => {
  it("explains an empty admitted WAF endpoint list and links to custom search", async () => {
    vi.spyOn(api, "wafBehaviorSources").mockResolvedValue({
      items: [{
        log_source_id: "waf-1",
        log_source_name: "edge-waf",
        product_label: null,
        enabled: true,
        profile_version: 1,
        validation_status: "VALID",
        validated_profile_version: 1,
        validated_at: "2026-10-16T07:00:00Z",
        validation_current: true,
        virtual_host_available: true,
        latest_bucket: null,
        active_endpoints: 0,
        total_endpoints: 0,
        active_endpoint_ceiling: 100,
        admission: { buckets_in_range: 1, buckets_admission_truncated: 0, rejected_over_quota: 0, below_admission_threshold: 2, untracked_requests: 2 },
      }],
      total: 1,
      limit: 200,
      offset: 0,
      range_start: "2026-10-16T06:00:00Z",
      range_end: "2026-10-16T07:00:00Z",
    });
    vi.spyOn(api, "wafBehaviorEndpoints").mockResolvedValue({ items: [], total: 0, limit: 200, offset: 0 });
    render(
      <SubjectPicker
        catalog={catalog()}
        kind="WAF_ENDPOINT"
        value={{ logSourceId: "", sourceClass: "", wafEndpointId: "", ipsEntityId: "", pwValueId: "" }}
        onKind={vi.fn()}
        onChange={vi.fn()}
        onLabel={vi.fn()}
      />,
    );
    expect(screen.getByRole("searchbox", { name: "Find WAF endpoint" })).toBeInTheDocument();
    const source = await screen.findByRole("combobox", { name: "WAF source" });
    fireEvent.change(source, { target: { value: "waf-1" } });
    await waitFor(() => expect(screen.getByText(/No admitted endpoints are available/)).toBeInTheDocument());
    expect(screen.getByText(/Only admitted subjects/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "custom search" })).toHaveAttribute("href", "/searches/new");
  });

  it("names a failed source inventory and the recovery action", async () => {
    vi.spyOn(api, "sourceBehaviors").mockRejectedValue(new Error("down"));
    render(
      <SubjectPicker
        catalog={catalog()}
        kind="LOG_SOURCE"
        value={{ logSourceId: "", sourceClass: "", wafEndpointId: "", ipsEntityId: "", pwValueId: "" }}
        onKind={vi.fn()}
        onChange={vi.fn()}
        onLabel={vi.fn()}
      />,
    );
    expect(screen.getByRole("searchbox", { name: "Find log source" })).toBeInTheDocument();
    expect(await screen.findByRole("alert")).toHaveTextContent("Check the backend");
  });

  it("offers only pinned property-watch values, renders hostile text inert and links to pin first", async () => {
    vi.spyOn(api, "propertyWatches").mockResolvedValue([watch()]);
    vi.spyOn(api, "propertyWatchValues").mockResolvedValue(
      paged([
        value("pin-1", '<img src=x onerror="x">', {
          pinned: true,
          components: ['<img src=x onerror="x">'],
        }),
        value("auto-1", "automatic", { pinned: false }),
      ]),
    );
    const change = vi.fn();
    const { container } = render(
      <SubjectPicker
        catalog={catalog()}
        kind="PW_VALUE"
        value={{ logSourceId: "", sourceClass: "", wafEndpointId: "", ipsEntityId: "", pwValueId: "" }}
        onKind={vi.fn()}
        onChange={change}
        onLabel={vi.fn()}
      />,
    );
    const watches = await screen.findByRole("combobox", { name: "Property watch" });
    fireEvent.change(watches, { target: { value: watch().id } });
    const values = await screen.findByRole("combobox", { name: "Pinned value" });
    await waitFor(() => expect(values).not.toBeDisabled());
    expect(within(values).getByRole("option", { name: /<img/ })).toBeInTheDocument();
    expect(within(values).queryByRole("option", { name: "automatic" })).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByRole("link", { name: /pin it on the watch first/i })).toHaveAttribute(
      "href",
      `/behavior/watches/${watch().id}`,
    );
  });

  it("explains that a fresh pin waits for the next collection tick", async () => {
    vi.spyOn(api, "propertyWatches").mockResolvedValue([watch()]);
    vi.spyOn(api, "propertyWatchValues").mockResolvedValue(paged([]));
    render(
      <SubjectPicker
        catalog={catalog()}
        kind="PW_VALUE"
        value={{ logSourceId: "", sourceClass: "", wafEndpointId: "", ipsEntityId: "", pwValueId: "" }}
        onKind={vi.fn()}
        onChange={vi.fn()}
        onLabel={vi.fn()}
      />,
    );
    fireEvent.change(await screen.findByRole("combobox", { name: "Property watch" }), {
      target: { value: watch().id },
    });
    expect(await screen.findByText(/new pin can be used in a rule after the next property-watch collection\./)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /open the property watch/i })).toHaveAttribute(
      "href",
      `/behavior/watches/${watch().id}`,
    );
  });

  it("says a just-pinned value is not listed yet, even when other pins are", async () => {
    vi.spyOn(api, "propertyWatches").mockResolvedValue([watch()]);
    vi.spyOn(api, "propertyWatchValues").mockResolvedValue(
      paged([value("pv-1", "svc-backup", { pinned: true }), value("pv-2", "alice")]),
    );
    render(
      <SubjectPicker
        catalog={catalog()}
        kind="PW_VALUE"
        value={{ logSourceId: "", sourceClass: "", wafEndpointId: "", ipsEntityId: "", pwValueId: "" }}
        onKind={vi.fn()}
        onChange={vi.fn()}
        onLabel={vi.fn()}
      />,
    );
    fireEvent.change(await screen.findByRole("combobox", { name: "Property watch" }), {
      target: { value: watch().id },
    });
    expect(await screen.findByText(/pinned just now is listed after the next/)).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "alice" })).toBeNull();
  });
});
