import { describe, expect, it } from "vitest";

import type { SocOverview } from "@/lib/api";
import { ok } from "@/components/home/KpiTestStates";

import { inventoryHonesty } from "./inventoryHonesty";

const overview = (state: SocOverview["log_sources"]["inventory_state"]) =>
  ok({ log_sources: { inventory_state: state, total_log_sources: 0 } } as SocOverview);

describe("inventoryHonesty", () => {
  it("shows sources that exist even when the latest inventory sync failed", () => {
    expect(inventoryHonesty(3, overview("FAILED"))).toBe("show");
  });

  it("waits while the overview of an empty fleet is still loading", () => {
    expect(inventoryHonesty(0, { status: "loading" })).toBe("wait");
  });

  it("is a failure when the empty fleet's inventory watermark failed", () => {
    expect(inventoryHonesty(0, overview("FAILED"))).toBe("failed");
  });

  it("is not collected when the empty fleet has no inventory watermark", () => {
    expect(inventoryHonesty(0, overview("NOT_COLLECTED"))).toBe("not_collected");
  });

  it("shows a clean empty inventory", () => {
    expect(inventoryHonesty(0, overview("COMPLETE"))).toBe("show");
  });

  it("does not treat a refused overview as a failed inventory", () => {
    expect(inventoryHonesty(0, { status: "forbidden", httpStatus: 403 })).toBe("show");
  });
});
