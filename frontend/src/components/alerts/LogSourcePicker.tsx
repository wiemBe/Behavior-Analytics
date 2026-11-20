"use client";

// The alert list's "Add a log source" filter (SCALE-2g).
//
// The inventory can hold thousands of sources, so the picker never reads it whole: the analyst
// types, and after a short pause the page asks `GET /log-sources?q=<text>&limit=20` for the best
// matches. Nothing is read before two characters are typed, and at most 20 matches are listed with
// the total the search found, so a broad search says "20 of 1,234 — refine the search" instead of
// pretending the list is complete.
//
// The combobox pattern, as the top bar's search uses it: the input is the combobox, the arrow keys
// move `aria-activedescendant` through the options, Enter chooses the active one and Escape
// closes. A source already chosen is not offered again.

import { useTranslations } from "next-intl";
import { useEffect, useId, useRef, useState } from "react";

import { ApiError, api, type LogSourceSummary } from "@/lib/api";
import { formatCount, type FormatLocale } from "@/lib/format";

export const PICKER_DEBOUNCE_MS = 250;
export const PICKER_MIN_CHARS = 2;
export const PICKER_LIMIT = 20;

type Result =
  | { status: "ok"; items: LogSourceSummary[]; total: number }
  | { status: "forbidden" }
  | { status: "error" };

function isAbort(e: unknown): boolean {
  return e instanceof DOMException && e.name === "AbortError";
}

export function LogSourcePicker({
  chosen,
  locale,
  onChoose,
}: {
  /** Ids already in the filter; they are not offered again. */
  chosen: string[];
  locale: FormatLocale;
  onChoose: (id: string) => void;
}) {
  const t = useTranslations("alerts.list.sourcePicker");
  const uid = useId();
  const listId = `${uid}-list`;
  const rootRef = useRef<HTMLDivElement>(null);
  const [text, setText] = useState("");
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<Result | null>(null);
  const [active, setActive] = useState(-1);
  const [attempt, setAttempt] = useState(0);

  const trimmed = text.trim();

  useEffect(() => {
    if (trimmed.length < PICKER_MIN_CHARS) {
      setResult(null);
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    // The previous search's matches are not shown against this one.
    setResult(null);
    const timer = setTimeout(async () => {
      try {
        const page = await api.logSources({ q: trimmed, limit: PICKER_LIMIT }, controller.signal);
        if (controller.signal.aborted) return;
        setResult({ status: "ok", items: page.items, total: page.total });
      } catch (e) {
        if (isAbort(e) || controller.signal.aborted) return;
        setResult(e instanceof ApiError && e.status === 403 ? { status: "forbidden" } : { status: "error" });
      }
      setActive(-1);
      setLoading(false);
    }, PICKER_DEBOUNCE_MS);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [trimmed, attempt]);

  const options = result?.status === "ok" ? result.items.filter((s) => !chosen.includes(s.id)) : [];

  const choose = (id: string) => {
    setOpen(false);
    setText("");
    setResult(null);
    onChoose(id);
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Escape") {
      setOpen(false);
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      setOpen(true);
      if (options.length) setActive((i) => (i + 1) % options.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (options.length) setActive((i) => (i <= 0 ? options.length - 1 : i - 1));
    } else if (e.key === "Enter" && open && active >= 0 && options[active]) {
      // Choosing a source is not applying the form.
      e.preventDefault();
      choose(options[active].id);
    }
  };

  const shown = open && trimmed.length > 0;
  let status: string | null = null;
  if (trimmed.length < PICKER_MIN_CHARS) status = t("minimum", { count: PICKER_MIN_CHARS });
  else if (loading) status = t("searching");
  else if (result?.status === "forbidden") status = t("forbidden");
  else if (result?.status === "error") status = t("failed");
  else if (result?.status === "ok" && options.length === 0) {
    status = t("noMatch", { query: trimmed });
  } else if (result?.status === "ok") {
    status =
      result.total > result.items.length
        ? t("partial", {
            shown: formatCount(result.items.length, locale),
            total: formatCount(result.total, locale),
          })
        : t("complete", { count: result.total, total: formatCount(result.total, locale) });
  }

  return (
    <div
      className="source-picker"
      ref={rootRef}
      onBlur={(e) => {
        if (!rootRef.current?.contains(e.relatedTarget as Node | null)) setOpen(false);
      }}
    >
      <input
        type="text"
        role="combobox"
        aria-label={t("label")}
        aria-expanded={shown}
        aria-controls={shown ? listId : undefined}
        aria-autocomplete="list"
        aria-activedescendant={shown && active >= 0 ? `${uid}-o${active}` : undefined}
        autoComplete="off"
        spellCheck={false}
        placeholder={t("placeholder")}
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKeyDown}
      />
      {shown && (
        <div className="source-picker-panel">
          {status && (
            <p className="source-picker-status" role="status">
              {status}
              {result?.status === "error" && !loading && (
                <>
                  {" "}
                  <button
                    type="button"
                    className="action"
                    onClick={() => setAttempt((n) => n + 1)}
                  >
                    {t("retry")}
                  </button>
                </>
              )}
            </p>
          )}
          <ul id={listId} role="listbox" aria-label={t("results")}>
            {options.map((s, i) => (
              <li
                key={s.id}
                id={`${uid}-o${i}`}
                role="option"
                aria-selected={i === active}
                className={`source-picker-option${i === active ? " is-active" : ""}`}
                // Keep focus in the input so the panel is not closed by the blur.
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => choose(s.id)}
              >
                {s.name}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
