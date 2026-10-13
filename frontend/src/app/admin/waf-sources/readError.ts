// What a failed read of the WAF administration API says. Never the raw error:
// it can carry an upstream URL, and this text goes into the DOM.

import { ApiError } from "@/lib/api";

export function readError(err: unknown, what: string): string {
  if (err instanceof ApiError && err.status === 403) {
    return `Your role cannot read ${what} (permission admin:waf). Nothing was changed.`;
  }
  if (err instanceof ApiError && err.status === 401) {
    return "Your session has expired. Sign in again to continue.";
  }
  return `${what[0].toUpperCase()}${what.slice(1)} could not be loaded, so nothing is shown here. The backend may be unreachable; check that it is running, then reload the page. Nothing was changed.`;
}
