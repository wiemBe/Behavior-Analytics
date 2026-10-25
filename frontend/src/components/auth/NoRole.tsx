"use client";

// An account with no permission. There is nothing else on this page: the
// frame does not render navigation the account cannot use.

import { signedInName } from "./access";
import { SignOutButton } from "./SignOutButton";
import styles from "./auth.module.css";
import type { CallerInfo } from "@/lib/api";
import { PageHeader } from "@/components/ui/PageHeader";
import { useTranslations } from "next-intl";

export function NoRole({ me }: { me: CallerInfo }) {
  const t = useTranslations("auth");
  const name = signedInName(me);
  return (
    <div className={styles.column}>
      <PageHeader title={t("noRoleTitle")} actions={<SignOutButton />} />
      <div className="notice" role="status">
        <p>
          <strong>{t("noRoleAccount", { name })}</strong>
        </p>
        <p>{t("noRoleHelp")}</p>
      </div>
    </div>
  );
}
