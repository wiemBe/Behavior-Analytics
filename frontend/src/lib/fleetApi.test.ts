import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, fleetApi } from "./api";

afterEach(() => vi.unstubAllGlobals());

function stub(status: number, body: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(status === 204 ? null : JSON.stringify(body), { status }),
    ),
  );
}

describe("fleetApi", () => {
  it("returns undefined for a 204 delete", async () => {
    stub(204, null);
    await expect(fleetApi.deleteRule("r")).resolves.toBeUndefined();
  });

  it("flattens a pydantic 422 detail list into one sentence", async () => {
    stub(422, {
      detail: [{ msg: "Value error, pattern is not a valid regular expression: x" }],
    });
    await expect(
      fleetApi.createRule({ kind: "NAME_REGEX", pattern: "(", target_class: "WAF" }),
    ).rejects.toMatchObject({
      status: 422,
      message: "pattern is not a valid regular expression: x",
    });
  });

  it("keeps a string detail", async () => {
    stub(409, { detail: "a built-in rule can only be enabled or disabled" });
    const err = await fleetApi.updateRule("r", { enabled: true }).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.message).toContain("built-in");
  });

  it("always sends override_class, null to clear", async () => {
    stub(200, {});
    await fleetApi.setOverride("s", null);
    const init = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0][1];
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body)).toEqual({ override_class: null });
  });
});
