"use client";

// Who is signed in, and the control that ends a real session. The development
// principal has no session, so it has a name and no Sign out.

import { isDevPrincipal, signedInName } from "@/components/auth/access";
import { SignOutButton } from "@/components/auth/SignOutButton";
import { useSession } from "@/components/auth/session";
import styles from "@/components/auth/auth.module.css";

export function Account() {
  const session = useSession();

  if (session.status !== "ready" || session.me == null) return null;
  const me = session.me;
  const name = signedInName(me);

  return (
    <div className={styles.account}>
      <span className={styles.name}>Signed in as {name}</span>
      {isDevPrincipal(me) ? null : <SignOutButton />}
    </div>
  );
}
