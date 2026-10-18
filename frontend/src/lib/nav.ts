// The application's navigation model.
//
// Grouped, not one flat list. The Dashboard is the cross-cutting home and sits
// alone at the top. Behavioral analytics is what the platform is for —
// detecting that a log source changed, and explaining what changed — so the
// Behavior group leads the labeled groups and carries full visual weight.
// Fleet, detection, operations and administration are supporting capabilities:
// reachable, but not competing with the product's purpose.
//
// Only routes that exist appear here. A navigation entry is a promise that
// something is on the other side of it, and a disabled or placeholder item
// spends the analyst's attention to deliver nothing.

export interface NavItem {
  href: string;
  label: string;
  /** When set, the entry is shown only to a caller who holds this permission. */
  permission?: string;
}

export interface NavGroup {
  /** Rendered as a group heading. Null for the primary group, which needs no
   *  label: it is the top of the list. */
  label: string | null;
  items: NavItem[];
}

export const NAV: NavGroup[] = [
  {
    label: null,
    items: [
      // The former "SOC Overview", renamed. The route is unchanged, so
      // bookmarks keep working.
      { href: "/", label: "Dashboard" },
    ],
  },
  {
    label: "Behavior",
    items: [
      { href: "/behavior", label: "Overview" },
      { href: "/anomalies", label: "Anomalies" },
      // Listed after /behavior, and resolved by longest match rather than by
      // order: /behavior/sources and /behavior/sources/{id} belong to Sources,
      // not to the Overview whose path is their prefix.
      { href: "/behavior/sources", label: "Sources" },
      // Under /behavior like Sources, so it too wins by longest match.
      { href: "/behavior/waf", label: "WAF behavior" },
      // Under /behavior like Sources and WAF, so it too wins by longest match.
      { href: "/behavior/watches", label: "Property watches" },
    ],
  },
  {
    label: "Fleet",
    items: [
      { href: "/fleet", label: "Fleet" },
      // Resolved by longest match, like /behavior/sources: a path under
      // /fleet/sources belongs here, not to Fleet.
      { href: "/fleet/sources", label: "Fleet sources" },
      { href: "/fleet/catchall", label: "Catch-all senders" },
    ],
  },
  {
    label: "Detection",
    items: [
      { href: "/offenses", label: "Offenses" },
      { href: "/alerts", label: "Alerts" },
      { href: "/rules", label: "Rules" },
      { href: "/rules/statistics", label: "Rule statistics" },
      { href: "/custom-rules", label: "Custom rules" },
      { href: "/coverage", label: "Coverage" },
    ],
  },
  {
    label: "Operations",
    items: [
      { href: "/log-sources", label: "Log Sources" },
      { href: "/searches", label: "Searches" },
    ],
  },
  {
    label: "Administration",
    items: [
      { href: "/admin/search-cadence", label: "Search cadence" },
      { href: "/admin/classification-rules", label: "Classification rules" },
      { href: "/admin/outage-policy", label: "Outage alerts" },
      { href: "/admin/waf-sources", label: "WAF sources" },
      { href: "/admin/ips-sources", label: "IPS sources" },
      { href: "/admin/users", label: "Users", permission: "admin:users" },
      { href: "/admin/directory", label: "Directory", permission: "admin:auth" },
    ],
  },
];

/**
 * Whether a path falls under a nav entry.
 *
 * A detail route belongs to the section that owns it, so `/anomalies/{id}`
 * matches `/anomalies`. The root is matched exactly and never by prefix —
 * otherwise "/" would claim every route on the site.
 */
export function navItemMatches(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(`${href}/`);
}

/**
 * The single nav entry that should be marked current for a path.
 *
 * Longest match wins, which is what makes nested sections work: with both
 * `/behavior` and `/behavior/sources` in the nav, the path
 * `/behavior/sources/{id}` resolves to `/behavior/sources` rather than
 * lighting up both. Returns null for a path no entry owns, so nothing is
 * marked current rather than something arbitrary being marked.
 */
export function activeNavHref(
  pathname: string,
  groups: NavGroup[] = NAV,
): string | null {
  let best: string | null = null;
  for (const group of groups) {
    for (const item of group.items) {
      if (!navItemMatches(pathname, item.href)) continue;
      if (best === null || item.href.length > best.length) best = item.href;
    }
  }
  return best;
}
