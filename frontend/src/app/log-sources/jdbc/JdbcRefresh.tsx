"use client";

// "Refresh JDBC" for administrators: queues the parameter task and says what the API answered.
// A read-only session gets no control at all, not a disabled one.

import Link from "next/link";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { holds } from "@/components/auth/access";
import { useSession } from "@/components/auth/session";
import { ApiError, api } from "@/lib/api";

import "@/components/ui/controls.css";

type Outcome = "idle" | "pending" | "queued" | "already" | "off" | "unavailable" | "forbidden" | "failed";

export function JdbcRefresh() {
  const t = useTranslations("logSources.jdbcPage.refresh");
  const { me } = useSession();
  const [outcome, setOutcome] = useState<Outcome>("idle");
  if (!me || !holds([...me.permissions, ...me.effective_permissions], "admin:*")) return null;

  async function start() {
    setOutcome("pending");
    try {
      const res = await api.refreshJdbc();
      setOutcome(res.already_running ? "already" : "queued");
    } catch (err) {
      const status = err instanceof ApiError ? err.status : 0;
      setOutcome(
        status === 409 ? "off" : status === 503 ? "unavailable" : status === 403 ? "forbidden" : "failed",
      );
    }
  }

  // "Off" is a state of the platform, not a failed request: a neutral line, never an alert.
  const failed = outcome === "unavailable" || outcome === "forbidden" || outcome === "failed";
  return (
    <div className="jdbc-refresh">
      <button type="button" className="action secondary" disabled={outcome === "pending"} onClick={() => void start()}>
        {t("button")}
      </button>
      {outcome !== "idle" && outcome !== "pending" && (
        <p className={failed ? "change-status crit" : outcome === "off" ? "change-status neutral" : "change-status"} role={failed ? "alert" : "status"}>
          {outcome === "off"
            ? t.rich("off", { features: (chunks) => <Link href="/admin/features">{chunks}</Link> })
            : t(outcome)}
        </p>
      )}
    </div>
  );
}
