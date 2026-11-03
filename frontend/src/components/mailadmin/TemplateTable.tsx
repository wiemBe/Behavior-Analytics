"use client";

// The templates: the default, then one per alert type. A row says whether the template is
// customised, whether the stored one is in use, and what that key sends today. Choosing a row
// opens it in the editor below; a row with unsaved changes says so.

import { useTranslations } from "next-intl";

import { TableScroll } from "@/components/ui/TableScroll";
import { UtcTime } from "@/components/ui/UtcTime";
import type { EmailTemplateList } from "@/lib/api";

import { sourceOf } from "./mail";

export function TemplateTable({
  list,
  selected,
  dirtyKeys,
  onSelect,
}: {
  list: EmailTemplateList;
  selected: string;
  dirtyKeys: readonly string[];
  onSelect: (key: string) => void;
}) {
  const t = useTranslations("mailAdmin.table");
  return (
    <TableScroll label={t("label")}>
      <table className="mail-table">
        <caption className="sr-only">{t("label")}</caption>
        <thead>
          <tr>
            <th scope="col">{t("template")}</th>
            <th scope="col">{t("customised")}</th>
            <th scope="col">{t("enabled")}</th>
            <th scope="col">{t("sends")}</th>
            <th scope="col">{t("updated")}</th>
          </tr>
        </thead>
        <tbody>
          {list.templates.map((row) => {
            const source = sourceOf(row, list);
            const current = row.key === selected;
            return (
              <tr key={row.key} className={current ? "mail-row-current" : undefined}>
                <td className="cell-name">
                  <button
                    type="button"
                    className="mail-pick"
                    aria-current={current ? "true" : undefined}
                    onClick={() => onSelect(row.key)}
                  >
                    {row.label}
                  </button>
                  {dirtyKeys.includes(row.key) ? (
                    <span className="pill warn mail-unsaved">{t("unsaved")}</span>
                  ) : null}
                </td>
                <td>{row.customised ? t("yes") : t("no")}</td>
                <td>{row.customised ? (row.enabled ? t("yes") : t("no")) : <span className="muted">—</span>}</td>
                <td>
                  {source === "own"
                    ? t("source.own")
                    : source === "default"
                      ? t("source.default")
                      : t("source.shipped")}
                </td>
                <td className="num">
                  {row.updated_at ? (
                    <>
                      <UtcTime value={row.updated_at} variant="compact" wrapZone />
                      {row.updated_by ? <span className="cell-sub">{row.updated_by}</span> : null}
                    </>
                  ) : (
                    <span className="muted">—</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </TableScroll>
  );
}
