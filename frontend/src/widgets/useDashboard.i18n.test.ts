// The editing hook's own sentences in Turkish: what it appends when a save or a reload fails.
// The refusal sentence in front of them is the server's or `describeRefusal`'s, and stays as sent.

import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api";
import { setTestLocale } from "../../vitest.setup";

import { SOC_OVERVIEW } from "./templates";
import { useDashboard } from "./useDashboard";

const api = vi.hoisted(() => ({
  createDashboard: vi.fn(),
  getDashboard: vi.fn(),
  replaceDashboard: vi.fn(),
  setDefaultDashboard: vi.fn(),
}));
vi.mock("@/lib/dashboardApi", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/dashboardApi")>()),
  ...api,
}));

beforeEach(() => {
  setTestLocale("tr");
  for (const fn of Object.values(api)) fn.mockReset();
});

const options = { setDefaultOnCreate: false };

describe("useDashboard messages in Turkish", () => {
  it("keeps the server's sentence and says in Turkish that the work is still there", async () => {
    api.createDashboard.mockRejectedValue(new ApiError(500, "Veritabanı hatası", "Database is down."));
    const { result } = renderHook(() =>
      useDashboard({ kind: "template", template: SOC_OVERVIEW }, SOC_OVERVIEW.layout, options),
    );
    await act(async () => {
      await result.current.save();
    });
    expect(result.current.state.saveState).toBe("error");
    expect(result.current.state.message).toMatch(/Kaydedilemedi; değişiklikleriniz burada duruyor\.$/);
  });

  it("says a failed copy keeps the work as well", async () => {
    api.createDashboard.mockRejectedValue(new ApiError(500, "x"));
    const { result } = renderHook(() =>
      useDashboard({ kind: "template", template: SOC_OVERVIEW }, SOC_OVERVIEW.layout, options),
    );
    await act(async () => {
      await result.current.saveAsCopy("Kopya");
    });
    expect(result.current.state.message).toMatch(/Kaydedilemedi; değişiklikleriniz burada duruyor\.$/);
  });

  it("says in Turkish that a failed reload leaves the unsaved changes", async () => {
    api.getDashboard.mockRejectedValue(new ApiError(500, "x"));
    const saved = {
      id: "d1", name: "Gece", description: null, owner_subject: "me", visibility: "PRIVATE",
      template_key: null, template_version: null, schema_version: 1, layout: SOC_OVERVIEW.layout,
      revision: 1, created_at: "2026-10-15T10:00:00Z", updated_at: "2026-10-15T10:00:00Z",
      updated_by: "me", is_default: false,
    };
    const { result } = renderHook(() =>
      useDashboard({ kind: "saved", dashboard: saved as never }, SOC_OVERVIEW.layout, options),
    );
    await act(async () => {
      await result.current.reload();
    });
    expect(result.current.state.message).toMatch(/Kaydedilmemiş değişiklikleriniz burada duruyor\.$/);
  });
});
