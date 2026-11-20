// Tests only: a ready session holding exactly these permissions, for a notice that links
// Administration → Features only to an account that may open it.

import { vi } from "vitest";

import type { SessionValue } from "@/components/auth/session";
import type { CallerInfo } from "@/lib/api";

export function sessionHolding(permissions: string[]): SessionValue {
  const me = {
    subject: "ada",
    email: null,
    permissions,
    effective_permissions: permissions,
    username: "ada",
    auth_source: "LOCAL",
    must_change_password: false,
    roles: [],
  } as CallerInfo;
  return { status: "ready", me, error: null, reload: vi.fn(), signOut: vi.fn() };
}
