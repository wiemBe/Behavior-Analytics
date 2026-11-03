"use client";

// One read of the page, with its own state. The four reads of the alert e-mail page fail apart:
// a template list that did not load must not hide the relay summary. A reload keeps the answer on
// screen until the new one lands, so a table does not flash empty after a save.

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "@/lib/api";

export type Part<T> =
  | { status: "loading" }
  | { status: "ok"; data: T }
  | { status: "forbidden" }
  | { status: "error"; httpStatus: number | null; retryAfter: number | null };

export function usePart<T>(
  load: (signal: AbortSignal) => Promise<T>,
): { part: Part<T>; reload: () => void } {
  const [part, setPart] = useState<Part<T>>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  const loadRef = useRef(load);
  loadRef.current = load;

  useEffect(() => {
    const controller = new AbortController();
    if (attempt > 0) setPart((previous) => (previous.status === "ok" ? previous : { status: "loading" }));
    loadRef
      .current(controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) setPart({ status: "ok", data });
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        if (err instanceof ApiError && err.status === 403) {
          setPart({ status: "forbidden" });
        } else {
          setPart((previous) =>
            // A failed reload keeps the answer the page already shows.
            previous.status === "ok" && attempt > 0
              ? previous
              : {
                  status: "error",
                  httpStatus: err instanceof ApiError ? err.status : null,
                  retryAfter: err instanceof ApiError ? err.retryAfter : null,
                },
          );
        }
      });
    return () => controller.abort();
  }, [attempt]);

  const reload = useCallback(() => setAttempt((n) => n + 1), []);
  return { part, reload };
}
