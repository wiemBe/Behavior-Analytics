"use client";

// A small "?" beside a field label that explains the field in a sentence or two.
//
// The control is a real button. The text shows on pointer hover, on keyboard focus and on
// click or tap (a second click closes it), and closes on Escape and on blur. While the text is
// shown the caller can link it to its field with `aria-describedby` by passing `onShownChange`
// and reading the state back; the text carries `id`, so the field points at it.

import { useCallback, useEffect, useRef, useState } from "react";

import "./controls.css";

const GLYPH = "?";

export function HelpTip({
  id,
  name,
  text,
  align = "start",
  onShownChange,
}: {
  /** Id of the explanation text, which the field points at with aria-describedby. */
  id: string;
  /** The button's accessible name, for example "About Metric". */
  name: string;
  text: string;
  /** Which edge of the "?" the text lines up with: "end" opens leftwards. */
  align?: "start" | "end";
  onShownChange?: (shown: boolean) => void;
}) {
  const [hover, setHover] = useState(false);
  const [focus, setFocus] = useState(false);
  const [pinned, setPinned] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const shown = !dismissed && (hover || focus || pinned);
  const notify = useRef(onShownChange);
  notify.current = onShownChange;

  useEffect(() => {
    notify.current?.(shown);
  }, [shown]);

  const close = useCallback(() => {
    setPinned(false);
    setDismissed(true);
  }, []);

  useEffect(() => {
    if (!shown) return;
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") close();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [shown, close]);

  return (
    <span
      className="help-tip"
      onPointerEnter={() => {
        setDismissed(false);
        setHover(true);
      }}
      onPointerLeave={() => {
        setDismissed(false);
        setHover(false);
      }}
    >
      <button
        type="button"
        className="help-tip-button"
        aria-label={name}
        aria-expanded={shown}
        onFocus={() => {
          setDismissed(false);
          setFocus(true);
        }}
        onBlur={() => {
          setDismissed(false);
          setFocus(false);
          setPinned(false);
        }}
        onClick={() => {
          // A click pins the text open (a tap arrives after hover and focus); the next closes it.
          if (pinned) close();
          else {
            setDismissed(false);
            setPinned(true);
          }
        }}
      >
        {GLYPH}
      </button>
      <span id={id} role="tooltip" className={`help-tip-text ${align}`} hidden={!shown}>
        {text}
      </span>
    </span>
  );
}
