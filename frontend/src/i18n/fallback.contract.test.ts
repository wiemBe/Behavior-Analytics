import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import en from "../../messages/en";
import { englishText as authEnglish } from "@/components/auth/englishText";
import { englishText as shellEnglish } from "@/components/shell/englishText";
import { englishText as dashboardEnglish } from "@/widgets/englishText";

const SRC = path.resolve(__dirname, "..");

function sourceFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) return sourceFiles(full);
    return /\.tsx?$/.test(entry.name) ? [full] : [];
  });
}

describe("i18n/fallback is gone", () => {
  it("is imported by no file, tests included, and no longer exists", () => {
    const importers = sourceFiles(SRC)
      .filter((file) => !file.endsWith("fallback.contract.test.ts"))
      .filter((file) => /(?:from|import)\s*\(?\s*["'](@\/i18n\/fallback|\.{1,2}\/(?:.*\/)?fallback)["']/.test(readFileSync(file, "utf8")))
      .map((file) => path.relative(SRC, file));
    expect(importers).toEqual([]);
    expect(sourceFiles(path.join(SRC, "i18n")).map((file) => path.basename(file))).not.toContain(
      "fallback.ts",
    );
  });
});

describe("area English bundles (the default translators of the converted helpers)", () => {
  it("auth holds the same text as the catalog", () => {
    expect(authEnglish("auth.probeOk")).toBe(en.auth.probeOk);
    expect(authEnglish("auth.requestFailed")).toBe(en.auth.requestFailed);
    expect(authEnglish("common.unknown")).toBe(en.common.unknown);
  });

  it("shell holds the same text as the catalog", () => {
    expect(shellEnglish("shell.connection.connected")).toBe(en.shell.connection.connected);
    expect(shellEnglish("format.minutesAgo", { count: 5 })).toBe("5 min ago");
    expect(shellEnglish("common.ok")).toBe(en.common.ok);
  });

  it("widgets holds the same text as the catalog", () => {
    expect(dashboardEnglish("widgets.menu.nameRequired")).toBe(en.widgets.menu.nameRequired);
    expect(dashboardEnglish("home.frame.gapFailed")).toBe(en.home.frame.gapFailed);
    expect(dashboardEnglish("units.eps")).toBe("EPS");
    expect(dashboardEnglish("dashboard.notFinished")).toBe(en.dashboard.notFinished);
    expect(dashboardEnglish("enums.severity.HIGH")).toBe("High");
  });

  it("each bundle covers every namespace its helper reads", () => {
    const namespacesUsed = (relative: string) => {
      const found = new Set<string>();
      const source = readFileSync(path.join(SRC, relative), "utf8");
      for (const match of source.matchAll(/\bt\(\s*[`"']([A-Za-z]+)\./g)) found.add(match[1]);
      return [...found];
    };
    for (const file of ["components/auth/probeCopy.ts", "components/auth/reasons.ts", "components/auth/accountStatus.ts"]) {
      for (const namespace of namespacesUsed(file)) {
        expect(["auth", "common"], `${file} reads ${namespace}`).toContain(namespace);
      }
    }
    for (const namespace of namespacesUsed("components/shell/ConnectionStatus.tsx")) {
      expect(["common", "format", "shell"], `ConnectionStatus reads ${namespace}`).toContain(namespace);
    }
  });
});
