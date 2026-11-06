"use client";

// "Copy link": puts the address of the current view on the clipboard (SOC-7b, QW1).
//
// The address comes from the parsed view, not from the browser's location, so a link that was
// opened with a dropped or malformed value is copied in its clean form. The confirmation is one
// short line beside the button, announced politely and cleared after a moment: no toast.

import { useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";

type Outcome = "idle" | "copied" | "failed";

export function CopyLink({ href }: { href: string }) {
  const t = useTranslations("ui.copyLink");
  const [outcome, setOutcome] = useState<Outcome>("idle");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  const copy = async () => {
    let next: Outcome = "copied";
    try {
      await navigator.clipboard.writeText(new URL(href, window.location.origin).href);
    } catch {
      next = "failed";
    }
    setOutcome(next);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setOutcome("idle"), next === "failed" ? 6000 : 2500);
  };

  return (
    <span className="copy-link">
      <button type="button" className="action secondary" onClick={copy} aria-label={t("label")}>
        {t("button")}
      </button>
      <span aria-live="polite" className="muted copy-link-note">
        {outcome === "copied" ? t("copied") : outcome === "failed" ? t("failed") : null}
      </span>
    </span>
  );
}
