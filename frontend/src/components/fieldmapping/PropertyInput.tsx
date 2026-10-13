"use client";

// A property-name picker over the console's property list.
//
// An ARIA editable combobox with list autocomplete (WAI-ARIA APG). The list is
// a finder, never a gate: any text may be typed, because the catalog may not
// see every property and validation decides. The query is kept apart from the
// value. Opening the picker shows the whole list with the current value marked;
// typing filters it; Escape puts the previous value back.
//
// A native <datalist> cannot group, mark a name as unavailable or count, and
// the catalog serves names that fail the pattern ("not selectable").

import { useEffect, useId, useMemo, useRef, useState } from "react";

import type { CatalogProperty } from "@/lib/api";
import { matchProperty } from "@/lib/fieldMapping";

interface Item {
  key: string;
  name: string;
  hint: string;
  selectable: boolean;
  ranges: [number, number][];
  /** Position in the flat list, which is what the active descendant indexes. */
  index: number;
}

interface Section {
  key: string;
  title: string;
  items: Item[];
}

const GROUPS: { key: string; title: string; applies: boolean | null }[] = [
  { key: "applies", title: "Applies to this source type", applies: true },
  { key: "other", title: "Other source types", applies: false },
  { key: "unknown", title: "Type unknown", applies: null },
];

function build(
  normalized: string[],
  properties: CatalogProperty[],
  query: string,
): { sections: Section[]; flat: Item[]; total: number } {
  const sections: Section[] = [];
  const flat: Item[] = [];

  function add(
    key: string,
    title: string,
    rows: { name: string; hint: string; selectable: boolean }[],
  ) {
    const hits = rows
      .map((r) => ({ r, m: matchProperty(r.name, query) }))
      .filter((h): h is { r: (typeof rows)[number]; m: NonNullable<typeof h.m> } => h.m !== null);
    // Stable: the catalog's own order holds among equal scores.
    if (query.trim() !== "") hits.sort((a, b) => a.m.score - b.m.score);
    if (hits.length === 0) return;
    const items = hits.map(({ r, m }) => {
      const item: Item = {
        key: `${key}:${r.name}`,
        name: r.name,
        hint: r.hint,
        selectable: r.selectable,
        ranges: m.ranges,
        index: flat.length,
      };
      flat.push(item);
      return item;
    });
    sections.push({ key, title, items });
  }

  add(
    "normalized",
    "QRadar normalized fields",
    normalized.map((name) => ({ name, hint: "", selectable: true })),
  );
  for (const g of GROUPS) {
    add(
      g.key,
      g.title,
      properties
        .filter((p) => p.applies_to_source_type === g.applies)
        .map((p) => ({
          name: p.name,
          hint: p.name_allowed
            ? (p.property_type ?? "").toLowerCase()
            : "not selectable: name not allowed",
          selectable: p.name_allowed,
        })),
    );
  }
  return { sections, flat, total: normalized.length + properties.length };
}

function Highlighted({ text, ranges }: { text: string; ranges: [number, number][] }) {
  if (ranges.length === 0) return <>{text}</>;
  const parts: React.ReactNode[] = [];
  let at = 0;
  ranges.forEach(([a, b], i) => {
    if (a > at) parts.push(text.slice(at, a));
    parts.push(
      <mark key={i} className="fm-hit">
        {text.slice(a, b)}
      </mark>,
    );
    at = b;
  });
  if (at < text.length) parts.push(text.slice(at));
  return <>{parts}</>;
}

export function PropertyInput({
  id,
  label,
  value,
  placeholder,
  suggestions,
  normalized = [],
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
  /** The console's properties; grouped by whether they apply to this source's type. */
  suggestions: CatalogProperty[];
  /** QRadar's normalized fields the field may map to; listed first. */
  normalized?: string[];
  describedBy?: string;
  invalid?: boolean;
  disabled?: boolean;
  onChange: (next: string) => void;
}) {
  const listId = useId();
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  // Null until the operator types: the list is then the whole list, not a
  // filter by the current value.
  const [query, setQuery] = useState<string | null>(null);
  const before = useRef(value);
  const listRef = useRef<HTMLDivElement>(null);
  const justFocused = useRef(false);

  const usable = normalized.length + suggestions.length > 0;
  const { sections, flat, total } = useMemo(
    () => build(normalized, suggestions, query ?? ""),
    [normalized, suggestions, query],
  );
  const visible = open && usable;
  const optionId = (i: number) => `${listId}-o${i}`;
  const current = value.trim();
  const currentIndex = flat.findIndex((it) => it.selectable && it.name === current);
  const filtering = (query ?? "").trim() !== "";

  function show(startAtCurrent: boolean) {
    if (!open) {
      before.current = value;
      setQuery(null);
      setActive(startAtCurrent ? currentIndex : -1);
      setOpen(true);
    }
  }

  function close() {
    setOpen(false);
    setActive(-1);
    setQuery(null);
  }

  function choose(it: Item) {
    if (!it.selectable) return;
    onChange(it.name);
    close();
  }

  function step(from: number, dir: 1 | -1): number {
    for (let i = from + dir; i >= 0 && i < flat.length; i += dir) {
      if (flat[i].selectable) return i;
    }
    return from;
  }

  function edge(dir: 1 | -1): number {
    return step(dir === 1 ? -1 : flat.length, dir);
  }

  // Keep the active option inside the list's own scroll area. Not
  // scrollIntoView: that would also scroll the page.
  useEffect(() => {
    const box = listRef.current;
    if (!visible || !box || active < 0) return;
    const el = document.getElementById(optionId(active));
    if (!el) return;
    const head = box.querySelector<HTMLElement>(".fm-group-head")?.offsetHeight ?? 0;
    if (el.offsetTop - head < box.scrollTop) box.scrollTop = el.offsetTop - head;
    else if (el.offsetTop + el.offsetHeight > box.scrollTop + box.clientHeight) {
      box.scrollTop = el.offsetTop + el.offsetHeight - box.clientHeight;
    }
    // optionId is derived from listId, which is stable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible, active]);

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (!usable) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (!open) {
        show(true);
        if (currentIndex < 0) setActive(edge(1));
      } else setActive((i) => step(i, 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (!open) {
        show(true);
        if (currentIndex < 0) setActive(edge(-1));
      } else setActive((i) => (i < 0 ? edge(-1) : step(i, -1)));
    } else if ((e.key === "Home" || e.key === "End") && visible && active >= 0) {
      // Once an option is active the keys move through the list; before that
      // they keep moving the text cursor.
      e.preventDefault();
      setActive(edge(e.key === "Home" ? 1 : -1));
    } else if (e.key === "Enter" && open) {
      e.preventDefault();
      if (active >= 0 && flat[active]) choose(flat[active]);
      else close();
    } else if (e.key === "Escape" && open) {
      e.preventDefault();
      if (before.current !== value) onChange(before.current);
      close();
    }
  }

  const q = (query ?? "").trim();
  const count = filtering ? `${flat.length} of ${total} properties match` : `${total} properties`;

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
          show(false);
          setQuery(e.target.value);
          setActive(-1);
        }}
        onFocus={(e) => {
          // Select the value, so typing replaces it instead of extending it.
          justFocused.current = true;
          e.currentTarget.select();
        }}
        onMouseUp={(e) => {
          if (justFocused.current) e.preventDefault();
          justFocused.current = false;
        }}
        onClick={() => {
          if (usable) show(true);
        }}
        onBlur={() => {
          justFocused.current = false;
          if (open) close();
        }}
        onKeyDown={onKeyDown}
      />
      {visible && (
        // mousedown must not move focus off the input, or blur closes the
        // popup before a choice or a scrollbar drag lands.
        <div className="fm-popup" onMouseDown={(e) => e.preventDefault()}>
          <p className="fm-count" role="status" aria-live="polite">
            <span className="fm-count-n">{count}</span>
            {flat.length === 0 && (
              <span className="fm-count-note">
                {" "}
                for “{q}”. The name you typed can still be used; validation will check it.
              </span>
            )}
          </p>
          <div
            id={listId}
            ref={listRef}
            role="listbox"
            aria-label={`Properties for ${label}`}
            className="fm-list"
            hidden={flat.length === 0}
          >
            {sections.map((s) => (
              <div key={s.key} role="group" aria-labelledby={`${listId}-g-${s.key}`}>
                <div id={`${listId}-g-${s.key}`} role="presentation" className="fm-group-head">
                  {s.title}
                  <span className="fm-group-n">{s.items.length}</span>
                </div>
                {s.items.map((it) => {
                  const isCurrent = it.selectable && it.name === current;
                  return (
                    <div
                      key={it.key}
                      id={optionId(it.index)}
                      role="option"
                      // Whole-name label: the emphasis marks would split it.
                      aria-label={[it.name, isCurrent ? "current" : "", it.hint]
                        .filter(Boolean)
                        .join(", ")}
                      aria-selected={isCurrent}
                      aria-disabled={!it.selectable || undefined}
                      className={`fm-option${it.index === active ? " is-active" : ""}${
                        it.selectable ? "" : " is-disabled"
                      }`}
                      onMouseDown={() => choose(it)}
                    >
                      <span className="fm-option-name">
                        <Highlighted text={it.name} ranges={it.ranges} />
                      </span>
                      {isCurrent && <span className="fm-option-current">current</span>}
                      {it.hint && <span className="fm-option-hint">{it.hint}</span>}
                    </div>
                  );
                })}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
