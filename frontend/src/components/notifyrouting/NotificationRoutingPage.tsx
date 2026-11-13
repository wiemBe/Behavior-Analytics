"use client";

// "Which alerts leave the site, and where do the webhooks go?" Two reads that fail apart: the
// rules, which say which alerts go where, and the destinations, which are where. A rule form needs
// the destinations, so a destination list that did not load keeps the rule form closed.

import { useTranslations } from "next-intl";

import { usePart } from "@/components/mailadmin/usePart";
import { notificationRoutingApi } from "@/lib/api";

import { DestinationsPanel } from "./DestinationsPanel";
import { RulesPanel } from "./RulesPanel";

import "./notifyrouting.css";

export function NotificationRoutingPage() {
  const t = useTranslations("notifyRouting.page");
  const rules = usePart(notificationRoutingApi.rules);
  const destinations = usePart(notificationRoutingApi.destinations);

  if (rules.part.status === "forbidden" || destinations.part.status === "forbidden") {
    return (
      <div className="notice" role="alert">
        <p>
          <strong>{t("forbidden.heading")}</strong>
        </p>
        <p>
          {t.rich("forbidden.body", { perm: (chunks) => <code className="perm">{chunks}</code> })}
        </p>
      </div>
    );
  }

  return (
    <div className="nr-page">
      <RulesPanel
        part={rules.part}
        destinations={destinations.part}
        onChanged={rules.reload}
        onRetry={rules.reload}
        onRetryDestinations={destinations.reload}
      />
      <DestinationsPanel
        part={destinations.part}
        onChanged={() => {
          destinations.reload();
          rules.reload();
        }}
        onRetry={destinations.reload}
      />
    </div>
  );
}
