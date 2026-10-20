"use client";

// The admin page's body: what has been read from QRadar's past, and what can still be. One
// section per enabled console, its decision block first, then its table. No chart.

import { useCallback, useEffect, useState } from "react";

import { holds } from "@/components/auth/access";
import { useSession } from "@/components/auth/session";
import { LoadingState } from "@/components/ui/RouteState";
import { ApiError, api, type HistoryConsole } from "@/lib/api";

import { DecisionBlock } from "./DecisionBlock";
import { ADMIN_PERMISSION, READ_PERMISSION } from "./historyFormat";
import { HistoryTable } from "./HistoryTable";

import "./history.css";

type Load =
  | { status: "loading" }
  | { status: "ok"; consoles: HistoryConsole[] }
  | { status: "forbidden" }
  | { status: "expired" }
  | { status: "error" };

export function HistoryBackfillPage() {
  const session = useSession();
  const [load, setLoad] = useState<Load>({ status: "loading" });
  const [now] = useState(() => new Date());

  const read = useCallback(async () => {
    setLoad({ status: "loading" });
    try {
      setLoad({ status: "ok", consoles: await api.historyBackfill() });
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) setLoad({ status: "forbidden" });
      else if (err instanceof ApiError && err.status === 401) setLoad({ status: "expired" });
      else setLoad({ status: "error" });
    }
  }, []);

  useEffect(() => {
    void read();
  }, [read]);

  const granted = session.status === "ready" && session.me ? session.me.permissions : [];
  const canAdmin = session.status === "ready" && holds(granted, ADMIN_PERMISSION);

  if (load.status === "loading") return <LoadingState what="the history backfill status" />;

  if (load.status === "forbidden") {
    return (
      <div className="notice" role="alert">
        <p>
          <strong>The history backfill could not be shown.</strong>
        </p>
        <p>
          Your role cannot read it (permission <code className="perm">{READ_PERMISSION}</code>).
          Nothing was changed.
        </p>
      </div>
    );
  }
  if (load.status === "expired") {
    return (
      <div className="notice" role="alert">
        <p>
          <strong>The history backfill could not be shown.</strong>
        </p>
        <p>Your session has expired. Sign in again to continue.</p>
      </div>
    );
  }
  if (load.status === "error") {
    return (
      <div className="notice" role="alert">
        <p>
          <strong>The history backfill could not be loaded.</strong>
        </p>
        <p>
          The backend may be unreachable. Nothing was changed. Check that it is running, then try
          again.
        </p>
        <p>
          <button type="button" className="action secondary" onClick={() => void read()}>
            Try again
          </button>
        </p>
      </div>
    );
  }
  if (load.consoles.length === 0) {
    return (
      <div className="notice" role="status">
        <p>
          <strong>No QRadar console is enabled.</strong>
        </p>
        <p>
          The history backfill is decided and read per enabled console, so there is nothing to
          show. Register and enable a console, then reload this page.
        </p>
      </div>
    );
  }

  const replace = (next: HistoryConsole) =>
    setLoad((current) =>
      current.status === "ok"
        ? {
            status: "ok",
            consoles: current.consoles.map((c) =>
              c.instance_id === next.instance_id ? next : c,
            ),
          }
        : current,
    );

  return (
    <>
      {load.consoles.map((console) => (
        <section key={console.instance_id} className="hb-console" aria-label={`Console ${console.name}`}>
          <h2 className="hb-console-name">{console.name}</h2>
          <DecisionBlock
            console={console}
            canAdmin={canAdmin}
            days={console.families[0]?.window.days_requested ?? null}
            onChange={replace}
          />
          <HistoryTable console={console} now={now} />
        </section>
      ))}
    </>
  );
}
