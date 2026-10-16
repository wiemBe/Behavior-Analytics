"use client";

// An account with no permission. There is nothing else on this page: the
// frame does not render navigation the account cannot use.

import { signedInName } from "./access";
import { SignOutButton } from "./SignOutButton";
import styles from "./auth.module.css";
import type { CallerInfo } from "@/lib/api";
import { PageHeader } from "@/components/ui/PageHeader";

export function NoRole({ me }: { me: CallerInfo }) {
  const name = signedInName(me);
  return (
    <div className={styles.column}>
      <PageHeader title="No role assigned" actions={<SignOutButton />} />
      <div className="notice" role="status">
        <p>
          <strong>The account {name} exists and has no role.</strong>
        </p>
        <p>An administrator assigns roles under Administration → Users.</p>
      </div>
    </div>
  );
}
