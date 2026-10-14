// Shared builders for the KPI card tests: a `ResourceState` of each kind.
import type { ResourceState } from "@/hooks/useApiResource";

export const RECEIVED = "2026-10-14T13:55:00Z";

export function ok<T>(
  data: T,
  over: Partial<Extract<ResourceState<T>, { status: "ok" }>> = {},
): ResourceState<T> {
  return { status: "ok", data, receivedAt: RECEIVED, refreshing: false, refreshFailed: null, ...over };
}

export const loading: ResourceState<never> = { status: "loading" };
export const forbidden: ResourceState<never> = { status: "forbidden", httpStatus: 403 };
export const failed = (httpStatus: number | null = 502): ResourceState<never> => ({
  status: "error",
  httpStatus,
});
