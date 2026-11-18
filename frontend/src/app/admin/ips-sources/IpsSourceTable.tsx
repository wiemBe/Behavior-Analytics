"use client";

import Link from "next/link";
import { useState } from "react";
import { useTranslations } from "next-intl";
// The IPS sources table: one row per profile, needing-attention first.

import { RemoveSource } from "@/components/fieldmapping/RemoveSource";
import { TableScroll } from "@/components/ui/TableScroll";
import type { IpsSourceStatus } from "@/lib/api";
import { versionLabel } from "@/lib/behaviorI18n";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";
import { collectionState, sortByAttention, validationState } from "@/lib/fieldMapping";
import { UtcTime } from "@/components/ui/UtcTime";
import { useBackendMessage } from "@/i18n/backendMessage";

import "@/components/ui/controls.css";

export function IpsSourceTable({ sources }: { sources: IpsSourceStatus[] }) {
  const t = useTranslations("ips.table");
  const remove = useTranslations("fieldMapping.remove");
  const [gone, setGone] = useState<ReadonlySet<string>>(new Set());
  const [notes, setNotes] = useState<string[]>([]);
  const i18n = useBehaviorI18n();
  const backendMessage = useBackendMessage();
  const rows = sortByAttention(sources.filter((s) => !gone.has(s.log_source_id)));
  return (
    <>
      {notes.length > 0 && (
        <div className="notice" role="status">
          {notes.map((n) => (
            <p key={n}>{n}</p>
          ))}
        </div>
      )}
      {rows.length > 0 && (
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
              <th scope="col">
                <span className="sr-only">{t("actions")}</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((s) => {
              const v = validationState(s, null, i18n);
              const c = collectionState(
                { ...s, renderBackendMessage: backendMessage },
                i18n,
              );
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
                  <td>
                    <RemoveSource
                      family="IPS"
                      logSourceId={s.log_source_id}
                      name={s.log_source_name}
                      label={t("remove")}
                      onRemoved={(note) => {
                        setGone((prev) => new Set(prev).add(s.log_source_id));
                        setNotes((prev) => [
                          ...prev,
                          remove("removedNote", { name: s.log_source_name }),
                          ...(note ? [note] : []),
                        ]);
                      }}
                    />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </TableScroll>
      )}
      <ul className="table-notes" aria-label={t("notesLabel")}>
        <li>{t("notes")}</li>
        <li>{t("noteDash")}</li>
      </ul>
    </>
  );
}
