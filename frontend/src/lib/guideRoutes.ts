// Which guide section each page header links to: the one place a route pattern meets a section
// id. A page asks `guideFor(pattern)`; no page spells a section id. The patterns are the
// `src/app/**/page.tsx` paths with the route-group-free folder names, `[param]` kept.
//
// `guideRoutes.contract.test.ts` fails when a `page.tsx` has neither an entry here nor an entry in
// `GUIDE_EXCLUDED`, and when an entry names no page or no section.

export const GUIDE_ROUTES = {
  "/": "dashboard",
  "/behavior": "behavior",
  "/anomalies": "anomalies",
  "/anomalies/[id]": "anomaly-detail",
  "/behavior/sources": "source-behavior",
  "/behavior/sources/[id]": "source-behavior",
  "/behavior/waf": "waf-behavior",
  "/behavior/waf/sources/[id]": "waf-behavior",
  "/behavior/watches": "property-watches",
  "/behavior/watches/[id]": "property-watches",
  "/behavior/watches/new": "property-watch-setup",
  "/fleet": "fleet",
  "/fleet/sources": "fleet-sources",
  "/fleet/catchall": "catch-all-senders",
  "/offenses": "offenses",
  "/offenses/[id]": "offenses",
  "/alerts": "alerts",
  "/alerts/[id]": "alerts",
  "/rules": "rules",
  "/rules/[id]": "rules",
  "/rules/statistics": "rule-statistics",
  "/custom-rules": "custom-rules",
  "/custom-rules/[id]": "custom-rules",
  "/custom-rules/new": "custom-rule-setup",
  "/coverage": "coverage",
  "/log-sources": "log-sources",
  "/log-sources/[id]": "log-sources",
  "/searches": "searches",
  "/searches/[id]": "searches",
  "/searches/new": "searches",
  "/config-changes": "config-changes",
  "/account": "account",
  "/admin/waf-sources": "waf-source-setup",
  "/admin/waf-sources/[logSourceId]": "waf-source-setup",
  "/admin/ips-sources": "ips-source-setup",
  "/admin/ips-sources/[logSourceId]": "ips-source-setup",
  "/admin/outage-policy": "outage-policy",
  "/admin/classification-rules": "classification-rules",
  "/admin/search-cadence": "search-cadence",
  "/admin/history-backfill": "history-backfill",
  "/admin/users": "users-and-directory",
  "/admin/directory": "users-and-directory",
} as const;

export type GuideRoute = keyof typeof GUIDE_ROUTES;

/** Pages with no page-header link, each with the reason. */
export const GUIDE_EXCLUDED: Readonly<Record<string, string>> = {
  "/login": "Signed-out page: it renders before the shell, and the Guide needs a session.",
  "/admin": "The administration index: the route map gives it no section.",
  "/guide": "The Guide's own index.",
  "/guide/[section]": "A Guide section: it is the destination of the links.",
  "/account/password": "Omitted by the route map: the password change belongs to the Account section.",
};

/** The section id for a page's route pattern. A pattern that is not in the table fails to compile. */
export function guideFor(route: GuideRoute): string {
  return GUIDE_ROUTES[route];
}
