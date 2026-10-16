import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { SubjectPicker } from "./SubjectPicker";
import { catalog } from "./fixtures";
import { api } from "@/lib/api";

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
        value={{ logSourceId: "", sourceClass: "", wafEndpointId: "", ipsEntityId: "" }}
        onKind={vi.fn()}
        onChange={vi.fn()}
        onLabel={vi.fn()}
      />,
    );
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
        value={{ logSourceId: "", sourceClass: "", wafEndpointId: "", ipsEntityId: "" }}
        onKind={vi.fn()}
        onChange={vi.fn()}
        onLabel={vi.fn()}
      />,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("Check the backend");
  });
});
