// The same wildcard rule as `Principal.has` in `backend/app/security/rbac.py`.
// `admin:*` grants every permission. `read:*` grants `read:` only.

import type { NavGroup } from "@/lib/nav";

export function holds(granted: readonly string[], permission: string): boolean {
  if (granted.includes(permission)) return true;
  const prefix = permission.split(":", 1)[0] ?? permission;
  return granted.includes(`${prefix}:*`) || granted.includes("admin:*");
}

export function hasAnyAccess(me: {
  permissions: readonly string[];
  effective_permissions: readonly string[];
}): boolean {
  return me.permissions.length > 0 || me.effective_permissions.length > 0;
}

/** Drop entries the caller cannot hold, and drop a group that would be empty. */
export function visibleNav(groups: readonly NavGroup[], granted: readonly string[]): NavGroup[] {
  return groups
    .map((group) => ({
      ...group,
      items: group.items.filter((item) => item.permission == null || holds(granted, item.permission)),
    }))
    .filter((group) => group.items.length > 0);
}

/**
 * The development principal has no session to end. A real account is `LOCAL`
 * or `LDAP`. A person who happens to be named `local-dev` still has a source.
 */
export function isDevPrincipal(me: {
  username?: string | null;
  subject: string;
  auth_source?: string | null;
}): boolean {
  if (me.auth_source === "LOCAL" || me.auth_source === "LDAP") return false;
  const name = me.username || me.subject;
  return name === "local-dev" || name === "local-token";
}

/** `/me` carries no display name. The username is the session identity. */
export function signedInName(me: { username?: string | null; subject: string }): string {
  const username = me.username?.trim();
  return username || me.subject;
}
