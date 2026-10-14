"use client";

// One small hook for the Home widgets' reads (UR.5.4, UR.8.3).
//
// Widgets that need the same URL share one request: a module-level map keyed by
// the request's identity holds the request in flight and, once it settles, its
// result for as long as at least one caller is still mounted. The request is
// aborted only when no caller is left. A refresh from the top bar moves the
// shell's tick, every mounted caller re-runs, and the first one starts a new
// request that the others join; the data already on screen stays until the new
// answer lands.
//
// Failure never leaks text: a refused read (403) is its own state, so the widget
// can say which role it needs, and anything else is `error` with the HTTP status
// only. An exception's message is not exposed.
//
// A refresh that fails does not erase what the analyst was looking at: when the
// caller already holds an answer for the key and the refresh fails with anything
// but 403, the state stays `ok` with the previous data and `receivedAt`, and
// `refreshFailed` carries the HTTP status so the widget can say so. A 403 on
// refresh is still `forbidden`: the role changed, and data it can no longer read
// must not stay up. With no earlier answer a failure is `error`. The next
// successful refresh clears `refreshFailed`.

import { useEffect, useRef, useState } from "react";

import { ApiError } from "@/lib/api";
import { useRefreshTick } from "@/components/shell/AutoRefresh";

export type ResourceState<T> =
  | { status: "idle" }
  | { status: "loading" }
  | {
      status: "ok";
      data: T;
      /** ISO instant the browser received the data. */
      receivedAt: string;
      /** True while a refresh is in flight; `data` is the previous answer. */
      refreshing: boolean;
      /** The latest refresh failed (not 403); `data` is the last good answer. */
      refreshFailed: { httpStatus: number | null } | null;
    }
  | { status: "forbidden"; httpStatus: 403 }
  | { status: "error"; httpStatus: number | null };

type Settled =
  | { status: "ok"; data: unknown; receivedAt: string }
  | { status: "forbidden"; httpStatus: 403 }
  | { status: "error"; httpStatus: number | null };

interface Entry {
  controller: AbortController;
  listeners: Set<(s: Settled) => void>;
  settled: Settled | null;
}

interface Held {
  key: string;
  settled: Settled | null;
  refreshing: boolean;
  refreshFailed: { httpStatus: number | null } | null;
}

const entries = new Map<string, Entry>();

function settle(entry: Entry, result: Settled) {
  entry.settled = result;
  for (const listener of [...entry.listeners]) listener(result);
}

function start(key: string, fetcher: (signal: AbortSignal) => Promise<unknown>): Entry {
  const entry: Entry = { controller: new AbortController(), listeners: new Set(), settled: null };
  entries.set(key, entry);
  fetcher(entry.controller.signal).then(
    (data) => {
      if (entries.get(key) !== entry || entry.controller.signal.aborted) return;
      settle(entry, { status: "ok", data, receivedAt: new Date().toISOString() });
    },
    (err: unknown) => {
      if (entries.get(key) !== entry || entry.controller.signal.aborted) return;
      if (err instanceof ApiError && err.status === 403) {
        settle(entry, { status: "forbidden", httpStatus: 403 });
      } else {
        settle(entry, {
          status: "error",
          httpStatus: err instanceof ApiError ? err.status : null,
        });
      }
    },
  );
  return entry;
}

function release(key: string, entry: Entry, listener: (s: Settled) => void) {
  entry.listeners.delete(listener);
  if (entry.listeners.size > 0) return;
  if (entries.get(key) === entry) entries.delete(key);
  entry.controller.abort();
}

/**
 * `key` is the request's identity (the path with its query); `null` means "do
 * not fetch yet". Callers with the same key share one request.
 */
export function useApiResource<T>(
  key: string | null,
  fetcher: (signal: AbortSignal) => Promise<T>,
): ResourceState<T> {
  const tick = useRefreshTick();
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;
  const [held, setHeld] = useState<Held | null>(null);

  useEffect(() => {
    if (key === null) return;
    const listener = (s: Settled) =>
      setHeld((prev) =>
        s.status === "error" && prev && prev.key === key && prev.settled?.status === "ok"
          ? { ...prev, refreshing: false, refreshFailed: { httpStatus: s.httpStatus } }
          : { key, settled: s, refreshing: false, refreshFailed: null },
      );
    // A refresh keeps the previous answer on screen; a new key starts empty.
    setHeld((prev) =>
      prev && prev.key === key
        ? { ...prev, refreshing: prev.settled?.status === "ok" }
        : { key, settled: null, refreshing: false, refreshFailed: null },
    );
    const entry = entries.get(key) ?? start(key, (signal) => fetcherRef.current(signal));
    entry.listeners.add(listener);
    if (entry.settled) listener(entry.settled);
    return () => release(key, entry, listener);
  }, [key, tick]);

  if (key === null) return { status: "idle" };
  if (!held || held.key !== key || !held.settled) return { status: "loading" };
  const s = held.settled;
  if (s.status === "ok") {
    return {
      status: "ok",
      data: s.data as T,
      receivedAt: s.receivedAt,
      refreshing: held.refreshing,
      refreshFailed: held.refreshFailed,
    };
  }
  return s;
}
