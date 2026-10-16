"use client";

// Where the account menu sends a local account to change its password. A
// directory account has no password here. The frame already replaced this
// page when the account must change its password before anything else.

import { PasswordChange } from "@/components/auth/PasswordChange";
import { useSession } from "@/components/auth/session";
import styles from "@/components/auth/auth.module.css";
import { PageHeader } from "@/components/ui/PageHeader";
import { LoadingState } from "@/components/ui/RouteState";

export function AccountHome() {
  const session = useSession();
  if (session.status !== "ready" || session.me == null) {
    return <LoadingState what="the account" />;
  }
  if (session.me.auth_source === "LOCAL") return <PasswordChange />;
  const description =
    session.me.auth_source === "LDAP"
      ? "An LDAP account changes its password in the directory, not here."
      : "This account has no password to change here.";
  return (
    <div className={styles.column}>
      <PageHeader title="Change password" description={description} />
    </div>
  );
}
