// Two reads that one card needs, folded into one state.
//
// Precedence is the honest one: if either read is refused, the card says the role
// cannot read it; else if either failed, it failed; else if either is still
// loading, it is loading. Only when both are in is the card `ok`, and then a
// failed refresh of either one is reported, with the older of the two receive
// times (the card shows data no newer than that).
import type { ResourceState } from "@/hooks/useApiResource";

export function combine2<A, B>(
  a: ResourceState<A>,
  b: ResourceState<B>,
): ResourceState<[A, B]> {
  for (const s of [a, b]) if (s.status === "forbidden") return s;
  for (const s of [a, b]) if (s.status === "error") return s;
  if (a.status !== "ok" || b.status !== "ok") {
    return a.status === "idle" && b.status === "idle" ? { status: "idle" } : { status: "loading" };
  }
  return {
    status: "ok",
    data: [a.data, b.data],
    receivedAt: a.receivedAt < b.receivedAt ? a.receivedAt : b.receivedAt,
    refreshing: a.refreshing || b.refreshing,
    refreshFailed: a.refreshFailed ?? b.refreshFailed,
  };
}
