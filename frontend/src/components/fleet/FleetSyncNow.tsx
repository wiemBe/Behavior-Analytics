"use client";

// "Sync now" for the log-source inventory, and when it last ran.
//
// The click queues the worker task and nothing more. Success is only claimed once the status
// endpoint shows a pass that started after the click and is no longer running; a failed pass is
// said as failed; a pass that never reports back is said as unreported after a bounded wait.

import { useTranslations } from "next-intl";
import { useCallback, useEffect, useRef, useState } from "react";

import { useSession } from "@/components/auth/session";
import { UtcTime } from "@/components/ui/UtcTime";
import { ApiError, fleetApi, type FleetSyncStatus } from "@/lib/api";
import { ADMIN_PERMISSION, holds } from "@/lib/fleetSources";

import "@/components/ui/controls.css";
import "./fleet-sources.css";

export const SYNC_POLL_MS = 3000;
export const SYNC_POLL_MAX = 40;

type Outcome =
  | { kind: "idle" }
  | { kind: "waiting"; already: boolean }
  | { kind: "done" }
  | { kind: "failed"; names: string }
  | { kind: "timeout" }
  | { kind: "error"; message: string };

function latest(status: FleetSyncStatus | null): string | null {
  const times = (status?.instances ?? [])
    .map((i) => i.last_success_at)
    .filter((v): v is string => v !== null)
    .sort();
  return times.length ? times[times.length - 1] : null;
}

function failedNames(status: FleetSyncStatus | null): string {
  return (status?.instances ?? [])
    .filter((i) => i.status === "FAILED")
    .map((i) => i.instance_name)
    .join(", ");
}

export function FleetSyncNow({ onFinished }: { onFinished: () => void }) {
  const t = useTranslations("fleet.sourceList.sync");
  const { me } = useSession();
  const admin = holds(me, ADMIN_PERMISSION);
  const [status, setStatus] = useState<FleetSyncStatus | null>(null);
  const [statusFailed, setStatusFailed] = useState(false);
  const [outcome, setOutcome] = useState<Outcome>({ kind: "idle" });
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      if (timer.current) clearTimeout(timer.current);
    };
  }, []);

  const read = useCallback(async () => {
    try {
      const next = await fleetApi.syncStatus();
      if (alive.current) {
        setStatus(next);
        setStatusFailed(false);
      }
      return next;
    } catch {
      if (alive.current) setStatusFailed(true);
      return null;
    }
  }, []);

  useEffect(() => {
    if (admin) void read();
  }, [admin, read]);

  if (!admin) return null;

  function poll(before: Map<string, string | null>, tries: number) {
    timer.current = setTimeout(async () => {
      const now = await read();
      if (!alive.current) return;
      const running = now?.running ?? true;
      const ran = (now?.instances ?? []).some((i) => i.last_run_at !== (before.get(i.instance_id) ?? null));
      if (now && !running && ran) {
        const names = failedNames(now);
        if (names) {
          setOutcome({ kind: "failed", names });
        } else {
          setOutcome({ kind: "done" });
          onFinished();
        }
        return;
      }
      if (tries + 1 >= SYNC_POLL_MAX) {
        setOutcome({ kind: "timeout" });
        return;
      }
      poll(before, tries + 1);
    }, SYNC_POLL_MS);
  }

  async function start() {
    if (outcome.kind === "waiting") return;
    const before = new Map<string, string | null>(
      (status?.instances ?? []).map((i) => [i.instance_id, i.last_run_at]),
    );
    setOutcome({ kind: "waiting", already: false });
    try {
      const res = await fleetApi.queueSync();
      if (!alive.current) return;
      setOutcome({ kind: "waiting", already: res.already_running });
      poll(before, 0);
    } catch (err) {
      if (!alive.current) return;
      setOutcome({
        kind: "error",
        message:
          err instanceof ApiError && err.status === 403
            ? t("forbidden", { permission: ADMIN_PERMISSION })
            : t("error", { detail: err instanceof Error ? err.message : String(err) }),
      });
    }
  }

  const waiting = outcome.kind === "waiting";
  const last = latest(status);
  const failedBefore = failedNames(status);
  return (
    <div className="fs-sync">
      <button type="button" className="action secondary" disabled={waiting} onClick={() => void start()}>
        {waiting ? t("running") : t("button")}
      </button>
      <p className="muted fs-sync-line">
        {statusFailed ? (
          t("statusError")
        ) : status === null ? null : last ? (
          <>
            {t("last")}
            <UtcTime value={last} />
            {failedBefore ? ` · ${t("failedOn", { names: failedBefore })}` : ""}
          </>
        ) : (
          t("never")
        )}
      </p>
      {outcome.kind === "waiting" && (
        <p className="change-status" role="status">
          {outcome.already ? t("alreadyRunning") : t("queued")}
        </p>
      )}
      {outcome.kind === "done" && (
        <p className="change-status ok" role="status">
          {t("done")}
        </p>
      )}
      {outcome.kind === "failed" && (
        <p className="change-status crit" role="alert">
          {t("failedNow", { names: outcome.names })}
        </p>
      )}
      {outcome.kind === "timeout" && (
        <p className="change-status" role="status">
          {t("timeout", { minutes: Math.round((SYNC_POLL_MS * SYNC_POLL_MAX) / 60000) })}
        </p>
      )}
      {outcome.kind === "error" && (
        <p className="change-status crit" role="alert">
          {outcome.message}
        </p>
      )}
    </div>
  );
}
