// The WAF incident's identity: what kind of change, on which endpoint, of which
// source.
//
// The endpoint (`GET /portal/login`) is the page's <h1>; the detector is the
// largest text, as on a Phase A incident. When the source exposes no virtual
// host, the header says so in the one sentence design §27.15 fixes, because an
// endpoint without a host may combine traffic of unrelated applications.

import { useTranslations } from "next-intl";
import Link from "next/link";

import type { WafAnomalyDetail } from "@/lib/api";
import { sevTone } from "@/lib/api";
import { stateTone } from "@/lib/behavior";
import { useEnumLabels } from "@/i18n/enumLabels";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";
import { UtcTime } from "@/components/ui/UtcTime";

import { ARROW_SEP, MID } from "./wafI18n";
import { anomalyTypeLabel, endpointLabel, statePillText } from "./wafLabels";

/** Design §27.15, verbatim. */
export const NO_VIRTUAL_HOST_SENTENCE =
  "This WAF source does not expose a virtual host, so this endpoint may combine traffic for different applications that share the path.";

function Interval({ anomaly }: { anomaly: WafAnomalyDetail }) {
  const t = useTranslations("waf.incident");
  if (!anomaly.anomaly_start) return <>{t("intervalNotRecorded")}</>;
  return (
    <>
      <UtcTime value={anomaly.anomaly_start} />
      {ARROW_SEP}
      {anomaly.anomaly_end ? (
        <UtcTime value={anomaly.anomaly_end} />
      ) : (
        // Still running: an end would report a closed incident that is open.
        t("stillRunning")
      )}
    </>
  );
}

export function WafIncidentHeader({ anomaly }: { anomaly: WafAnomalyDetail }) {
  const t = useTranslations("waf.incident");
  const i18n = useBehaviorI18n();
  const { severityPill } = useEnumLabels();
  const endpoint = anomaly.waf_endpoint;
  const sourceHref = `/behavior/waf/sources/${anomaly.log_source_id}`;
  return (
    <>
      <header className="page-header incident-header">
        <div>
          <p className="incident-back">
            <Link href="/anomalies">{t("back")}</Link>
          </p>

          <div className="row incident-identity">
            <span className="incident-detector">{anomalyTypeLabel(anomaly.anomaly_type, i18n)}</span>
            <span className={`pill pill-strong ${stateTone(anomaly.state)}`}>
              {statePillText(anomaly.state, i18n)}
            </span>
            <span className={`pill pill-quiet ${sevTone(anomaly.severity)}`}>
              {severityPill(anomaly.severity)}
            </span>
            {anomaly.suppressed && <span className="pill pill-quiet warn">{t("suppressed")}</span>}
            <span className="incident-interval">
              <Interval anomaly={anomaly} />
            </span>
          </div>

          <h1 className="page-title incident-source waf-endpoint">{endpointLabel(endpoint)}</h1>
          <p className="waf-subject">
            <span className="muted">{t("source")} </span>
            <Link href={sourceHref}>{anomaly.log_source_name ?? anomaly.log_source_id}</Link>
            {endpoint.virtual_host ? (
              <>
                <span className="muted">
                  {MID}
                  {t("virtualHost")}{" "}
                </span>
                {endpoint.virtual_host}
              </>
            ) : endpoint.virtual_host_available ? (
              <span className="muted">
                {MID}
                {t("noVirtualHostOnEndpoint")}
              </span>
            ) : null}
          </p>
        </div>

        <div className="page-header-actions">
          <Link href={sourceHref} className="action secondary button-link">
            {t("viewSource")}
          </Link>
        </div>
      </header>

      {!endpoint.virtual_host_available && <p className="banner">{t("noVirtualHost")}</p>}
    </>
  );
}
