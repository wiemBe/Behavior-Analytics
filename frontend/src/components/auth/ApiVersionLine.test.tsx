import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiVersionLine } from "./ApiVersionLine";

afterEach(() => vi.unstubAllGlobals());

function stubFetch(impl: () => Promise<unknown>) {
  vi.stubGlobal("fetch", vi.fn(impl));
}

describe("ApiVersionLine", () => {
  it("shows the version the API reports", async () => {
    stubFetch(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ version: "4.5.6", git_sha: null, migration_head: "0025" }),
    }));
    render(<ApiVersionLine />);
    expect(await screen.findByText("API version 4.5.6")).toBeInTheDocument();
  });

  it("says the version could not be read when the request fails", async () => {
    stubFetch(async () => {
      throw new TypeError("network down");
    });
    render(<ApiVersionLine />);
    expect(await screen.findByText("API version could not be read")).toBeInTheDocument();
    expect(screen.queryByText(/0\.1\.0/)).not.toBeInTheDocument();
  });

  it("says the same on an HTTP error", async () => {
    stubFetch(async () => ({
      ok: false,
      status: 502,
      statusText: "Bad Gateway",
      json: async () => ({}),
    }));
    render(<ApiVersionLine />);
    expect(await screen.findByText("API version could not be read")).toBeInTheDocument();
  });
});
