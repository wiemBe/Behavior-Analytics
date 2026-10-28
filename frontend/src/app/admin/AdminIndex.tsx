"use client";

// The hub lists every administration surface. Users and Directory appear only
// once the session is known and the caller holds the permission. Until then
// they stay hidden: a link the account cannot open is not a destination.

import Link from "next/link";
import { useTranslations } from "next-intl";

import { holds } from "@/components/auth/access";
import { useSession } from "@/components/auth/session";

interface Section {
  /** The key under `admin.index` that holds the card's name and one-line purpose. */
  key: string;
  href: string;
  permission?: string;
}

const SECTIONS: readonly Section[] = [
  { key: "searchCadence", href: "/admin/search-cadence" },
  { key: "classification", href: "/admin/classification-rules" },
  { key: "outage", href: "/admin/outage-policy" },
  { key: "waf", href: "/admin/waf-sources" },
  { key: "ips", href: "/admin/ips-sources" },
  { key: "history", href: "/admin/history-backfill", permission: "read:history_backfill" },
  { key: "users", href: "/admin/users", permission: "admin:users" },
  { key: "directory", href: "/admin/directory", permission: "admin:auth" },
];

export function AdminIndex() {
  const t = useTranslations("admin.index");
  const session = useSession();
  const granted = session.status === "ready" && session.me ? session.me.permissions : [];
  const visible = SECTIONS.filter(
    (section) =>
      section.permission == null ||
      (session.status === "ready" && holds(granted, section.permission)),
  );

  return (
    <ul className="admin-links" aria-label={t("label")}>
      {visible.map((section) => (
        <li key={section.href}>
          <Link href={section.href}>{t(`${section.key}.name` as "users.name")}</Link>
          <span className="cell-sub">{t(`${section.key}.description` as "users.description")}</span>
        </li>
      ))}
    </ul>
  );
}
