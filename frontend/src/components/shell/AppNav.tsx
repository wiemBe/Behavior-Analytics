"use client";

// The shell's navigation.
//
// A client component for one reason: the active route is only knowable from
// the current path, and `usePathname` is a client hook. It renders no data of
// its own, so nothing else about the shell needs to leave the server.
//
// The current entry is marked with `aria-current="page"` as well as with the
// active styling, so the position in the site is announced rather than only
// shown.

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTranslations } from "next-intl";
import { useEffect, useRef } from "react";

import { NAV, activeNavHref, type NavGroup } from "@/lib/nav";

export function AppNav({ groups = NAV }: { groups?: NavGroup[] }) {
  const t = useTranslations("shell");
  const nav = useTranslations("shell.nav");
  const pathname = usePathname() ?? "/";
  const active = activeNavHref(pathname, groups);
  const navRef = useRef<HTMLElement>(null);

  // The sidebar scrolls inside itself on a short viewport, so the current
  // entry may start off screen. `block: "nearest"` moves only the sidebar's own
  // scroll position, and only when the entry is not already visible; the page
  // never scrolls because of it. Guarded because jsdom has no scrollIntoView.
  useEffect(() => {
    const link = navRef.current?.querySelector<HTMLElement>('[aria-current="page"]');
    if (link && typeof link.scrollIntoView === "function") {
      link.scrollIntoView({ block: "nearest" });
    }
  }, [pathname]);

  return (
    <nav aria-label={t("primaryNavigation")} ref={navRef}>
      {groups.map((group, i) => (
        <div
          key={group.label ?? `primary-${i}`}
          className={`nav-group${group.label ? " nav-group-supporting" : ""}`}
        >
          {/* Not a heading element. The shell sits before the page's <h1> in
              document order, so a heading here would put the document's first
              heading in the sidebar and break the page's outline. The label is
              still announced, via `aria-labelledby` on the list below. */}
          {group.label && (
            <div className="nav-group-label" id={`nav-group-${i}`}>
              {groupLabel(group.label, nav)}
            </div>
          )}
          <ul
            className="nav-list"
            aria-labelledby={group.label ? `nav-group-${i}` : undefined}
          >
            {group.items.map((item) => (
              <li key={item.href}>
                <Link
                  href={item.href}
                  className="nav-link plain"
                  aria-current={item.href === active ? "page" : undefined}
                >
                  {itemLabel(item.href, item.label, nav)}
                </Link>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </nav>
  );
}

type NavTranslator = ReturnType<typeof useTranslations<"shell.nav">>;

function groupLabel(label: string, t: NavTranslator): string {
  switch (label) {
    case "Behavior": return t("behavior");
    case "Fleet": return t("fleetGroup");
    case "Detection": return t("detection");
    case "Operations": return t("operations");
    case "Administration": return t("administration");
    default: return label;
  }
}

function itemLabel(href: string, fallback: string, t: NavTranslator): string {
  switch (href) {
    case "/": return t("dashboard");
    case "/behavior": return t("overview");
    case "/anomalies": return t("anomalies");
    case "/behavior/sources": return t("sources");
    case "/behavior/waf": return t("wafBehavior");
    case "/behavior/watches": return t("propertyWatches");
    case "/fleet": return t("fleet");
    case "/fleet/sources": return t("fleetSources");
    case "/fleet/catchall": return t("catchAllSenders");
    case "/offenses": return t("offenses");
    case "/alerts": return t("alerts");
    case "/rules": return t("rules");
    case "/rules/statistics": return t("ruleStatistics");
    case "/custom-rules": return t("customRules");
    case "/coverage": return t("coverage");
    case "/log-sources": return t("logSources");
    case "/searches": return t("searches");
    case "/admin": return t("adminOverview");
    case "/admin/search-cadence": return t("searchCadence");
    case "/admin/classification-rules": return t("classificationRules");
    case "/admin/outage-policy": return t("outageAlerts");
    case "/admin/waf-sources": return t("wafSources");
    case "/admin/ips-sources": return t("ipsSources");
    case "/admin/history-backfill": return t("historyBackfill");
    case "/admin/users": return t("users");
    case "/admin/directory": return t("directory");
    case "/guide": return t("guide");
    default: return fallback;
  }
}
