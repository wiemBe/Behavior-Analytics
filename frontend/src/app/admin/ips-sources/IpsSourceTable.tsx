import Link from "next/link";
import { useTranslations } from "next-intl";
// The IPS sources table: one row per profile, needing-attention first.

import { TableScroll } from "@/components/ui/TableScroll";
import type { IpsSourceStatus } from "@/lib/api";
import { versionLabel } from "@/lib/behaviorI18n";
import { collectionState, sortByAttention, validationState } from "@/lib/fieldMapping";
import { UtcTime } from "@/components/ui/UtcTime";

import "@/components/ui/controls.css";

export function IpsSourceTable({ sources }: { sources: IpsSourceStatus[] }) {
  const t = useTranslations("ips.table");
  const rows = sortByAttention(sources);
  return (
    <>
      <TableScroll label={t("ipsSources")}>
        <table className="table-dense">
          <caption className="sr-only">{t("caption")}</caption>
          <thead>
            <tr>
              <th scope="col">{t("source")}</th>
              <th scope="col" className="num">
                {t("profile")}
                <span className="cell-sub">{t("version")}</span>
              </th>
              <th scope="col">{t("validation")}</th>
              <th scope="col">{t("enabled")}</th>
              <th scope="col">{t("collection")}</th>
              <th scope="col" className="num">
                {t("lastValidated")}
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((s) => {
              const v = validationState(s);
              const c = collectionState(s);
              return (
                <tr key={s.profile_id}>
                  <td className="cell-name ips-source-cell">
                    <Link href={`/admin/ips-sources/${s.log_source_id}`}>{s.log_source_name}</Link>
                    <span className="cell-sub">
                      {t("qradarId", { id: s.qradar_log_source_id })}
                      {s.log_source_type_name ? ` · ${s.log_source_type_name}` : ""}
                    </span>
                  </td>
                  <td className="num">{versionLabel(s.profile_version)}</td>
                  <td>
                    <span className={`fm-state ips-status ${v.tone}`}>{v.label}</span>
                  </td>
                  <td>{s.enabled ? t("enabledWord") : t("disabledWord")}</td>
                  <td>
                    <span className={`fm-state ${c.collecting ? "ok" : "warn"}`}>{c.label}</span>
                  </td>
                  <td className="num">
                    {s.validated_at ? (
                      <>
                        <UtcTime value={s.validated_at} variant="compact" className="nowrap" />
                        {s.validated_profile_version !== s.profile_version &&
                          s.validated_profile_version !== null && (
                            <span className="cell-sub">
                              {t("ofVersion", { version: s.validated_profile_version })}
                            </span>
                          )}
                      </>
                    ) : (
                      <span className="muted">
                        —<span className="sr-only"> {t("neverValidated")}</span>
                      </span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </TableScroll>
      <ul className="table-notes" aria-label={t("notesLabel")}>
        <li>{t("notes")}</li>
        <li>{t("noteDash")}</li>
      </ul>
    </>
  );
}
