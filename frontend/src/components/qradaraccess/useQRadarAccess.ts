"use client";

// The page's one read, the "Check now" request and the polling between them. A check takes about a
// minute and runs in the worker: the POST answers 202 at once, and the page reads the stored result
// every few seconds while any console says a check runs. Polling stops when the page unmounts,
// pauses while the tab is hidden, and ends after a fixed bound with a sentence that the check is
// still running in the worker. A run that never ends is bounded by the API (10 minutes), not here.

import { useCallback, useEffect, useRef, useState } from "react";

import {
  ApiError,
  qradarAccessApi,
  type AccessCheckAnswer,
  type QRadarAccess,
} from "@/lib/api";

import { anyRunning, POLL_LIMIT_SECONDS, POLL_SECONDS } from "./access";

export type AccessPart =
  | { status: "loading" }
  | { status: "ok"; data: QRadarAccess }
  | { status: "forbidden" }
  | { status: "error"; httpStatus: number | null; retryAfter: number | null };

export type CheckView =
  | { kind: "idle" }
  | { kind: "posting" }
  | {
      kind: "answered";
      started: string[];
      alreadyRunning: string[];
      failed: string[];
    }
  | { kind: "limited"; retryAfter: number | null }
  | { kind: "forbidden" }
  | { kind: "error"; httpStatus: number | null };

export interface QRadarAccessState {
  part: AccessPart;
  reload: () => void;
  view: CheckView;
  checkNow: () => void;
  /** The polling bound passed while a check still runs. */
  timedOut: boolean;
  /** A check this page started has ended since the last "Check now". */
  finished: boolean;
  /** A refresh after the first read failed: the page keeps showing the last answer. */
  refreshFailed: boolean;
}

export function useQRadarAccess(): QRadarAccessState {
  const [part, setPart] = useState<AccessPart>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  const [view, setView] = useState<CheckView>({ kind: "idle" });
  const [epoch, setEpoch] = useState(0);
  const [awaiting, setAwaiting] = useState(false);
  const [timedOut, setTimedOut] = useState(false);
  const [finished, setFinished] = useState(false);
  const [refreshFailed, setRefreshFailed] = useState(false);
  const mounted = useRef(true);
  const awaitingRef = useRef(false);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  // The first read, and the read behind "Try again".
  useEffect(() => {
    const controller = new AbortController();
    if (attempt > 0) setPart((prev) => (prev.status === "ok" ? prev : { status: "loading" }));
    qradarAccessApi
      .get(controller.signal)
      .then((data) => {
        if (controller.signal.aborted) return;
        setPart({ status: "ok", data });
        setRefreshFailed(false);
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        if (err instanceof ApiError && err.status === 403) {
          setPart({ status: "forbidden" });
          return;
        }
        setPart((prev) =>
          prev.status === "ok" && attempt > 0
            ? prev
            : {
                status: "error",
                httpStatus: err instanceof ApiError ? err.status : null,
                retryAfter: err instanceof ApiError ? err.retryAfter : null,
              },
        );
      });
    return () => controller.abort();
  }, [attempt]);

  const running = part.status === "ok" && anyRunning(part.data.consoles);
  const polling = running || awaiting;

  useEffect(() => {
    if (!polling) return;
    const controller = new AbortController();
    let visibleMs = 0;
    let stopped = false;
    let inFlight = false;

    const read = () => {
      if (inFlight || stopped) return;
      inFlight = true;
      qradarAccessApi
        .get(controller.signal)
        .then((data) => {
          if (controller.signal.aborted) return;
          setPart({ status: "ok", data });
          setRefreshFailed(false);
          if (!anyRunning(data.consoles)) {
            if (awaitingRef.current) {
              awaitingRef.current = false;
              setAwaiting(false);
              setFinished(true);
            }
          }
        })
        .catch(() => {
          if (!controller.signal.aborted) setRefreshFailed(true);
        })
        .finally(() => {
          inFlight = false;
        });
    };

    const tick = () => {
      if (typeof document !== "undefined" && document.hidden) return;
      visibleMs += POLL_SECONDS * 1000;
      if (visibleMs > POLL_LIMIT_SECONDS * 1000) {
        stopped = true;
        window.clearInterval(timer);
        if (mounted.current) setTimedOut(true);
        return;
      }
      read();
    };
    const timer = window.setInterval(tick, POLL_SECONDS * 1000);
    const onVisible = () => {
      if (!document.hidden) read();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      stopped = true;
      controller.abort();
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
    // `epoch` restarts the bound: a new "Check now" is a new wait.
  }, [polling, epoch]);

  const checkNow = useCallback(() => {
    setView({ kind: "posting" });
    setTimedOut(false);
    setFinished(false);
    qradarAccessApi
      .check()
      .then((answer: AccessCheckAnswer) => {
        if (!mounted.current) return;
        const { started, already_running, failed, ...stored } = answer;
        setPart({ status: "ok", data: stored });
        setView({ kind: "answered", started, alreadyRunning: already_running, failed });
        setRefreshFailed(false);
        setEpoch((n) => n + 1);
        const wait = started.length > 0 || already_running.length > 0;
        awaitingRef.current = wait;
        setAwaiting(wait);
      })
      .catch((err: unknown) => {
        if (!mounted.current) return;
        if (err instanceof ApiError && err.status === 429) {
          setView({ kind: "limited", retryAfter: err.retryAfter });
        } else if (err instanceof ApiError && err.status === 403) {
          setView({ kind: "forbidden" });
        } else {
          setView({ kind: "error", httpStatus: err instanceof ApiError ? err.status : null });
        }
      });
  }, []);

  const reload = useCallback(() => setAttempt((n) => n + 1), []);
  return { part, reload, view, checkNow, timedOut, finished, refreshFailed };
}
