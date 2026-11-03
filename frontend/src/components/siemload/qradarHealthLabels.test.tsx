import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import en from "../../../messages/en";
import { familyName } from "@/components/catalog/derive";
import { familyInfo, FAMILY_INFO } from "@/lib/searchCadence";

import { caller, summary } from "./fixtures";
import { CallerSummary } from "./CallerSummary";

const mocks = vi.hoisted(() => ({ states: {} as Record<string, unknown> }));
vi.mock("@/hooks/useApiResource", () => ({
  useApiResource: (key: string) => mocks.states[key] ?? { status: "loading" },
}));

describe("the QRADAR_HEALTH search family has its English label wherever families are listed", () => {
  it("is named in the cadence table's family info", () => {
    expect(FAMILY_INFO.QRADAR_HEALTH).toEqual({
      name: "QRadar health",
      scope: "Disk, system, pipeline and notifications per QRadar host",
    });
    expect(familyInfo("QRADAR_HEALTH").name).toBe("QRadar health");
  });

  it("is named in the cadence, catalog and SIEM-load catalogs", () => {
    expect(en.cadence.family.QRADAR_HEALTH.name).toBe(FAMILY_INFO.QRADAR_HEALTH.name);
    expect(en.cadence.family.QRADAR_HEALTH.scope).toBe(FAMILY_INFO.QRADAR_HEALTH.scope);
    expect(en.catalog.family.QRADAR_HEALTH).toBe("QRadar health");
    expect(familyName("QRADAR_HEALTH")).toBe("QRadar health");
    expect(en.siemLoad.caller.QRADAR_HEALTH).toBe("QRadar health");
  });

  it("is shown by its label, never its key, in the per-caller summary", () => {
    mocks.states["/admin/ariel-load?hours=24"] = {
      status: "ok",
      data: summary({ callers: [caller({ caller: "QRADAR_HEALTH", searches: 192 })] }),
      receivedAt: "2026-11-02T10:15:04Z",
      refreshing: false,
      refreshFailed: null,
    };
    const { container } = render(<CallerSummary range="24h" />);
    expect(screen.getByRole("rowheader", { name: "QRadar health" })).toBeInTheDocument();
    expect(container.textContent ?? "").not.toMatch(/QRADAR_HEALTH/);
  });
});
