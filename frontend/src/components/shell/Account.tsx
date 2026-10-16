"use client";

// Who is signed in. The top bar shows the username only. The rest — source,
// roles, a password change for a local account, and Sign out — opens from
// that control. The development principal has no session to end.

import { useEffect, useRef } from "react";

import { isDevPrincipal, signedInName } from "@/components/auth/access";
import { SignOutButton } from "@/components/auth/SignOutButton";
import { useSession } from "@/components/auth/session";
import styles from "@/components/auth/auth.module.css";

export function Account() {
  const session = useSession();
  const details = useRef<HTMLDetailsElement>(null);

  useEffect(() => {
    const node = details.current;
    if (!node) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || !node.open) return;
      event.preventDefault();
      node.open = false;
      node.querySelector("summary")?.focus();
    };
    const onPointer = (event: MouseEvent) => {
      if (node.open && !node.contains(event.target as Node)) node.open = false;
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onPointer);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onPointer);
    };
  }, [session.status]);

  if (session.status !== "ready" || session.me == null) return null;
  const me = session.me;
  const name = signedInName(me);
  const source = me.auth_source === "LOCAL" || me.auth_source === "LDAP" ? me.auth_source : null;
  const roles = me.roles ?? [];

  return (
    <details ref={details} className={styles.account}>
      <summary className={styles.accountSummary} aria-label={`Account: ${name}`}>
        {name}
      </summary>
      <div className={styles.accountMenu}>
        <p>Signed in as {name}</p>
        {source ? <p className={styles.accountMeta}>Source: {source}</p> : null}
        <p className={styles.accountMeta}>
          {roles.length > 0 ? `Roles: ${[...roles].sort().join(", ")}` : "Roles: none"}
        </p>
        {source === "LOCAL" ? (
          <a className={styles.accountLink} href="/account">
            Change password
          </a>
        ) : null}
        {isDevPrincipal(me) ? null : <SignOutButton />}
      </div>
    </details>
  );
}
