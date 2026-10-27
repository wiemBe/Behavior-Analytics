"use client";

// One line on /admin while a console has no history-backfill decision. Shown only to a principal
// who can read the status; a failed or refused read shows nothing (the History backfill page is
// where the failure is reported).

import Link from "next/link";
import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";

import { holds } from "@/components/auth/access";
import { useSession } from "@/components/auth/session";
import { api } from "@/lib/api";

import { READ_PERMISSION } from "./historyFormat";

export function UndecidedNotice() {
  const t = useTranslations("history.undecided");
  const session = useSession();
  const granted = session.status === "ready" && session.me ? session.me.permissions : [];
  const allowed = session.status === "ready" && holds(granted, READ_PERMISSION);
  const [undecided, setUndecided] = useState(false);

  useEffect(() => {
    if (!allowed) {
      setUndecided(false);
      return;
    }
    let cancelled = false;
    api.historyBackfill().then(
      (consoles) => {
        if (!cancelled) setUndecided(consoles.some((c) => c.decision === null));
      },
      () => {
        if (!cancelled) setUndecided(false);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [allowed]);

  if (!undecided) return null;
  return (
    <p className="banner" role="status">
      <span>
        {t.rich("lead", {
          strong: (chunks) => <strong>{chunks}</strong>,
          link: (chunks) => <Link href="/admin/history-backfill">{chunks}</Link>,
        })}
      </span>
    </p>
  );
}
