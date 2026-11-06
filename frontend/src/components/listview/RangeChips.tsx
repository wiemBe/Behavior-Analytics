"use client";

// The time range of a list: relative chips, Custom, and the absolute window in UTC (SOC-7b).
//
// A chip is a link to the same view with `range=<chip>`, so it stays relative in the URL; the
// active chip links to the view without a range. Custom reveals two UTC inputs. They belong to
// the surrounding filter form through the `form` attribute, so the form's own submit button
// applies them and the chips can sit inside a form without nesting one.
//
// The absolute window is always printed beside the chips: a relative chip alone does not say
// which hours the list covers (guidelines §10).

import Link from "next/link";
import { useId, useState } from "react";
import { useTranslations } from "next-intl";

import { RANGE_CHIPS, stampText, type RangeChip, type RangeWindow } from "@/lib/timeRange";

/** `YYYY-MM-DDTHH:mm` for a `datetime-local` input, from an ISO instant. */
function inputValue(iso: string): string {
  return /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(iso) ? iso.slice(0, 16) : "";
}

export function RangeChips({
  formId,
  chipHrefs,
  active,
  custom,
  since,
  until,
  window: shown,
  emptyText,
}: {
  /** The id of the filter form the Custom inputs submit with. */
  formId: string;
  /** Where each chip goes. The active chip's entry is the view without a range. */
  chipHrefs: Record<RangeChip, string>;
  active: RangeChip | "";
  /** A Custom window is in the URL: the inputs start open. */
  custom: boolean;
  /** The ISO bounds to prefill the inputs with ("" when unset). */
  since: string;
  until: string;
  /** The absolute window the list is showing, or null when it has no time bound. */
  window: RangeWindow | null;
  /** What to print when there is no window (the list's own default). */
  emptyText: string;
}) {
  const t = useTranslations("ui.range");
  const panelId = useId();
  const [open, setOpen] = useState(custom);

  let windowText = emptyText;
  if (shown) {
    const from = shown.from ? stampText(shown.from, true) : "";
    const to = stampText(shown.to);
    windowText = !from
      ? t("windowUntil", { to })
      : shown.toIsNow
        ? t("windowNow", { from, to })
        : t("window", { from, to });
  }

  return (
    <div className="range-chips" role="group" aria-label={t("label")}>
      <span className="range-chip-list" role="group" aria-label={t("chipsLabel")}>
        {RANGE_CHIPS.map(({ key }) => {
          const on = active === key && !open;
          return (
            <Link
              key={key}
              href={chipHrefs[key]}
              className="range-chip"
              aria-current={on ? "true" : undefined}
              aria-label={t(`chipFull.${key}`)}
            >
              {t(`chip.${key}`)}
            </Link>
          );
        })}
        <button
          type="button"
          className="range-chip"
          aria-expanded={open}
          aria-controls={open ? panelId : undefined}
          aria-current={open ? "true" : undefined}
          onClick={() => setOpen((value) => !value)}
        >
          {t("custom")}
        </button>
      </span>
      {/* The chip rides with the form only while no Custom window is being typed, so the
          submit sends `range` or `since`/`until`, never both. */}
      {active && !open && <input type="hidden" name="range" value={active} form={formId} />}
      {open && (
        <span className="range-custom" id={panelId}>
          <label className="filter-field" htmlFor={`${panelId}-from`}>
            {t("from")}
            <input
              id={`${panelId}-from`}
              type="datetime-local"
              name="since"
              form={formId}
              defaultValue={inputValue(since || shown?.from || "")}
            />
          </label>
          <label className="filter-field" htmlFor={`${panelId}-to`}>
            {t("to")}
            <input
              id={`${panelId}-to`}
              type="datetime-local"
              name="until"
              form={formId}
              defaultValue={inputValue(until || (active ? (shown?.to ?? "") : ""))}
            />
          </label>
        </span>
      )}
      <span className="range-window" role="group" aria-label={t("windowLabel")}>
        {windowText}
      </span>
    </div>
  );
}
