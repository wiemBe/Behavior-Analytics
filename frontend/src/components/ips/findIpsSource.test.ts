import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";

import { ipsSource } from "./behaviorFixtures";
import { findIpsSource } from "./findIpsSource";

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("findIpsSource", () => {
  it("finds a source on a later page of the list", async () => {
    const list = vi
      .spyOn(api, "ipsBehaviorSources")
      .mockResolvedValueOnce({
        items: [ipsSource({ log_source_id: "a" }), ipsSource({ log_source_id: "b" })],
        total: 3,
        limit: 2,
        offset: 0,
      })
      .mockResolvedValueOnce({ items: [ipsSource({ log_source_id: "c" })], total: 3, limit: 2, offset: 2 });
    expect((await findIpsSource("c"))?.log_source_id).toBe("c");
    expect(list).toHaveBeenNthCalledWith(2, { limit: 200, offset: 2 });
  });

  it("is null for an id the list does not hold, and stops at the end of the list", async () => {
    const list = vi.spyOn(api, "ipsBehaviorSources").mockResolvedValue({
      items: [ipsSource({ log_source_id: "a" })],
      total: 1,
      limit: 200,
      offset: 0,
    });
    expect(await findIpsSource("zzz")).toBeNull();
    expect(list).toHaveBeenCalledTimes(1);
  });

  it("stops on an empty page even when the total claims more", async () => {
    const list = vi.spyOn(api, "ipsBehaviorSources").mockResolvedValue({ items: [], total: 9, limit: 200, offset: 0 });
    expect(await findIpsSource("a")).toBeNull();
    expect(list).toHaveBeenCalledTimes(1);
  });
});
