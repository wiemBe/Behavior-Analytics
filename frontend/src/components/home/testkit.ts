// Shared scaffolding for the Home table widget tests: the data hook is replaced by a
// table of canned states keyed by request path (each test file declares the mock itself,
// because `vi.mock` is hoisted above anything imported), so each test names only the states
// it is about.

import type { ResourceState } from "@/hooks/useApiResource";

export function ok<T>(
  data: T,
  over: Partial<Extract<ResourceState<T>, { status: "ok" }>> = {},
): ResourceState<T> {
  return {
    status: "ok",
    data,
    receivedAt: "2026-10-14T13:58:00Z",
    refreshing: false,
    refreshFailed: null,
    ...over,
  };
}

export const loading: ResourceState<never> = { status: "loading" };
export const forbidden: ResourceState<never> = { status: "forbidden", httpStatus: 403 };
export const failed = (httpStatus: number | null = 500): ResourceState<never> => ({
  status: "error",
  httpStatus,
});
