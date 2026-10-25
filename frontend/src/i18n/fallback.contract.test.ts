import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import en from "../../messages/en";
import { SHARED_ENGLISH } from "./fallback";
import { createTextTranslator } from "./textTranslator";
import { englishText as dashboardEnglish } from "@/widgets/englishText";

const SRC = path.resolve(__dirname, "..");
const read = (relative: string) => readFileSync(path.join(SRC, relative), "utf8");

/** The namespaces a source file asks a translator for, from `t("ns.key")` and t(`ns.${..}`). */
function namespacesUsed(source: string): Set<string> {
  const found = new Set<string>();
  for (const match of source.matchAll(/\bt\(\s*[`"']([A-Za-z]+)\./g)) found.add(match[1]);
  return found;
}

describe("i18n/fallback", () => {
  const callers = [
    "components/auth/probeCopy.ts",
    "components/auth/reasons.ts",
    "components/auth/accountStatus.ts",
    "components/shell/ConnectionStatus.tsx",
  ];

  it("holds the English of every namespace its remaining callers read, and no other", () => {
    expect(Object.keys(SHARED_ENGLISH).sort()).toEqual(["auth", "common", "format", "shell"]);
    for (const caller of callers) {
      for (const namespace of namespacesUsed(read(caller))) {
        expect(Object.keys(SHARED_ENGLISH), `${caller} reads ${namespace}`).toContain(namespace);
      }
    }
  });

  it("holds the same text as the catalog for those namespaces", () => {
    const shared = createTextTranslator("en", SHARED_ENGLISH);
    expect(shared("auth.probeOk")).toBe(en.auth.probeOk);
    expect(shared("shell.connection.connected")).toBe(en.shell.connection.connected);
    expect(shared("format.minutesAgo", { count: 5 })).toBe("5 min ago");
    expect(shared("common.unknown")).toBe(en.common.unknown);
  });

  it("is not imported by any converted Dashboard file", () => {
    for (const file of [
      "app/page.tsx",
      "components/home/HomeDashboard.tsx",
      "components/home/WidgetFrame.tsx",
      "components/home/qualifierText.ts",
      "components/home/chartOptions.ts",
      "widgets/DashboardView.tsx",
      "widgets/DashboardMenu.tsx",
      "widgets/Catalog.tsx",
      "widgets/Configure.tsx",
      "widgets/PropertyWatchWidget.tsx",
      "widgets/pwWidgetState.ts",
      "widgets/englishText.ts",
    ]) {
      expect(read(file), file).not.toMatch(/from "@\/i18n\/fallback"/);
    }
  });
});

describe("widgets/englishText", () => {
  it("is the English of the Dashboard area, the same text as the catalog", () => {
    expect(dashboardEnglish("widgets.menu.nameRequired")).toBe(en.widgets.menu.nameRequired);
    expect(dashboardEnglish("home.frame.gapFailed")).toBe(en.home.frame.gapFailed);
    expect(dashboardEnglish("units.eps")).toBe("EPS");
    expect(dashboardEnglish("dashboard.notFinished")).toBe(en.dashboard.notFinished);
    expect(dashboardEnglish("enums.severity.HIGH")).toBe("High");
  });
});
