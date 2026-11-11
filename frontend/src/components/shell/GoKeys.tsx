"use client";

// `g` then a letter goes to a page (SOC-15b): g d Dashboard, g q Queue, g a Anomalies, g l Alerts,
// g o Offenses. The second key has to come within a second and a half; any other key cancels.
//
// It lives once, in the top bar. It listens in the capture phase so that the second key, which is
// also a list key (`a`, `o`), is taken by the sequence and never reaches a list's own handler.
// Keys are left alone in a form control or an editable region, with Ctrl / Alt / Meta held (Ctrl K
// stays the search) and while a dialog is open.

import { useEffect } from "react";

import { dialogOpen, typingTarget } from "@/components/listview/useListKeyboard";
import { useSafeRouter } from "@/components/listview/useSafeRouter";

export const GO_WINDOW_MS = 1500;

export const GO_ROUTES: Record<string, string> = {
  d: "/",
  q: "/queue",
  a: "/anomalies",
  l: "/alerts",
  o: "/offenses",
};

export function GoKeys() {
  const router = useSafeRouter();

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null;
    const cancel = () => {
      if (timer !== null) clearTimeout(timer);
      timer = null;
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.ctrlKey || e.altKey || e.metaKey) return;
      if (typingTarget(e.target) || dialogOpen()) return;
      if (timer !== null) {
        const route = GO_ROUTES[e.key];
        cancel();
        if (route === undefined) return;
        e.preventDefault();
        e.stopPropagation();
        if (router) router.push(route);
        else window.location.assign(route);
        return;
      }
      if (e.key === "g" && !e.repeat) {
        e.preventDefault();
        timer = setTimeout(cancel, GO_WINDOW_MS);
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      cancel();
    };
  }, [router]);

  return null;
}
