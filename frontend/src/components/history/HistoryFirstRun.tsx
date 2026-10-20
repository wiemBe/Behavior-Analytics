"use client";

// Mounted once in the shell. For a principal holding `admin:history_backfill` it asks the status
// once per browser session and opens the first-run dialog when an enabled console has no
// decision. A caller without the permission is never asked, and a 403 renders nothing: that is
// not an error. The session remembers that the dialog was shown, so a page change or a reload
// does not ask again; when storage is unavailable the question is asked once per page load.

import { useEffect, useRef, useState } from "react";

import { holds } from "@/components/auth/access";
import { useSession } from "@/components/auth/session";
import { ApiError, api, type HistoryConsole } from "@/lib/api";

import { FirstRunDialog, FirstRunErrorDialog } from "./FirstRunDialog";
import { ADMIN_PERMISSION } from "./historyFormat";

export const SHOWN_KEY = "history-backfill-first-run-shown";

function wasShown(): boolean {
  try {
    return window.sessionStorage.getItem(SHOWN_KEY) === "1";
  } catch {
    return false;
  }
}

function remember() {
  try {
    window.sessionStorage.setItem(SHOWN_KEY, "1");
  } catch {
    /* a per-viewer convenience: without storage the dialog is asked once per page load */
  }
}

type Gate =
  | { status: "idle" }
  | { status: "open"; consoles: HistoryConsole[] }
  | { status: "error" };

export function HistoryFirstRun() {
  const session = useSession();
  const granted = session.status === "ready" && session.me ? session.me.permissions : [];
  const allowed = session.status === "ready" && holds(granted, ADMIN_PERMISSION);
  const asked = useRef(false);
  const [gate, setGate] = useState<Gate>({ status: "idle" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!allowed || (asked.current && attempt === 0)) return;
    asked.current = true;
    if (attempt === 0 && wasShown()) return;
    let cancelled = false;
    api.historyBackfill().then(
      (consoles) => {
        if (cancelled) return;
        const undecided = consoles.filter((c) => c.decision === null);
        if (undecided.length === 0) {
          setGate({ status: "idle" });
          return;
        }
        remember();
        setGate({ status: "open", consoles: undecided });
      },
      (err: unknown) => {
        if (cancelled) return;
        if (err instanceof ApiError && (err.status === 403 || err.status === 401)) return;
        // Not remembered: a failed read has not shown the question, so the next page load asks.
        setGate({ status: "error" });
      },
    );
    return () => {
      cancelled = true;
    };
  }, [allowed, attempt]);

  if (gate.status === "open") {
    return <FirstRunDialog consoles={gate.consoles} onClose={() => setGate({ status: "idle" })} />;
  }
  if (gate.status === "error") {
    return (
      <FirstRunErrorDialog
        onClose={() => setGate({ status: "idle" })}
        onRetry={() => setAttempt((n) => n + 1)}
      />
    );
  }
  return null;
}
