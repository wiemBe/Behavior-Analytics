import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { KNOWN_PERMISSIONS } from "./permissions";
import { WIDGETS, widgetDef } from "./registry";
import {
  BEHAVIORAL_ANALYTICS,
  LOG_SOURCE_MONITORING,
  QRADAR_ADMINISTRATION,
  TEMPLATES,
  WAF_IPS_MONITORING,
} from "./templates";
import { GRID_COLUMNS } from "./types";
import { deriveLayout, type GridColumns } from "./layout";

const apiSource = readFileSync(resolve(__dirname, "../lib/api.ts"), "utf8");
const rbacSource = readFileSync(resolve(__dirname, "../../../backend/app/security/rbac.py"), "utf8");

describe("widget registry", () => {
  it("has unique, well-formed types", () => {
    const types = WIDGETS.map((w) => w.type);
    expect(new Set(types).size).toBe(types.length);
    for (const type of types) expect(type).toMatch(/^[a-z0-9_.]{1,64}$/);
  });

  it("lists exactly the Phase 2 widgets, the catalog widgets and the one blocked one", () => {
    expect(WIDGETS.filter((w) => w.status === "live").map((w) => w.type)).toEqual([
      "kpi.total_eps",
      "kpi.log_sources",
      "kpi.offenses",
      "kpi.anomalies",
      "chart.eps_fleet_total",
      "chart.eps_by_class",
      "table.top_sources",
      "chart.anomaly_severity",
      "health.log_sources",
      "table.recent_anomalies",
      "table.recent_offenses",
      "health.detection",
      "health.collection",
      "health.evidence",
      "table.catchall_senders",
      "table.fleet_classes",
      "table.outage_policy",
      "waf.sources",
      "table.recent_waf_anomalies",
      "waf.endpoint_traffic",
      "chart.eps_observed_expected",
      "chart.search_results",
      "ips.source_anomalies",
    ]);
    expect(WIDGETS.filter((w) => w.status === "blocked").map((w) => w.type)).toEqual([
      "map.geography",
    ]);
  });

  it("reads only routes the frontend client actually calls", () => {
    for (const def of WIDGETS) {
      for (const source of def.sources) {
        // The client builds each path as a string or template literal that starts with it.
        const called = [`"${source}"`, `"${source}?`, `\`${source}\``, `\`${source}\${`].some((form) =>
          apiSource.includes(form),
        );
        expect(called, `${def.type} reads ${source}`).toBe(true);
      }
    }
  });

  it("requires only permissions the backend defines, and the list cannot drift from rbac.py", () => {
    const backend = [...rbacSource.matchAll(/^PERM_[A-Z_]+ = "([^"]+)"/gm)].map((m) => m[1]);
    expect([...KNOWN_PERMISSIONS].sort()).toEqual([...backend].sort());
    for (const def of WIDGETS) {
      for (const permission of def.requires) {
        expect(KNOWN_PERMISSIONS as readonly string[], def.type).toContain(permission);
      }
    }
  });

  it("gives a blocked widget a reason, a live one none, and keeps blocked ones out of templates", () => {
    for (const def of WIDGETS) {
      if (def.status === "blocked") {
        expect(def.blockedBy, def.type).toBeTruthy();
        expect(def.sources).toEqual([]);
      } else {
        expect(def.blockedBy, def.type).toBeUndefined();
        expect(def.sources.length, def.type).toBeGreaterThan(0);
        expect(def.requires.length, def.type).toBeGreaterThan(0);
      }
    }
    for (const template of TEMPLATES) {
      for (const placed of template.layout.widgets) {
        expect(widgetDef(placed.type)?.status, `${template.key}: ${placed.type}`).toBe("live");
      }
    }
  });

  it("states the blocked reasons the catalog shows", () => {
    expect(widgetDef("map.geography")?.blockedBy).toBe(
      "the map widget is not built yet; geo collection is",
    );
    // UI-3.5b: the IPS analyst API and its UI exist, so the IPS widget is no longer blocked.
    expect(widgetDef("ips.source_anomalies")?.status).toBe("live");
    expect(widgetDef("ips.source_anomalies")?.blockedBy).toBeUndefined();
  });

  it("keeps every default size inside its own bounds and the grid", () => {
    for (const { type, size } of WIDGETS) {
      expect(size.w, type).toBeGreaterThanOrEqual(size.minW);
      expect(size.h, type).toBeGreaterThanOrEqual(size.minH);
      expect(size.w, type).toBeLessThanOrEqual(size.maxW ?? GRID_COLUMNS);
      expect(size.h, type).toBeLessThanOrEqual(24);
    }
  });

  it("keeps every template valid: known live types, unique ids, inside the grid, no overlap", () => {
    for (const template of TEMPLATES) {
      expect(template.layout.v).toBe(1);
      const ws = template.layout.widgets;
      expect(ws.length).toBeLessThanOrEqual(24);
      expect(new Set(ws.map((w) => w.id)).size).toBe(ws.length);
      ws.forEach((a, i) => {
        expect(a.id).toMatch(/^w_[a-z0-9]{4,12}$/);
        expect(a.x + a.w).toBeLessThanOrEqual(GRID_COLUMNS);
        expect(widgetDef(a.type)?.status).toBe("live");
        for (const b of ws.slice(i + 1)) {
          const apart =
            a.x + a.w <= b.x || b.x + b.w <= a.x || a.y + a.h <= b.y || b.y + b.h <= a.y;
          expect(apart, `${a.type} overlaps ${b.type}`).toBe(true);
        }
      });
    }
  });

  it.each([
    [QRADAR_ADMINISTRATION.name, QRADAR_ADMINISTRATION],
    [BEHAVIORAL_ANALYTICS.name, BEHAVIORAL_ANALYTICS],
    [LOG_SOURCE_MONITORING.name, LOG_SOURCE_MONITORING],
    [WAF_IPS_MONITORING.name, WAF_IPS_MONITORING],
  ])("derives %s at 6 and 1 columns without overlap", (_name, template) => {
    for (const columns of [6, 1] as GridColumns[]) {
      const widgets = deriveLayout(template.layout.widgets, columns);
      widgets.forEach((widget, index) => {
        expect(widget.x + widget.w).toBeLessThanOrEqual(columns);
        for (const other of widgets.slice(index + 1)) {
          const apart =
            widget.x + widget.w <= other.x ||
            other.x + other.w <= widget.x ||
            widget.y + widget.h <= other.y ||
            other.y + other.h <= widget.y;
          expect(apart, `${template.key}/${columns}: ${widget.type} overlaps ${other.type}`).toBe(
            true,
          );
        }
      });
    }
  });
});

// T-098: at 1440 and 1280 the 13-row "Source classes" table overflowed its panel by 16-17 px
// and scrolled inside it, so the scrollbar covered its last column header. One more grid row
// is 36 px (24 px row, 12 px gap); the templates give the tables the room they measured.
describe("templates give their tables the rows they measured", () => {
  const sized = (template: { layout: { widgets: Array<{ type: string; h: number }> } }, type: string) =>
    template.layout.widgets.find((w) => w.type === type)?.h ?? 0;

  it("QRadar Administration: Source classes is 14 rows or more, and nothing below it overlaps", () => {
    expect(sized(QRADAR_ADMINISTRATION, "table.fleet_classes")).toBeGreaterThanOrEqual(14);
    const classes = QRADAR_ADMINISTRATION.layout.widgets.find((w) => w.type === "table.fleet_classes")!;
    const policy = QRADAR_ADMINISTRATION.layout.widgets.find((w) => w.type === "table.outage_policy")!;
    expect(policy.y).toBeGreaterThanOrEqual(classes.y + classes.h);
  });

  it("Log Source Monitoring: Source classes is 14 rows or more and Top sources 16 or more", () => {
    expect(sized(LOG_SOURCE_MONITORING, "table.fleet_classes")).toBeGreaterThanOrEqual(14);
    expect(sized(LOG_SOURCE_MONITORING, "table.top_sources")).toBeGreaterThanOrEqual(16);
    const top = LOG_SOURCE_MONITORING.layout.widgets.filter((w) => w.y === 0);
    const lower = LOG_SOURCE_MONITORING.layout.widgets.filter((w) => w.y > 0);
    for (const upper of top) for (const w of lower) expect(w.y).toBeGreaterThanOrEqual(upper.h);
  });
});
