"use client";

// The shell's top bar: search, auto-refresh, the open-alert count and the
// connection status. Each is its own component; this one owns the shared reads.
//
// /overview feeds both the bell and the instance signal, so it is read once.
// All reads are browser GETs of existing endpoints, aborted on unmount
// (StrictMode mounts twice in development) and repeated on every auto-refresh.
// A re-read keeps the previous value on screen until the new one arrives, so the
// bar does not flicker to "loading" every few minutes.

import { useCallback, useEffect, useState } from "react";

import { AlertBell } from "./AlertBell";
import { AutoRefresh } from "./AutoRefresh";
import { ConnectionStatus } from "./ConnectionStatus";
import { TopSearch } from "./TopSearch";
import {
  ApiError,
  shellApi,
  type HealthReady,
  type SearchCadenceItem,
  type SocOverview,
} from "@/lib/api";

/** A read as the bar sees it: pending, answered, refused (403) or failed. */
export type Loaded<T> =
  | { state: "loading" }
  | { state: "ok"; data: T }
  | { state: "forbidden" }
  | { state: "error" };

const LOADING = { state: "loading" } as const;

function track<T>(
  read: (signal: AbortSignal) => Promise<T>,
  signal: AbortSignal,
  set: (v: Loaded<T>) => void,
) {
  read(signal).then(
    (data) => {
      if (!signal.aborted) set({ state: "ok", data });
    },
    (e: unknown) => {
      if (signal.aborted) return;
      set(e instanceof ApiError && e.status === 403 ? { state: "forbidden" } : { state: "error" });
    },
  );
}

export function TopBar() {
  const [tick, setTick] = useState(0);
  const [health, setHealth] = useState<Loaded<HealthReady>>(LOADING);
  const [overview, setOverview] = useState<Loaded<SocOverview>>(LOADING);
  const [cadence, setCadence] = useState<Loaded<SearchCadenceItem[]>>(LOADING);

  useEffect(() => {
    const controller = new AbortController();
    track((s) => shellApi.healthReady(s), controller.signal, setHealth);
    track((s) => shellApi.overview(s), controller.signal, setOverview);
    track((s) => shellApi.searchCadence(s), controller.signal, setCadence);
    return () => controller.abort();
  }, [tick]);

  const reload = useCallback(() => setTick((t) => t + 1), []);

  return (
    <div className="topbar">
      <TopSearch />
      <AutoRefresh onRefresh={reload} />
      <AlertBell overview={overview} />
      <ConnectionStatus health={health} overview={overview} cadence={cadence} />
    </div>
  );
}
