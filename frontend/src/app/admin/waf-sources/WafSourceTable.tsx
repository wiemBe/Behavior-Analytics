"use client";

import { useTranslations } from "next-intl";
import Link from "next/link";
import { useState } from "react";
// The WAF sources table: one row per profile, needing-attention first.

import { RemoveSource } from "@/components/fieldmapping/RemoveSource";
import { TableScroll } from "@/components/ui/TableScroll";
import type { MappingSchemaOut, WafSourceStatus } from "@/lib/api";
import { collectionState, sortByAttention, validationState } from "@/lib/fieldMapping";
import { UtcTime } from "@/components/ui/UtcTime";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";
import { formatCount } from "@/lib/format";
import { versionLabel } from "@/lib/behaviorI18n";
import { useBackendMessage } from "@/i18n/backendMessage";

import "@/components/ui/controls.css";

export function WafSourceTable({
  sources,
  schema,
}: {
  sources: WafSourceStatus[];
  schema: MappingSchemaOut | null;
}) {
  const t = useTranslations("waf.admin.table");
  const remove = useTranslations("fieldMapping.remove");
  const [gone, setGone] = useState<ReadonlySet<string>>(new Set());
  const [notes, setNotes] = useState<string[]>([]);
  const i18n = useBehaviorI18n();
  const backendMessage = useBackendMessage();
  const { locale } = i18n;
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
      <TableScroll label={t("label")}>
        <table className="table-dense">
          <caption className="sr-only">{t("caption")}</caption>
          <thead>
            <tr>
              <th scope="col">{t("source")}</th>
              <th scope="col">{t("product")}</th>
              <th scope="col" className="num">
                {t("mapping")}
              </th>
              <th scope="col">{t("validation")}</th>
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
              const state = {
                ...s,
                fields: s.validation_fields,
                renderBackendMessage: backendMessage,
              };
              const v = validationState(state, schema, i18n);
              const c = collectionState(state, i18n);
              return (
                <tr key={s.profile_id}>
                  <td className="cell-name">
                    <Link href={`/admin/waf-sources/${s.log_source_id}`}>{s.log_source_name}</Link>
                    <span className="cell-sub">
                      {t("qradarId", { id: formatCount(s.qradar_log_source_id, locale) })}
                    </span>
                  </td>
                  <td className="cell-name">
                    {s.product_label ?? (
                      <span className="muted">
                        —<span className="sr-only"> {t("noProduct")}</span>
                      </span>
                    )}
                  </td>
                  <td className="num">{versionLabel(formatCount(s.profile_version, locale))}</td>
                  <td>
                    <span className={`fm-state ${v.tone}`}>
                      {v.label}
                    </span>
                  </td>
                  <td>
                    <span className={`fm-state ${c.collecting ? "ok" : "warn"}`}>
                      {c.label}
                    </span>
                  </td>
                  <td className="num">
                    {s.validated_at ? (
                      <>
                        <UtcTime value={s.validated_at} variant="compact" className="nowrap" />
                        {s.validated_profile_version !== s.profile_version &&
                          s.validated_profile_version !== null && (
                            <span className="cell-sub">
                              {t("ofVersion", {
                                version: formatCount(s.validated_profile_version, locale),
                              })}
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
                      family="WAF"
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
    </>
  );
}
