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
import { useEffect, useRef, useState } from "react";

import { NAV, activeNavHref, type NavGroup } from "@/lib/nav";

// Which labeled groups the analyst opened or closed, remembered per browser. Only an explicit
// choice is stored; a group never chosen is closed, except that Behavior, the product's purpose,
// starts open on a page no labeled group owns (the Dashboard). The group that holds the current
// page is open whatever is stored, so the position in the site is never hidden.
const STORAGE_KEY = "qradar-dash.nav.groups";
const DEFAULT_OPEN = "Behavior";

function readChoices(): Record<string, boolean> {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : null;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    const out: Record<string, boolean> = {};
    for (const [k, v] of Object.entries(parsed)) if (typeof v === "boolean") out[k] = v;
    return out;
  } catch {
    return {};
  }
}

function writeChoices(choices: Record<string, boolean>): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(choices));
  } catch {
    // Storage blocked: the choice lasts until the page is reloaded.
  }
}

export function AppNav({ groups = NAV }: { groups?: NavGroup[] }) {
  const t = useTranslations("shell");
  const nav = useTranslations("shell.nav");
  const pathname = usePathname() ?? "/";
  const active = activeNavHref(pathname, groups);
  const currentInLabeled = groups.some(
    (g) => g.label !== null && g.items.some((item) => item.href === active),
  );
  const navRef = useRef<HTMLElement>(null);
  // Empty on the server and on the first client render, so both render the defaults; the
  // remembered choices are applied after mount.
  const [choices, setChoices] = useState<Record<string, boolean>>({});
  useEffect(() => {
    setChoices(readChoices());
  }, []);
  const toggle = (label: string, open: boolean) => {
    const next = { ...choices, [label]: !open };
    setChoices(next);
    writeChoices(next);
  };

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
      {groups.map((group, i) => {
        const holdsCurrent = group.items.some((item) => item.href === active);
        const open =
          !group.label ||
          holdsCurrent ||
          (choices[group.label] ?? (group.label === DEFAULT_OPEN && !currentInLabeled));
        return (
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
              {/* A disclosure button. The group that holds the current page cannot be closed,
                  and says so with aria-disabled rather than the disabled attribute, which
                  would take it out of the tab order. */}
              <button
                type="button"
                className="nav-group-toggle"
                aria-expanded={open}
                aria-controls={`nav-list-${i}`}
                aria-disabled={holdsCurrent ? true : undefined}
                onClick={() => {
                  if (!holdsCurrent) toggle(group.label as string, open);
                }}
              >
                <span>{groupLabel(group.label, nav)}</span>
                <span className="nav-group-state" aria-hidden="true">
                  {holdsCurrent ? "" : open ? "\u2212" : "+"}
                </span>
              </button>
            </div>
          )}
          <ul
            className="nav-list"
            id={`nav-list-${i}`}
            inert={!open}
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
        );
      })}
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
    case "/queue": return t("queue");
    case "/handover": return t("handover");
    case "/reports": return t("reports");
    case "/anomalies": return t("anomalies");
    case "/behavior/sources": return t("sources");
    case "/behavior/waf": return t("wafBehavior");
    case "/behavior/ips": return t("ipsBehavior");
    case "/behavior/traffic": return t("traffic");
    case "/behavior/noise": return t("noisySources");
    case "/behavior/watches": return t("propertyWatches");
    case "/geography": return t("geography");
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
    case "/admin/alert-policy": return t("alertPolicy");
    case "/admin/waf-sources": return t("wafSources");
    case "/admin/ips-sources": return t("ipsSources");
    case "/admin/history-backfill": return t("historyBackfill");
    case "/admin/siem-load": return t("siemLoad");
    case "/admin/qradar-access": return t("qradarAccess");
    case "/admin/email": return t("alertEmail");
    case "/admin/users": return t("users");
    case "/admin/directory": return t("directory");
    case "/guide": return t("guide");
    default: return fallback;
  }
}
