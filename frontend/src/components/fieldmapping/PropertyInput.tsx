"use client";

// A property-name input with suggestions from the console's property list.
//
// A native <datalist> cannot show a suggestion as unavailable, and the catalog
// serves names that fail the pattern ("not selectable: name not allowed"), so
// this is a small ARIA combobox. The list only suggests: any text may be typed,
// because the catalog may not see every property and validation decides.

import { useId, useMemo, useState } from "react";

import type { CatalogProperty } from "@/lib/api";
import { suggestionHint } from "@/lib/fieldMapping";

const MAX_SUGGESTIONS = 8;

export function PropertyInput({
  id,
  label,
  value,
  placeholder,
  suggestions,
  describedBy,
  invalid,
  disabled,
  onChange,
}: {
  id: string;
  /** Accessible name; the visible label sits outside this component. */
  label: string;
  value: string;
  placeholder?: string;
  /** Already ordered: properties that apply to this source's type first. */
  suggestions: CatalogProperty[];
  describedBy?: string;
  invalid?: boolean;
  disabled?: boolean;
  onChange: (next: string) => void;
}) {
  const listId = useId();
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);

  const shown = useMemo(() => {
    const needle = value.trim().toLowerCase();
    const hits = suggestions.filter((p) => p.name.toLowerCase().includes(needle));
    return hits.slice(0, MAX_SUGGESTIONS);
  }, [suggestions, value]);

  const visible = open && shown.length > 0;
  const optionId = (i: number) => `${listId}-o${i}`;

  function choose(p: CatalogProperty) {
    if (!p.name_allowed) return;
    onChange(p.name);
    setOpen(false);
    setActive(-1);
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setOpen(true);
      setActive((i) => Math.min(i + 1, shown.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter" && visible && active >= 0) {
      e.preventDefault();
      choose(shown[active]);
    } else if (e.key === "Escape" && open) {
      e.preventDefault();
      setOpen(false);
    }
  }

  return (
    <div className="fm-combo">
      <input
        id={id}
        type="text"
        className="field fm-input"
        role="combobox"
        aria-label={label}
        aria-autocomplete="list"
        aria-expanded={visible}
        aria-controls={visible ? listId : undefined}
        aria-activedescendant={visible && active >= 0 ? optionId(active) : undefined}
        aria-describedby={describedBy}
        aria-invalid={invalid || undefined}
        disabled={disabled}
        autoComplete="off"
        spellCheck={false}
        value={value}
        placeholder={placeholder}
        onChange={(e) => {
          onChange(e.target.value);
          setOpen(true);
          setActive(-1);
        }}
        onFocus={() => setOpen(suggestions.length > 0)}
        onBlur={() => setOpen(false)}
        onKeyDown={onKeyDown}
      />
      {visible && (
        <ul id={listId} role="listbox" aria-label={`Properties for ${label}`} className="fm-suggest">
          {shown.map((p, i) => {
            const hint = suggestionHint(p);
            return (
              <li
                key={p.name}
                id={optionId(i)}
                role="option"
                aria-selected={i === active}
                aria-disabled={!p.name_allowed || undefined}
                className={`fm-option${i === active ? " is-active" : ""}${
                  p.name_allowed ? "" : " is-disabled"
                }`}
                // mousedown, not click: the input must keep focus until the
                // choice lands, or blur closes the list first.
                onMouseDown={(e) => {
                  e.preventDefault();
                  choose(p);
                }}
              >
                <span className="fm-option-name">{p.name}</span>
                {hint && <span className="fm-option-hint">{hint}</span>}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
