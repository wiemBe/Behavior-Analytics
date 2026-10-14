"use client";

// The one per-widget range control (UR.5.3). Presets in the title row; a select where three
// buttons would not fit beside the title: always at KPI density, and in a panel narrower than
// the stylesheet's breakpoint, which the panel's own width decides (a container query hides
// the one that does not fit, so the unused one is out of the accessibility tree too). Choosing
// sets the widget's override in the in-memory layout; nothing is saved.

import { useId } from "react";

import { RANGE_TEXT } from "./runtime";
import type { WidgetRange } from "./types";

import "./dashboard.css";

export function RangeControl({
  options,
  value,
  onChange,
  compact = false,
  title,
}: {
  options: readonly WidgetRange[];
  /** The effective range. */
  value: WidgetRange;
  onChange: (range: WidgetRange) => void;
  /** A select in place of the preset buttons. */
  compact?: boolean;
  /** The widget's title, so each control is named per widget. */
  title: string;
}) {
  const selectId = useId();
  const select = (
    <span className={`wr-select-wrap${compact ? "" : " wr-alt"}`}>
      <label className="sr-only" htmlFor={selectId}>
        Window for {title}
      </label>
      <select
        id={selectId}
        className="wr-select"
        value={value}
        onChange={(event) => onChange(event.target.value as WidgetRange)}
      >
        {options.map((option) => (
          <option key={option} value={option}>
            {RANGE_TEXT[option]}
          </option>
        ))}
      </select>
    </span>
  );
  if (compact) return select;
  return (
    <>
      <div className="wr-presets" role="group" aria-label="Window">
        {options.map((option) => (
          <button
            key={option}
            type="button"
            className="wr-preset"
            aria-pressed={option === value}
            onClick={() => onChange(option)}
          >
            {RANGE_TEXT[option]}
          </button>
        ))}
      </div>
      {select}
    </>
  );
}
