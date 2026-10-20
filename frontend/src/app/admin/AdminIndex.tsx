"use client";

// The hub lists every administration surface. Users and Directory appear only
// once the session is known and the caller holds the permission. Until then
// they stay hidden: a link the account cannot open is not a destination.

import Link from "next/link";

import { holds } from "@/components/auth/access";
import { useSession } from "@/components/auth/session";

interface Section {
  name: string;
  href: string;
  description: string;
  permission?: string;
}

const SECTIONS: readonly Section[] = [
  {
    name: "Search cadence",
    href: "/admin/search-cadence",
    description: "How often each search family queries QRadar",
  },
  {
    name: "Classification rules",
    href: "/admin/classification-rules",
    description: "Which rules decide a log source's class, and in what order",
  },
  {
    name: "Outage alerts",
    href: "/admin/outage-policy",
    description: "Which silent sources raise an alert, and which are only recorded",
  },
  {
    name: "WAF sources",
    href: "/admin/waf-sources",
    description: "Which QRadar properties each WAF source is read from",
  },
  {
    name: "IPS sources",
    href: "/admin/ips-sources",
    description: "Which QRadar properties each IPS source is read from",
  },
  {
    name: "History backfill",
    href: "/admin/history-backfill",
    description: "What has been read from QRadar's past for the baselines, and what can still be",
    permission: "read:history_backfill",
  },
  {
    name: "Users",
    href: "/admin/users",
    description: "Who can sign in, and the roles an administrator assigned",
    permission: "admin:users",
  },
  {
    name: "Directory",
    href: "/admin/directory",
    description: "Where directory accounts bind, and a connection test that stores nothing",
    permission: "admin:auth",
  },
];

export function AdminIndex() {
  const session = useSession();
  const granted = session.status === "ready" && session.me ? session.me.permissions : [];
  const visible = SECTIONS.filter(
    (section) =>
      section.permission == null ||
      (session.status === "ready" && holds(granted, section.permission)),
  );

  return (
    <ul className="admin-links" aria-label="Administration sections">
      {visible.map((section) => (
        <li key={section.href}>
          <Link href={section.href}>{section.name}</Link>
          <span className="cell-sub">{section.description}</span>
        </li>
      ))}
    </ul>
  );
}
