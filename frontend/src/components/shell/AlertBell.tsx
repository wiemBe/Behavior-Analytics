"use client";

// The open-alert count, as a text link to the alerts list.
//
// The number is `alerts.open` from the overview. When that read failed or was
// refused there is no count to show, and the control says so: it never shows 0,
// because "0 open alerts" is a clean bill of health the platform has not given.
// The `crit` tone appears only for a count above zero.

import Link from "next/link";

import type { Loaded } from "./TopBar";
import type { SocOverview } from "@/lib/api";

export function AlertBell({ overview }: { overview: Loaded<SocOverview> }) {
  const count = overview.state === "ok" ? overview.data.alerts.open : null;
  const name =
    count !== null
      ? `Open alerts: ${count}`
      : overview.state === "loading"
        ? "Open alerts: loading"
        : "Open alerts: unavailable";
  const tone = count !== null && count > 0 ? "crit" : "";

  return (
    <Link
      href="/alerts"
      className="topbar-bell plain"
      aria-label={name}
      title={count === null && overview.state !== "loading" ? "Alert count unavailable" : undefined}
    >
      <span aria-hidden="true">Alerts</span>
      <span className={`pill ${tone}`.trim()} aria-hidden="true">
        {count === null ? "—" : count}
      </span>
    </Link>
  );
}
