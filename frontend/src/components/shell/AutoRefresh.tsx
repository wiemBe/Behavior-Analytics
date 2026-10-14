"use client";

// The top bar's auto-refresh: a select (Off, 1 min, 5 min), the time the
// browser last refreshed, and the page's data age when the page supplies one.
//
// Two different times, deliberately: "refreshed" is when this browser last
// re-requested the page; "data to" is how far the data reaches, which on a
// platform that collects on a cadence is the older and the honest one. No page
// supplies a data age yet, so none is shown: the age is never inferred here.
//
// A refresh is `router.refresh()`, which re-runs the server components of the
// current route without losing client state. The timer is paused while the tab
// is hidden and while a dialog is open, so an unattended tab does not poll and
// a refresh never lands under an open modal.
//
// `router.refresh()` does not reach client hooks, so every automatic refresh
// also bumps a tick that `useRefreshTick()` exposes; `useApiResource` re-reads
// when it moves. The tick lives beside the data age in the same provider.

import { useRouter } from "next/navigation";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useMemo,
  useState,
  type ReactNode,
} from "react";

export type RefreshChoice = "off" | "1" | "5";

const PERIOD_MS: Record<Exclude<RefreshChoice, "off">, number> = {
  "1": 60_000,
  "5": 300_000,
};

// --- data age: the hook a page uses to say how far its data reaches ---------

interface DataAgeValue {
  dataTo: string | null;
  setDataTo: (iso: string | null) => void;
}

const DataAgeContext = createContext<DataAgeValue>({ dataTo: null, setDataTo: () => {} });

// --- refresh tick: how client-side data learns the bar refreshed ------------

interface RefreshTickValue {
  tick: number;
  bump: () => void;
}

const RefreshTickContext = createContext<RefreshTickValue>({ tick: 0, bump: () => {} });

/** Incremented on every automatic refresh; 0 until the first one. */
export function useRefreshTick(): number {
  return useContext(RefreshTickContext).tick;
}

export function DataAgeProvider({ children }: { children: ReactNode }) {
  const [dataTo, setDataTo] = useState<string | null>(null);
  const value = useMemo(() => ({ dataTo, setDataTo }), [dataTo]);
  const [tick, setTick] = useState(0);
  const bump = useCallback(() => setTick((n) => n + 1), []);
  const tickValue = useMemo(() => ({ tick, bump }), [tick, bump]);
  return (
    <DataAgeContext.Provider value={value}>
      <RefreshTickContext.Provider value={tickValue}>{children}</RefreshTickContext.Provider>
    </DataAgeContext.Provider>
  );
}

/**
 * For a page to call: the instant its data reaches (an ISO timestamp), or null
 * when it does not know. Cleared when the page unmounts so the next route does
 * not inherit it.
 */
export function useReportDataAge(iso: string | null) {
  const { setDataTo } = useContext(DataAgeContext);
  useEffect(() => {
    setDataTo(iso);
    return () => setDataTo(null);
  }, [iso, setDataTo]);
}

/** `14:02:11 UTC`. Always UTC and always says so. */
export function formatClockUtc(value: Date | string): string {
  const d = typeof value === "string" ? new Date(value) : value;
  if (Number.isNaN(d.getTime())) return "";
  return `${d.toISOString().slice(11, 19)} UTC`;
}

function dialogOpen(): boolean {
  return document.querySelector('dialog[open], [aria-modal="true"]') !== null;
}

export function AutoRefresh({ onRefresh }: { onRefresh?: () => void }) {
  const router = useRouter();
  const selectId = useId();
  const { dataTo } = useContext(DataAgeContext);
  const { bump } = useContext(RefreshTickContext);
  const [choice, setChoice] = useState<RefreshChoice>("off");
  const [visible, setVisible] = useState(true);
  // Null until mounted: the clock is the browser's, so the server cannot render
  // it, and rendering it during hydration would mismatch.
  const [last, setLast] = useState<Date | null>(null);

  useEffect(() => {
    setLast(new Date());
    setVisible(!document.hidden);
    const onVisibility = () => setVisible(!document.hidden);
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, []);

  useEffect(() => {
    if (choice === "off" || !visible) return;
    const timer = setInterval(() => {
      if (dialogOpen()) return;
      router.refresh();
      bump();
      onRefresh?.();
      setLast(new Date());
    }, PERIOD_MS[choice]);
    return () => clearInterval(timer);
  }, [choice, visible, router, onRefresh, bump]);

  return (
    <div className="topbar-refresh">
      <label htmlFor={selectId} className="topbar-label">
        Refresh
      </label>
      <select
        id={selectId}
        className="topbar-select"
        value={choice}
        onChange={(e) => setChoice(e.target.value as RefreshChoice)}
      >
        <option value="off">Off</option>
        <option value="1">1 min</option>
        <option value="5">5 min</option>
      </select>
      <div className="topbar-times">
        {last && (
          <span>
            Refreshed <time dateTime={last.toISOString()}>{formatClockUtc(last)}</time>
          </span>
        )}
        {dataTo && formatClockUtc(dataTo) && (
          <span>
            Data to <time dateTime={dataTo}>{formatClockUtc(dataTo)}</time>
          </span>
        )}
      </div>
    </div>
  );
}
