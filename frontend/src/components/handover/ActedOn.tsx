// What the author did in the window: triage changes and alert acknowledgements and resolutions.
// A comment is shown as "added", never its words: the log keeps no comment text.

import Link from "next/link";
import { useTranslations } from "next-intl";

import { TableScroll } from "@/components/ui/TableScroll";
import { UtcTime } from "@/components/ui/UtcTime";
import { asTextTranslator } from "@/i18n/textTranslator";
import type { HandoverActedOn } from "@/lib/api";

export function ActedOn({ rows }: { rows: HandoverActedOn[] }) {
  const t = useTranslations("handover");
  const say = asTextTranslator(useTranslations());

  const valueText = (row: HandoverActedOn, value: string | null): string => {
    if (value === null || value === "") return t("acted.none");
    // Status and disposition are enum values; an owner is a name and stays as it is.
    const group = row.event === "STATUS" ? "status" : row.event === "DISPOSITION" ? "disposition" : null;
    const key = `triage.${group}.${value}`;
    return group !== null && say.has?.(key) ? say(key) : value;
  };

  function label(row: HandoverActedOn): string {
    const key = `handover.acted.${row.kind}.${row.event}`;
    return say.has?.(key) ? say(key) : t("acted.unknown");
  }

  function change(row: HandoverActedOn): string | null {
    if (row.event === "COMMENT" || row.event === "NOTE") return null;
    if (row.kind === "alert") return null;
    if (row.to_value === null && row.from_value === null) return null;
    return row.from_value === null
      ? t("acted.changeTo", { to: valueText(row, row.to_value) })
      : t("acted.change", {
          from: valueText(row, row.from_value),
          to: valueText(row, row.to_value),
        });
  }

  return (
    <TableScroll label={t("record.table.actedLabel")}>
      <table className="table-dense ho-table" aria-label={t("record.table.actedLabel")}>
        <thead>
          <tr>
            <th scope="col">{t("record.table.action")}</th>
            <th scope="col" className="num">
              {t("record.table.at")}
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const detail = change(row);
            return (
              <tr key={`${row.kind}:${row.id}`}>
                <td className="ho-title">
                  <Link href={row.link}>{label(row)}</Link>
                  {detail && <span className="ho-sub muted">{detail}</span>}
                </td>
                <td className="num ho-waiting">
                  <UtcTime value={row.at} variant="compact" />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </TableScroll>
  );
}
