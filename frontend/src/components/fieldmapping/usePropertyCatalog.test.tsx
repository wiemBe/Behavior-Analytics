import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { usePropertyCatalog } from "./usePropertyCatalog";
import { catalog } from "./fixtures";

afterEach(() => vi.useRealTimers());
describe("catalog refresh polling", () => {
  it("polls a queued refresh and stops on completion", async () => {
    vi.useFakeTimers();
    const read = vi.fn().mockResolvedValue(catalog({ catalog_status: "COMPLETE" }));
    const { result } = renderHook(() => usePropertyCatalog(catalog({ catalog_status: "REFRESHING" }), read));
    await act(() => vi.advanceTimersByTimeAsync(2000));
    expect(result.current[0]?.catalog_status).toBe("COMPLETE");
    await act(() => vi.advanceTimersByTimeAsync(60000));
    expect(read).toHaveBeenCalledOnce();
  });
  it("bounds repeated refresh reads and errors, and cancels on unmount", async () => {
    vi.useFakeTimers();
    const read = vi.fn().mockRejectedValue(new Error("unavailable"));
    const { unmount } = renderHook(() => usePropertyCatalog(catalog({ catalog_status: "REFRESHING" }), read));
    await act(() => vi.advanceTimersByTimeAsync(120000));
    expect(read).toHaveBeenCalledTimes(30);
    unmount();
    await act(() => vi.advanceTimersByTimeAsync(60000));
    expect(read).toHaveBeenCalledTimes(30);
  });
  it("does not poll a complete snapshot", async () => {
    vi.useFakeTimers();
    const read = vi.fn();
    renderHook(() => usePropertyCatalog(catalog({ catalog_status: "COMPLETE" }), read));
    await act(() => vi.advanceTimersByTimeAsync(60000));
    expect(read).not.toHaveBeenCalled();
  });
});
