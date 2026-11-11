"use client";

// "Export CSV": a plain download link beside a list's "Copy link" and on the reports blocks
// (SOC-8). The link carries the view's own filters, so the file is what the page lists; the API
// caps it and names it. The cap is said beside the link, because the file may be cut short.

import { useLocale, useTranslations } from "next-intl";

import { formatCount, type FormatLocale } from "@/lib/format";
import { EXPORT_MAX_ROWS } from "@/lib/reports";

export function ExportCsv({ href, label }: { href: string; label: string }) {
  const t = useTranslations("ui.exportCsv");
  const locale = (useLocale() === "tr" ? "tr" : "en") as FormatLocale;
  return (
    <span className="export-csv">
      <a className="action secondary button-link" href={href} download>
        {label}
      </a>
      <span className="muted export-csv-note">
        {t("limit", { max: formatCount(EXPORT_MAX_ROWS, locale) })}
      </span>
    </span>
  );
}
