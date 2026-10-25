"use client";

// The open-alert count, as a text link to the alerts list.
//
// The number is `alerts.open` from the overview. When that read failed or was
// refused there is no count to show, and the control says so: it never shows 0,
// because "0 open alerts" is a clean bill of health the platform has not given.
// The `crit` tone appears only for a count above zero.

import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";

import type { Loaded } from "./TopBar";
import type { SocOverview } from "@/lib/api";
import { formatCount } from "@/lib/format";
import type { Locale } from "@/i18n/locale";

export function AlertBell({ overview }: { overview: Loaded<SocOverview> }) {
  const t = useTranslations("shell.alerts");
  const locale = useLocale() as Locale;
  const count = overview.state === "ok" ? overview.data.alerts.open : null;
  const name =
    count !== null
      ? t("visible", { count })
      : overview.state === "loading"
        ? t("loading")
        : t("unavailable");
  const tone = count !== null && count > 0 ? "crit" : "";

  return (
    <Link
      href="/alerts?status=OPEN"
      className="topbar-bell plain"
      aria-label={name}
      title={count === null && overview.state !== "loading" ? t("countUnavailable") : undefined}
    >
      <span aria-hidden="true">{t("label")}</span>
      <span className={`pill ${tone}`.trim()} aria-hidden="true">
        {count === null ? "—" : formatCount(count, locale)}
      </span>
    </Link>
  );
}
