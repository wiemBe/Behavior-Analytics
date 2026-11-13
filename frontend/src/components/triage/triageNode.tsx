// The triage block of an anomaly page, built on the server (SOC-1b).
//
// The detail page is a server component, so this reads what the interactive panel starts from:
// the triage (and its first activity page), the caller's grants and subject, and the alert that
// names the anomaly. Each read fails on its own: a failed triage read shows the panel's own error
// (never a "New" guessed from nothing), a failed grants read switches the controls off, and an
// alert lookup that could not be completed says nothing about alerts.

import { getLocale, getTranslations } from "next-intl/server";
import type { ReactNode } from "react";

import { api, type AnomalySummary, type Alert, type Triage } from "@/lib/api";
import { enumLabel } from "@/i18n/enumLabels";
import type { Locale } from "@/i18n/locale";
import { asTextTranslator } from "@/i18n/textTranslator";
import { alertTitleText } from "@/lib/alertTitle";
import { ACTIVITY_PAGE, triageFamilyOf } from "@/lib/triage";

import { TriagePanel, TriageUnavailable, type TriageAccess, type TriageAlertRef } from "./TriagePanel";

export const TRIAGE_PERMISSION = "anomaly:triage";

/** How far before detection an alert for this anomaly can have been raised. */
const ALERT_LOOKBACK_MS = 3_600_000;
const ALERT_SCAN = 500;

/**
 * The alert that lists this anomaly in `source_anomaly_ids`. The backend has no filter by anomaly,
 * so this reads the alerts of the anomaly's source raised since it was detected, one bounded page.
 * `undefined` when that page is not the whole answer (more alerts than the page, or the read
 * failed): the panel then says nothing about alerts rather than "none".
 */
async function alertFor(
  anomaly: Pick<AnomalySummary, "id" | "log_source_id" | "detected_at">,
): Promise<Alert | null | undefined> {
  const detected = Date.parse(anomaly.detected_at);
  if (Number.isNaN(detected)) return undefined;
  try {
    const page = await api.alerts({
      limit: ALERT_SCAN,
      since: new Date(detected - ALERT_LOOKBACK_MS).toISOString(),
      ...(anomaly.log_source_id ? { log_source_id: anomaly.log_source_id } : {}),
    });
    const hit = page.items.find((a) => a.source_anomaly_ids.includes(anomaly.id));
    if (hit) return hit;
    return page.total > page.items.length ? undefined : null;
  } catch {
    return undefined;
  }
}

export async function triageNode(anomaly: AnomalySummary, extra?: ReactNode): Promise<ReactNode> {
  const family = triageFamilyOf(anomaly);
  if (family === null) return <TriageUnavailable />;

  const say = asTextTranslator(await getTranslations());
  const locale = (await getLocale()) as Locale;
  const [triage, caller, alert] = await Promise.all([
    api.triage(family, anomaly.id, { limit: ACTIVITY_PAGE }).catch((): Triage | null => null),
    api.caller().catch(() => null),
    alertFor(anomaly),
  ]);

  const access: TriageAccess = caller
    ? caller.effective_permissions.includes(TRIAGE_PERMISSION)
      ? "write"
      : "read"
    : "unknown";

  let ref: TriageAlertRef | null | undefined;
  if (alert === undefined) ref = undefined;
  else if (alert === null) ref = null;
  else {
    ref = {
      id: alert.id,
      title: alertTitleText(alert, locale, say),
      severity: enumLabel(say, "severityPill", alert.severity),
      status: enumLabel(say, "alertStatusPill", alert.status),
    };
  }

  return (
    <TriagePanel
      family={family}
      anomalyId={anomaly.id}
      initial={triage}
      access={access}
      me={caller?.subject ?? null}
      alert={ref}
      extra={extra}
    />
  );
}
