"use client";

// The expand dialog (UR.5.3): the same widget, with the same config and range, in a modal
// sized to the viewport. A native `<dialog>` opened with `showModal`: the page behind it is
// inert, `Esc` closes it, and the page returns focus to the control that opened it. The
// dialog's title is the widget's title with its effective range; the range control sits in
// its header so the figures can be re-read without leaving it.

import { useTranslations } from "next-intl";
import { useEffect, useRef, type ReactNode } from "react";

import { useWidgetCopy } from "./copy";
import { RangeControl } from "./RangeControl";
import type { WidgetRange } from "./types";

import "./dashboard.css";

export function ExpandDialog({
  title,
  range,
  rangeOptions,
  onRange,
  onClose,
  children,
}: {
  title: string;
  /** The effective range, shown in the title when the widget takes one. */
  range: WidgetRange;
  rangeOptions: readonly WidgetRange[] | null;
  onRange: (range: WidgetRange) => void;
  onClose: () => void;
  children: ReactNode;
}) {
  const t = useTranslations("widgets.expand");
  const copy = useWidgetCopy();
  const dialogRef = useRef<HTMLDialogElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (!dialog.open) dialog.showModal();
    closeRef.current?.focus();
    return () => {
      if (dialog.open) dialog.close();
    };
  }, []);

  return (
    <dialog
      ref={dialogRef}
      className="dg-expand"
      aria-labelledby="dg-expand-title"
      onCancel={(event) => {
        // Esc: the dialog closes by unmounting, so the page owns the focus return.
        event.preventDefault();
        onClose();
      }}
      onClose={onClose}
    >
      <header className="dg-expand-head">
        <h2 id="dg-expand-title" className="dg-expand-title">
          {title}
          {rangeOptions ? <span className="tw-window">{copy.range(range)}</span> : null}
        </h2>
        {rangeOptions ? (
          <RangeControl options={rangeOptions} value={range} onChange={onRange} title={title} />
        ) : null}
        <button type="button" ref={closeRef} className="action secondary" onClick={onClose}>
          {t("close")}
        </button>
      </header>
      <div className="dg-expand-body">{children}</div>
    </dialog>
  );
}
