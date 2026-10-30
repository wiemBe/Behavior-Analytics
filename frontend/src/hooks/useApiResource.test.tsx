// useApiResource: callers with one key share one request, the request is aborted
// only when nobody is left, a refresh re-reads while the old data stays, and a
// failure never leaks the exception's text.

import { act, render, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api";

import { useApiResource, type ResourceState } from "./useApiResource";

const shell = vi.hoisted(() => ({ tick: 0 }));
vi.mock("@/components/shell/AutoRefresh", () => ({ useRefreshTick: () => shell.tick }));

interface Call {
  key: string;
  signal: AbortSignal;
  resolve: (v: unknown) => void;
  reject: (e: unknown) => void;
}

let calls: Call[] = [];
function fetcherFor(key: string) {
  return (signal: AbortSignal) =>
    new Promise<unknown>((resolve, reject) => {
      calls.push({ key, signal, resolve, reject });
    });
}
const flush = () => act(async () => {});

beforeEach(() => {
  calls = [];
  shell.tick = 0;
});
afterEach(() => {
  // Leave no shared request behind for the next test.
  document.body.innerHTML = "";
});

function useTwo(key: string | null) {
  const a = useApiResource<string>(key, fetcherFor(key ?? "none") as never);
  const b = useApiResource<string>(key, fetcherFor(key ?? "none") as never);
  return [a, b] as const;
}

describe("sharing", () => {
  it("sends one request for two callers with one key, and gives both the result", async () => {
    const { result, unmount } = renderHook(() => useTwo("/a"));
    await flush();
    expect(calls).toHaveLength(1);
    expect(result.current[0]).toEqual({ status: "loading" });
    await act(async () => calls[0].resolve("hello"));
    for (const s of result.current) {
      expect(s).toMatchObject({ status: "ok", data: "hello", refreshing: false });
    }
    unmount();
  });

  it("gives a caller that arrives after it settled the cached result, without a new request", async () => {
    const first = renderHook(() => useApiResource<string>("/b", fetcherFor("/b") as never));
    await flush();
    await act(async () => calls[0].resolve("one"));
    const late = renderHook(() => useApiResource<string>("/b", fetcherFor("/b") as never));
    await flush();
    expect(calls).toHaveLength(1);
    expect(late.result.current).toMatchObject({ status: "ok", data: "one" });
    first.unmount();
    late.unmount();
  });

  it("sends distinct requests for distinct keys", async () => {
    const x = renderHook(() => useApiResource<string>("/x", fetcherFor("/x") as never));
    const y = renderHook(() => useApiResource<string>("/y", fetcherFor("/y") as never));
    await flush();
    expect(calls.map((c) => c.key).sort()).toEqual(["/x", "/y"]);
    x.unmount();
    y.unmount();
  });

  it("sends nothing for a null key", async () => {
    const { result } = renderHook(() => useApiResource<string>(null, fetcherFor("/n") as never));
    await flush();
    expect(calls).toHaveLength(0);
    expect(result.current).toEqual({ status: "idle" });
  });

  it("does not show another key's data after the key changes", async () => {
    const { result, rerender, unmount } = renderHook(
      ({ k }: { k: string }) => useApiResource<string>(k, fetcherFor(k) as never),
      { initialProps: { k: "/k1" } },
    );
    await flush();
    await act(async () => calls[0].resolve("first"));
    rerender({ k: "/k2" });
    expect(result.current).toEqual({ status: "loading" });
    unmount();
  });
});

describe("abort", () => {
  it("does not abort while another caller is still mounted", async () => {
    const a = renderHook(() => useApiResource<string>("/c", fetcherFor("/c") as never));
    const b = renderHook(() => useApiResource<string>("/c", fetcherFor("/c") as never));
    await flush();
    a.unmount();
    expect(calls[0].signal.aborted).toBe(false);
    await act(async () => calls[0].resolve("still delivered"));
    expect(b.result.current).toMatchObject({ status: "ok", data: "still delivered" });
    b.unmount();
  });

  it("aborts when the last caller unmounts", async () => {
    const a = renderHook(() => useApiResource<string>("/d", fetcherFor("/d") as never));
    const b = renderHook(() => useApiResource<string>("/d", fetcherFor("/d") as never));
    await flush();
    a.unmount();
    b.unmount();
    expect(calls[0].signal.aborted).toBe(true);
  });

  it("ignores a late answer to an aborted request, and a later mount starts a fresh one", async () => {
    const a = renderHook(() => useApiResource<string>("/e", fetcherFor("/e") as never));
    await flush();
    a.unmount();
    calls[0].resolve("too late");
    const b = renderHook(() => useApiResource<string>("/e", fetcherFor("/e") as never));
    await flush();
    expect(calls).toHaveLength(2);
    expect(b.result.current).toEqual({ status: "loading" });
    b.unmount();
  });
});

describe("failure", () => {
  it("reports a 403 as forbidden, with the status", async () => {
    const { result, unmount } = renderHook(() =>
      useApiResource<string>("/f", fetcherFor("/f") as never),
    );
    await flush();
    await act(async () => calls[0].reject(new ApiError(403, "role lacks read:fleet")));
    expect(result.current).toEqual({ status: "forbidden", httpStatus: 403 });
    unmount();
  });

  it("reports any other failure as an error without the exception text", async () => {
    const { result, unmount } = renderHook(() =>
      useApiResource<string>("/g", fetcherFor("/g") as never),
    );
    await flush();
    await act(async () =>
      calls[0].reject(new ApiError(500, "psycopg2 password authentication failed")),
    );
    expect(result.current).toEqual({ status: "error", httpStatus: 500 });
    expect(JSON.stringify(result.current)).not.toContain("psycopg2");
    unmount();
  });

  it("carries a 429's Retry-After seconds on the error", async () => {
    const { result, unmount } = renderHook(() =>
      useApiResource<string>("/rl", fetcherFor("/rl") as never),
    );
    await flush();
    await act(async () =>
      calls[0].reject(new ApiError(429, "rate limit exceeded", undefined, undefined, 12)),
    );
    expect(result.current).toEqual({ status: "error", httpStatus: 429, retryAfter: 12 });
    unmount();
  });

  it("reports a network failure as an error with no status", async () => {
    const { result, unmount } = renderHook(() =>
      useApiResource<string>("/h", fetcherFor("/h") as never),
    );
    await flush();
    await act(async () => calls[0].reject(new TypeError("Failed to fetch http://secret-host")));
    expect(result.current).toEqual({ status: "error", httpStatus: null });
    unmount();
  });
});

describe("refresh", () => {
  it("re-fetches when the tick moves and keeps the old data visible meanwhile", async () => {
    const seen: ResourceState<string>[] = [];
    function Probe() {
      seen.push(useApiResource<string>("/r", fetcherFor("/r") as never));
      return null;
    }
    const { rerender, unmount } = render(<Probe />);
    await flush();
    await act(async () => calls[0].resolve("v1"));
    shell.tick = 1;
    rerender(<Probe />);
    await flush();
    expect(calls).toHaveLength(2);
    expect(seen[seen.length - 1]).toMatchObject({ status: "ok", data: "v1", refreshing: true });
    await act(async () => calls[1].resolve("v2"));
    expect(seen[seen.length - 1]).toMatchObject({ status: "ok", data: "v2", refreshing: false });
    unmount();
  });

  it("sends one new request for two callers on a refresh", async () => {
    const { rerender, unmount } = renderHook(() => useTwo("/s"));
    await flush();
    await act(async () => calls[0].resolve("v1"));
    shell.tick = 1;
    rerender();
    await flush();
    expect(calls).toHaveLength(2);
    unmount();
  });

  it("keeps the last good data and flags refreshFailed when a refresh fails", async () => {
    const seen: ResourceState<string>[] = [];
    function Probe() {
      seen.push(useApiResource<string>("/rf", fetcherFor("/rf") as never));
      return null;
    }
    const { rerender, unmount } = render(<Probe />);
    await flush();
    await act(async () => calls[0].resolve("v1"));
    const first = seen[seen.length - 1];
    shell.tick = 1;
    rerender(<Probe />);
    await flush();
    await act(async () => calls[1].reject(new ApiError(502, "bad gateway from upstream-host")));
    const s = seen[seen.length - 1];
    expect(s).toMatchObject({
      status: "ok",
      data: "v1",
      refreshing: false,
      refreshFailed: { httpStatus: 502 },
    });
    expect((s as { receivedAt: string }).receivedAt).toBe((first as { receivedAt: string }).receivedAt);
    expect(JSON.stringify(s)).not.toContain("upstream-host");
    unmount();
  });

  it("gives forbidden, not stale data, when a refresh is refused with 403", async () => {
    const seen: ResourceState<string>[] = [];
    function Probe() {
      seen.push(useApiResource<string>("/r403", fetcherFor("/r403") as never));
      return null;
    }
    const { rerender, unmount } = render(<Probe />);
    await flush();
    await act(async () => calls[0].resolve("v1"));
    shell.tick = 1;
    rerender(<Probe />);
    await flush();
    await act(async () => calls[1].reject(new ApiError(403, "no")));
    expect(seen[seen.length - 1]).toEqual({ status: "forbidden", httpStatus: 403 });
    unmount();
  });

  it("clears refreshFailed on the next successful refresh", async () => {
    const seen: ResourceState<string>[] = [];
    function Probe() {
      seen.push(useApiResource<string>("/rc", fetcherFor("/rc") as never));
      return null;
    }
    const { rerender, unmount } = render(<Probe />);
    await flush();
    await act(async () => calls[0].resolve("v1"));
    shell.tick = 1;
    rerender(<Probe />);
    await flush();
    await act(async () => calls[1].reject(new ApiError(500, "x")));
    expect(seen[seen.length - 1]).toMatchObject({ refreshFailed: { httpStatus: 500 } });
    shell.tick = 2;
    rerender(<Probe />);
    await flush();
    await act(async () => calls[2].resolve("v3"));
    expect(seen[seen.length - 1]).toMatchObject({
      status: "ok",
      data: "v3",
      refreshFailed: null,
    });
    unmount();
  });

  it("is an error, not ok, when the first request fails", async () => {
    const { result, unmount } = renderHook(() =>
      useApiResource<string>("/first", fetcherFor("/first") as never),
    );
    await flush();
    await act(async () => calls[0].reject(new ApiError(502, "x")));
    expect(result.current).toEqual({ status: "error", httpStatus: 502 });
    unmount();
  });
});
